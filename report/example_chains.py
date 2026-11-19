"""Pick five example chains and extract the XML lines behind each step.

Run after the pipeline (from the project folder):
  .venv/bin/python report/example_chains.py

Writes <output dir>/examples/example_chains.json (read by the report) and
example_chains.md (readable on its own). The examples are chosen by rule:

  1. confirmed      the longest chain confirmed by both sides at every step
  2. missing ID     a chain of 2+ steps whose end activity names its receiver's
                    organisation ID but not its activity ID
  3. implausible    a "reverses own funding link" edge (the GALVmed pattern; GALVmed
                    itself if present), with the true link it reverses
  4. longest        the longest chain
  5. fan-out        the origin with the most chains: one activity, many one-step links
                    (why depth per chain and per organisation path differ)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402
import config  # noqa: E402

MAX_TX_PER_SIDE = 2  # XML snippets shown per side of each link
GALVMED = "GB-COH-05393391"


def xml_of(bulk: Path, act: pd.DataFrame, copy_id: int, tx_seqs: list[int]) -> list[dict]:
    """The XML of the given transactions of one activity copy, with file and line."""
    a = act.loc[copy_id]
    path = bulk / "datasets" / a["publisher_folder"] / f"{a['file']}.xml"
    pos = int(a["position_in_file"])
    out = []
    for k, (_, el) in enumerate(etree.iterparse(str(path), tag="iati-activity", huge_tree=True)):
        if k < pos:
            el.clear()
            continue
        txs = el.findall("transaction")
        for seq in tx_seqs:
            t = txs[seq - 1]
            out.append(
                {
                    "file": f"datasets/{a['publisher_folder']}/{a['file']}.xml",
                    "line": t.sourceline,
                    "xml": etree.tostring(t, encoding="unicode").strip(),
                }
            )
        break
    return out


def step_evidence(bulk: Path, act: pd.DataFrame, links: pd.DataFrame, frm: str, to: str) -> dict:
    """Both sides' transactions behind one link, as XML."""
    lk = links[(links["from_activity"] == frm) & (links["to_activity"] == to)]
    sides = {}
    for side, g in lk.groupby("side"):
        g = g.sort_values("tx_seq").head(MAX_TX_PER_SIDE)
        sides[side] = {
            "n_transactions": int((lk["side"] == side).sum()),
            "xml": xml_of(bulk, act, int(g["copy_id"].iloc[0]), g["tx_seq"].tolist()),
        }
    return sides


def chain_example(
    kind: str,
    why: str,
    ch: pd.Series,
    steps: pd.DataFrame,
    edges: pd.DataFrame,
    bulk: Path,
    act: pd.DataFrame,
    links: pd.DataFrame,
) -> dict:
    st = steps[steps["chain_id"] == ch["chain_id"]].sort_values("step")
    e = edges.set_index(["from_activity", "to_activity"])
    return {
        "kind": kind,
        "why_chosen": why,
        "chain": {k: (None if pd.isna(v) else v) for k, v in ch.items()},
        "steps": [
            {
                "step": int(r.step),
                "from_activity": r.from_activity,
                "to_activity": r.to_activity,
                "from_org": r.from_org_name,
                "to_org": r.to_org_name,
                "evidence": r.evidence,
                "plausibility": e.loc[(r.from_activity, r.to_activity), "plausibility"],
                "sides": step_evidence(bulk, act, links, r.from_activity, r.to_activity),
            }
            for r in st.itertuples()
        ],
    }


def to_markdown(examples: list[dict]) -> str:
    lines = ["# Example chains\n"]
    for k, ex in enumerate(examples, 1):
        c = ex["chain"]
        lines.append(f"## {k}. {ex['kind']}\n\n{ex['why_chosen']}\n")
        if c.get("path_orgs"):
            lines.append(
                f"**Path:** {c['path_orgs']}  \n**Steps:** {c.get('steps')} · "
                f"**weakest evidence:** {c.get('weakest_evidence')} · "
                f"**countries:** {c.get('countries')} · **years:** {c.get('years')}\n"
            )
        for s in ex["steps"]:
            lines.append(
                f"### Step {s['step']}: {s['from_org']} → {s['to_org']} "
                f"({s['evidence']}; {s['plausibility']})\n"
            )
            lines.append(f"`{s['from_activity']}` → `{s['to_activity']}`\n")
            for side, d in s["sides"].items():
                who = "funder" if side == "provider" else "receiver"
                lines.append(f"Reported by the {who} ({d['n_transactions']} transactions); first shown:\n")
                for x in d["xml"]:
                    lines.append(f"`{x['file']}`, line {x['line']}:\n\n```xml\n{x['xml']}\n```\n")
        if ex.get("end"):
            lines.append(f"### Where the chain stops: {ex['end']['reason']}\n")
            for x in ex["end"]["xml"]:
                lines.append(f"`{x['file']}`, line {x['line']}:\n\n```xml\n{x['xml']}\n```\n")
        if ex.get("note"):
            lines.append(ex["note"] + "\n")
    return "\n".join(lines)


def main() -> None:
    out = config.output_dir()
    bulk = config.BULK_DIR
    act = pd.read_parquet(
        out / "activities_clean.parquet",
        columns=["copy_id", "iati_identifier", "publisher_folder", "file", "position_in_file"],
    )
    copy_of = act.set_index("iati_identifier")["copy_id"]
    act = act.set_index("copy_id")
    links = pd.read_parquet(out / "link_transactions.parquet")
    edges = pd.read_parquet(out / "activity_edges.parquet")
    chains = common.read_csv(out / "chains.csv")
    steps = common.read_csv(out / "chain_steps.csv")
    examples = []
    skipped = []  # kinds with no matching chain in this run (e.g. on the small test files)

    # 1. confirmed at every step (longest; most transactions as tie-break)
    c = chains[chains["weakest_evidence"] == "confirmed"].sort_values(
        ["steps", "chain_id"], ascending=[False, True]
    )
    if len(c) == 0:
        skipped.append("Confirmed at every step")
    else:
        examples.append(
            chain_example(
                "Confirmed at every step",
                "The longest chain whose every link is reported by both the funder and the receiver.",
                c.iloc[0],
                steps,
                edges,
                bulk,
                act,
                links,
            )
        )

    # 2. broken by a missing activity ID
    c = chains[(chains["end_reason"] == "receiver organisation ID only") & (chains["steps"] >= 2)]
    if len(c) == 0:
        skipped.append("Broken by a missing activity ID")
    else:
        c = c.sort_values(["n_confirmed_steps", "steps"], ascending=False).iloc[0]
        ex = chain_example(
            "Broken by a missing activity ID",
            "The chain stops because the last activity pays an "
            "organisation identified by its ID, but gives no activity ID, so the money cannot be "
            "followed further.",
            c,
            steps,
            edges,
            bulk,
            act,
            links,
        )
        tx = pd.read_parquet(
            out / "transactions_clean.parquet",
            columns=[
                "copy_id",
                "tx_seq",
                "iati_identifier",
                "transaction_type",
                "receiver_org_ref",
                "receiver_activity_id",
                "internal",
            ],
            filters=[("iati_identifier", "==", c["end_activity"])],
        )
        t = (
            tx[
                tx["transaction_type"].isin(config.link_provider_types())
                & tx["receiver_org_ref"].notna()
                & tx["receiver_activity_id"].isna()
                & ~tx["internal"]
            ]
            .sort_values("tx_seq")
            .head(2)
        )
        ex["end"] = {
            "reason": c["end_reason"],
            "xml": xml_of(bulk, act, int(copy_of[c["end_activity"]]), t["tx_seq"].tolist()),
        }
        examples.append(ex)

    # 3. implausible link (GALVmed pattern)
    imp = edges[edges["plausibility"] == "reverses own funding link"]
    if len(imp) == 0:
        skipped.append("Implausible link")
    else:
        galv = imp[imp["from_activity"].str.startswith(GALVMED)]
        e = (galv if len(galv) else imp.sort_values("n_transactions", ascending=False)).iloc[0]
        rev = edges[
            (edges["from_activity"] == e["to_activity"]) & (edges["to_activity"] == e["from_activity"])
        ].iloc[0]
        examples.append(
            {
                "kind": "Implausible link",
                "why_chosen": "A publisher puts its funder's activity ID in receiver-activity-id on payments "
                "to others. "
                "Read literally, money flows back to the funder and the two activities form a loop. The "
                "rule 'reverses own funding link' flags the second link; the first (true) link is kept.",
                "chain": {
                    "path_orgs": f"{rev['from_org_name']} > {rev['to_org_name']} > {e['to_org_name']}",
                    "steps": 2,
                    "weakest_evidence": "implausible",
                    "countries": None,
                    "years": None,
                },
                "steps": [
                    {
                        "step": k,
                        "from_activity": r["from_activity"],
                        "to_activity": r["to_activity"],
                        "from_org": r["from_org_name"],
                        "to_org": r["to_org_name"],
                        "evidence": r["evidence"],
                        "plausibility": r["plausibility"],
                        "sides": step_evidence(bulk, act, links, r["from_activity"], r["to_activity"]),
                    }
                    for k, r in [(1, rev), (2, e)]
                ],
            }
        )

    # 4. longest chain
    if len(chains) == 0:
        skipped.append("Longest chain")
    else:
        c = chains.sort_values(
            ["steps", "n_confirmed_steps", "chain_id"], ascending=[False, False, True]
        ).iloc[0]
        examples.append(
            chain_example(
                "Longest chain", "The longest chain in the network.", c, steps, edges, bulk, act, links
            )
        )

    # 5. fan-out origin
    if len(chains) == 0:
        skipped.append("One origin, many chains")
    else:
        o = common.read_csv(out / "chain_origins.csv").sort_values("n_chains", ascending=False).iloc[0]
        c = chains[chains["origin_activity"] == o["origin_activity"]].sort_values("chain_id").iloc[0]
        ex = chain_example(
            "One origin, many chains",
            f"This origin activity starts {int(o['n_chains']):,} chains. "
            "Each counts as a separate chain, but all follow few organisation paths. This is why depth "
            "per chain and per organisation path differ.",
            c,
            steps,
            edges,
            bulk,
            act,
            links,
        )
        oc = chains[chains["origin_activity"] == o["origin_activity"]]
        ex["note"] = (
            f"Chains from this origin: {len(oc):,}; distinct organisation paths among them: "
            f"{oc['path_org_refs'].nunique():,}; distinct end activities: {oc['end_activity'].nunique():,}."
        )
        examples.append(ex)

    d = out / "examples"
    d.mkdir(exist_ok=True)
    # NumPy numbers (from pandas) are written as JSON numbers, not as text
    as_json = lambda o: o.item() if hasattr(o, "item") else str(o)  # noqa: E731
    (d / "example_chains.json").write_text(json.dumps(examples, indent=1, default=as_json))
    (d / "example_chains.md").write_text(to_markdown(examples))
    if skipped:
        print("No example of this kind in these outputs, left out: " + "; ".join(skipped))
    print(f"Written to {d}")


if __name__ == "__main__":
    main()
