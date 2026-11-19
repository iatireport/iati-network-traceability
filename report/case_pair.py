"""Case pair for the working paper (CC-09): a highly traceable chain and a broken chain
in the same sector and the same recipient country. Rerun each year after the pipeline:

    .venv/bin/python report/case_pair.py            (from the project folder)

Selection rule (from the first draft, made explicit here)
---------------------------------------------------------
Traceable chain, all of:
  T1  3 or more steps (chains.csv);
  T2  completeness score at or near 1: at least NEAR_ONE (no chain of 3+ steps ending at a
      local organisation reaches 1 in the bulk copy of 30 October 2026; best 0.833);
  T3  ends at a national or local implementer: the end organisation's IATI organisation type
      is one of config.LOCAL_ORG_TYPES (11 local government, 22 national NGO, 23 regional NGO,
      24 partner country based NGO, 72 private sector in the aid recipient country);
  T4  its end activity names exactly one recipient country (the case country);
  T5  its origin activity has an activity-level DAC sector (the case sector, 3 digits).
  Ranked: origin funder the Netherlands MFA (XM-DAC-7) or UK FCDO (GB-GOV-1) first; then
  higher completeness; then more steps; then chain_id.
Broken chain, all of:
  B1  its origin activity has the same 3-digit DAC sector;
  B2  the case country is among the chain's countries (chains.csv);
  B3  it stops where the receiver is a name only, or where an activity identifier is given
      that does not resolve to a published activity (end_reason in BROKEN_REASONS);
  B4  not on the same organisation path as the traceable chain.
  Ranked: origin value in the same currency as the traceable chain's origin first, then the
  largest origin value (its commitments, or disbursements if it reports none, summed in its
  own main currency; values are never converted).
The first traceable chain (in rank order) that has a broken partner is the pair. If none
has, the output says which condition fails, with counts.

Sector: the activity-level <sector> element of the origin activity (vocabulary 1, DAC
5-digit, cut to 3 digits; or vocabulary 2, DAC 3-digit), the one with the largest
percentage. The pipeline does not read sectors, so this script reads them from the bulk
copy for the chain origins only (the same files stage 1 read).

Output: report/figures/wp/case_pair.json (both chains, their steps, scores, sector,
country, origin value; the rule and its parameters; the candidates tried).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
from lxml import etree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import common  # noqa: E402
import config  # noqa: E402

NEAR_ONE = 0.75
PREFERRED_ORIGINS = ["XM-DAC-7", "GB-GOV-1"]  # Netherlands MFA, UK FCDO
BROKEN_REASONS = ["receiver name only", "activity ID given but not published"]
MIN_STEPS = 3
OUT = HERE / "figures" / "wp" / "case_pair.json"


def load_stage(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main_sector(sec: pd.DataFrame) -> pd.Series:
    """One 3-digit DAC sector per activity (iati_identifier): from vocabulary 1 (5-digit, cut
    to 3) or 2 (3-digit); the sector with the largest percentage (first if tied or missing)."""
    s = sec[sec["vocabulary"].fillna("1").isin(["1", "2"])].copy()
    s["sector3"] = s["code"].astype("string").str.strip().str[:3]
    s = s[s["sector3"].str.fullmatch(r"\d{3}", na=False)]
    s["pct"] = pd.to_numeric(s["percentage"], errors="coerce").fillna(0)
    s = s.sort_values(["iati_identifier", "pct", "seq"], ascending=[True, False, True], kind="stable")
    return s.drop_duplicates("iati_identifier").set_index("iati_identifier")["sector3"]


def read_sectors(paths: dict[int, str], wanted: set[int]) -> pd.DataFrame:
    """Activity-level <sector> elements of the wanted activity copies (copy_id = file_idx x
    1,000,000 + position in file, as in stage 1)."""
    rows = []
    for file_idx, path in paths.items():
        pos = 0
        for _, act in etree.iterparse(path, tag="iati-activity", huge_tree=True, resolve_entities=False, no_network=True):
            cid = file_idx * 1_000_000 + pos
            if cid in wanted:
                ident = (act.findtext("iati-identifier") or "").strip()
                for k, s in enumerate(act.iterchildren("sector")):
                    rows.append({"copy_id": cid, "iati_identifier": ident, "seq": k, "vocabulary": s.get("vocabulary"),
                                 "code": s.get("code"), "percentage": s.get("percentage")})
            pos += 1
            act.clear(keep_tail=True)
            while act.getprevious() is not None:
                del act.getparent()[0]
    return pd.DataFrame(rows, columns=["copy_id", "iati_identifier", "seq", "vocabulary", "code", "percentage"])


def origin_values(tx: pd.DataFrame) -> pd.DataFrame:
    """Per activity: commitments (type 2) summed in its main currency, or disbursements (3) if
    it reports no commitments. Exact duplicates left out. Columns value, currency, value_type."""
    t = tx[~tx["exact_duplicate"] & tx["transaction_type"].astype("string").isin(["2", "3"]) & tx["value"].notna()]
    out = []
    for ident, g in t.groupby("iati_identifier"):
        kind = "2" if (g["transaction_type"].astype("string") == "2").any() else "3"
        g = g[g["transaction_type"].astype("string") == kind]
        cur = g["currency"].mode().iloc[0] if g["currency"].notna().any() else None
        v = g.loc[g["currency"] == cur, "value"].sum() if cur else g["value"].sum()
        out.append({"iati_identifier": ident, "origin_value": float(v), "currency": cur,
                    "value_type": "commitments" if kind == "2" else "disbursements"})
    return pd.DataFrame(out, columns=["iati_identifier", "origin_value", "currency", "value_type"]).set_index("iati_identifier")


def select_pair(ch: pd.DataFrame, local_labels: set[str]) -> dict:
    """Apply the rule to chains with columns chain_id, steps, completeness, path_org_refs,
    path_org_types, countries, end_reason, origin_sector, end_country, origin_value, currency.
    Returns {"traceable": row, "broken": row, "tried": [...]} or {"failed": counts}."""
    ch = ch.assign(origin=ch["path_org_refs"].str.split(" > ").str[0],
                   end_type=ch["path_org_types"].str.split(" > ").str[-1].str.strip())
    t1 = ch["steps"] >= MIN_STEPS
    t2 = t1 & (ch["completeness"] >= NEAR_ONE)
    t3 = t2 & ch["end_type"].isin(local_labels)
    t4 = t3 & ch["end_country"].notna()
    t5 = t4 & ch["origin_sector"].notna()
    cand = ch[t5].assign(pref=lambda d: ~d["origin"].isin(PREFERRED_ORIGINS)).sort_values(
        ["pref", "completeness", "steps", "chain_id"], ascending=[True, False, False, True])
    tried = []
    for _, t in cand.iterrows():
        b = ch[(ch["origin_sector"] == t["origin_sector"])
               & ch["countries"].fillna("").str.split(";").apply(lambda cs: t["end_country"] in cs)
               & ch["end_reason"].isin(BROKEN_REASONS)
               & (ch["path_org_refs"] != t["path_org_refs"])]
        tried.append({"chain_id": int(t["chain_id"]), "sector": t["origin_sector"], "country": t["end_country"], "broken_candidates": len(b)})
        if len(b):
            b = b.assign(same_cur=(b["currency"] == t["currency"])).sort_values(
                ["same_cur", "origin_value", "chain_id"], ascending=[False, False, True])
            return {"traceable": t, "broken": b.iloc[0], "tried": tried}
    return {"failed": {"T1 3+ steps": int(t1.sum()), "T2 completeness >= NEAR_ONE": int(t2.sum()), "T3 local end": int(t3.sum()),
                       "T4 one end country": int(t4.sum()), "T5 origin sector known": int(t5.sum()),
                       "with a broken partner (B1-B4)": 0}, "tried": tried}


def main() -> None:
    out_dir = config.output_dir()
    ch = common.read_csv(out_dir / "chains.csv")
    st = common.read_csv(out_dir / "chain_steps.csv")
    act = pd.read_parquet(out_dir / "activities_clean.parquet", columns=["copy_id", "iati_identifier", "recipient_countries"])
    s6 = load_stage("06_spatial")
    local = s6.local_type_labels()
    # end country: the end activity's activity-level recipient countries, if exactly one
    rc = common.valid_countries(act.set_index("iati_identifier")["recipient_countries"])
    one = rc[rc.notna() & ~rc.fillna("").str.contains(";")]
    ch["end_country"] = ch["end_activity"].map(one)
    # sectors of the origin activities, read from the bulk copy
    origins = act[act["iati_identifier"].isin(ch["origin_activity"])]
    files = common.read_csv(out_dir / "files.csv", usecols=["file_idx", "publisher_folder", "file", "kind", "status"])
    files = files[(files["kind"] == "activity") & (files["status"] == "ok") & files["file_idx"].isin(origins["copy_id"] // 1_000_000)]
    paths = {int(i): str(config.BULK_DIR / "datasets" / f / f"{n}.xml") for i, f, n in zip(files["file_idx"], files["publisher_folder"], files["file"])}
    sec = read_sectors(paths, set(origins["copy_id"]))
    ch["origin_sector"] = ch["origin_activity"].map(main_sector(sec))
    # origin values (own currency)
    tx = pd.read_parquet(out_dir / "transactions_clean.parquet", columns=["iati_identifier", "transaction_type", "value", "currency", "exact_duplicate"],
                         filters=[("iati_identifier", "in", list(ch["origin_activity"].unique()))])
    ov = origin_values(tx)
    ch = ch.join(ov, on="origin_activity")
    res = select_pair(ch, local)
    rule = {"NEAR_ONE": NEAR_ONE, "MIN_STEPS": MIN_STEPS, "PREFERRED_ORIGINS": PREFERRED_ORIGINS, "BROKEN_REASONS": BROKEN_REASONS,
            "origins_with_sector": int(ch.drop_duplicates("origin_activity")["origin_sector"].notna().sum()),
            "origins": int(ch["origin_activity"].nunique())}

    def chain_dict(r: pd.Series) -> dict:
        s = st[st["chain_id"] == r["chain_id"]].sort_values("step")
        keep = ["chain_id", "steps", "completeness", "n_confirmed_steps", "path_orgs", "path_org_refs", "path_org_types", "origin_activity",
                "end_activity", "end_reason", "years", "origin_sector", "end_country", "origin_value", "currency", "value_type"]
        d = {k: (r[k].item() if hasattr(r[k], "item") else r[k]) for k in keep}
        d = {k: (None if isinstance(v, float) and v != v else v) for k, v in d.items()}  # NaN -> null (valid JSON)
        d["steps_detail"] = s[["step", "from_activity", "to_activity", "from_org", "from_org_name", "to_org", "to_org_name", "evidence",
                               "receiver_ref_well_formed", "funder_names_activity", "receiver_confirms", "step_points"]].to_dict("records")
        return d

    doc = {"rule": __doc__, "parameters": rule, "tried": res["tried"]}
    if "failed" in res:
        doc["result"] = "no pair meets the rule"
        doc["condition_counts"] = res["failed"]
    else:
        doc["result"] = "pair found"
        doc["sector"] = res["traceable"]["origin_sector"]
        doc["country"] = res["traceable"]["end_country"]
        doc["traceable"] = chain_dict(res["traceable"])
        doc["broken"] = chain_dict(res["broken"])
    OUT.write_text(json.dumps(doc, indent=1, default=str, allow_nan=False))
    print(doc["result"], {k: doc[k] for k in ("sector", "country") if k in doc})


if __name__ == "__main__":
    main()
