"""The publishers behind counts the working paper gives without names (v3 docx task):
indicator D2, exact duplicate transactions (D1) and internal transactions. Rerun after the pipeline:

    .venv/bin/python report/top_publishers.py            (from the project folder)

Indicator D2 (stage 5) is the share of traced transactions held by the ten publishers with
the most of them. Stage 5 writes only the share. This script names the ten, for all years and
for each year subset, so the paper can list them instead of saying "ten publishers".
It reuses stage 5's flags (05_indicators.flag_transactions and in_scope), so the counts
are the same as in indicators.csv; the script stops if the sums differ.

Outputs (report/figures/wp/):
  d2_top_publishers.csv  scope, rank, reporting_org_ref, org_name, publisher_group, traced, traced_all, percent
                         (percent: share of all traced transactions in the scope, one decimal; rank 1 to 10)
  top_publishers_d1_internal.csv  measure (exact_duplicates, internal), rank, reporting_org_ref, org_name,
                         publisher_group, n, total, percent (the five publishers with most, all years; exact
                         both counted among all transactions, as D1 and the stage 2 summary count them)
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import common  # noqa: E402
import config  # noqa: E402

TOP_N = 10
OUT = HERE / "figures" / "wp" / "d2_top_publishers.csv"
OUT_OTHER = HERE / "figures" / "wp" / "top_publishers_d1_internal.csv"


def load_stage(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def top_publishers(t: pd.DataFrame, names: pd.Series, n: int = TOP_N) -> pd.DataFrame:
    """t: a scope's transactions without exact duplicates, with columns traced,
    reporting_org_ref and publisher_group. Returns the n publishers with most traced
    transactions (ties broken by identifier), as stage 5 counts them for D2."""
    tr = t[t["traced"]]
    total = len(tr)
    g = (
        tr.groupby("reporting_org_ref")
        .agg(traced=("traced", "size"), publisher_group=("publisher_group", "first"))
        .reset_index()
        .sort_values(["traced", "reporting_org_ref"], ascending=[False, True])
        .head(n)
    )
    g["rank"] = range(1, len(g) + 1)
    g["org_name"] = g["reporting_org_ref"].map(names)
    g["traced_all"] = total
    g["percent"] = (100 * g["traced"] / total).round(1) if total else None
    return g[["rank", "reporting_org_ref", "org_name", "publisher_group", "traced", "traced_all", "percent"]]


def top_by_flag(t: pd.DataFrame, flag: str, names: pd.Series, n: int = 5) -> pd.DataFrame:
    """The n publishers with most transactions where `flag` is true (ties by identifier)."""
    g = t[t[flag]].groupby("reporting_org_ref").agg(n=(flag, "size"), publisher_group=("publisher_group", "first")).reset_index()
    g = g.sort_values(["n", "reporting_org_ref"], ascending=[False, True]).head(n)
    g["rank"] = range(1, len(g) + 1)
    g["org_name"] = g["reporting_org_ref"].map(names)
    g["total"] = int(t[flag].sum())
    g["percent"] = (100 * g["n"] / g["total"]).round(1)
    return g[["rank", "reporting_org_ref", "org_name", "publisher_group", "n", "total", "percent"]]


def main() -> None:
    out_dir = ROOT / "outputs" / "bulk_2026-10-30"
    s5 = load_stage("05_indicators")
    edges = pd.read_parquet(out_dir / "activity_edges.parquet")
    act = pd.read_parquet(out_dir / "activities_clean.parquet", columns=["copy_id", "iati_identifier", "recipient_countries", "start_date", "end_date"])
    org = pd.read_parquet(out_dir / "organisations.parquet")
    tx = pd.read_parquet(out_dir / "transactions_clean.parquet", columns=s5.TX_COLS)
    tx = s5.flag_transactions(tx, edges, set(org.loc[org["is_publisher"], "org_ref"]), set(org["org_ref"]))
    tx = s5.add_countries(tx, act)
    names = org.drop_duplicates("org_ref").set_index("org_ref")["org_name"]
    ind = common.read_csv(out_dir / "indicators.csv")
    d2 = ind[(ind["indicator"] == "D2") & (ind["group"] == "All publishers")].set_index("scope")

    parts = []
    for country, year in [(None, None)] + [tuple(s) for s in config.SUBSETS]:
        if country is not None:
            continue  # the paper reports D2 for all countries only
        label = "global" if year is None else f"all_{year}"
        sub = tx[s5.in_scope(tx, act, country, year)]
        t = sub[~sub["exact_duplicate"]]
        top = top_publishers(t, names)
        if label in d2.index:
            if int(top["traced"].sum()) != int(d2.loc[label, "numerator"]) or int(top["traced_all"].iloc[0]) != int(d2.loc[label, "denominator"]):
                raise SystemExit(f"D2 counts differ from indicators.csv for scope {label}")
        parts.append(top.assign(scope=label))
    res = pd.concat(parts)[["scope", "rank", "reporting_org_ref", "org_name", "publisher_group", "traced", "traced_all", "percent"]]
    res.to_csv(OUT, index=False)
    print(f"Wrote {OUT} ({len(res)} rows); D2 sums match indicators.csv")

    # Exact duplicates (D1, among all transactions) and internal transactions (among the rest)
    dup = top_by_flag(tx, "exact_duplicate", names).assign(measure="exact_duplicates")
    d1 = ind[(ind["indicator"] == "D1") & (ind["group"] == "All publishers") & (ind["scope"] == "global")]
    if int(dup["total"].iloc[0]) != int(d1["numerator"].iloc[0]):
        raise SystemExit("D1 count differs from indicators.csv")
    internal = top_by_flag(tx, "internal", names).assign(measure="internal")
    s2 = json.loads((out_dir / "summaries" / "02_clean.json").read_text())["counterpart"]["internal"]
    if int(internal["total"].iloc[0]) != int(s2):
        raise SystemExit("internal count differs from summaries/02_clean.json")
    other = pd.concat([dup, internal])[["measure", "rank", "reporting_org_ref", "org_name", "publisher_group", "n", "total", "percent"]]
    other.to_csv(OUT_OTHER, index=False)
    print(f"Wrote {OUT_OTHER}; D1 and internal totals match indicators.csv and the stage 2 summary")


if __name__ == "__main__":
    main()
