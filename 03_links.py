"""Stage 3: edge tables. Who is linked to whom, and on what evidence.

Input   <output dir>/transactions_clean.parquet, activities_clean.parquet,
        organisations.parquet, publishers.parquet   (stages 1-2)
Output  <output dir>/
          link_transactions.parquet   one row per transaction that names a published
                                      counterpart activity (the evidence behind each edge)
          activity_edges.parquet/.csv one row per (from_activity, to_activity)
          activity_edge_values.parquet  value sums per edge, side and currency (long)
          org_edges.parquet/.csv      one row per (from_org, to_org)
          org_edge_values.parquet     value sums per organisation edge, side and currency

An activity link can be written by either side (guide 1.3):
  provider side  the funder's outgoing transaction (config.link_provider_types())
                 names the receiver's activity in receiver-activity-id
  receiver side  the receiver's incoming transaction (config.link_receiver_types())
                 names the funder's activity in provider-activity-id
Only IDs that match a published iati-identifier make an edge. Self-references and
exact duplicate transactions are left out. Values are summed per currency only.

Rules (named functions below): link_transactions(), check_named_org(),
is_plausible_link(), aggregate_activity_edges(), aggregate_org_edges().
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

import common
import config

STAGE = "03_links"
KEY = ["from_activity", "to_activity"]
OKEY = ["from_org", "to_org"]

TX_COLS = [
    "copy_id",
    "tx_seq",
    "iati_identifier",
    "reporting_org_ref",
    "transaction_type",
    "transaction_year",
    "value",
    "currency",
    "provider_org_ref",
    "provider_org_name",
    "provider_activity_id",
    "receiver_org_ref",
    "receiver_org_name",
    "receiver_activity_id",
    "exact_duplicate",
    "internal",
]


# =============================================================================
# Activity links
# =============================================================================
def link_transactions(tx: pd.DataFrame, published: pd.Index) -> pd.DataFrame:
    """One row per transaction that links two different published activities.

    Columns added: side ("provider"/"receiver"), from_activity, to_activity,
    linked_activity (the other activity), named_org_ref and named_org_name (the
    organisation the transaction names on the linked side)."""
    own = tx["iati_identifier"]
    rows = []
    for side, types, id_col, org in [
        ("provider", config.link_provider_types(), "receiver_activity_id", "receiver"),
        ("receiver", config.link_receiver_types(), "provider_activity_id", "provider"),
    ]:
        other = tx[id_col]
        hit = tx["transaction_type"].isin(types) & other.isin(published) & (other != own).fillna(False)
        x = tx[hit.to_numpy()].copy()
        x["side"] = side
        x["linked_activity"] = x[id_col]
        x["from_activity"] = x["iati_identifier"] if side == "provider" else x[id_col]
        x["to_activity"] = x[id_col] if side == "provider" else x["iati_identifier"]
        x["named_org_ref"] = x[f"{org}_org_ref"]
        x["named_org_name"] = x[f"{org}_org_name"]
        rows.append(x)
    keep = [
        "copy_id",
        "tx_seq",
        "iati_identifier",
        "side",
        "from_activity",
        "to_activity",
        "linked_activity",
        "named_org_ref",
        "named_org_name",
        "transaction_type",
        "transaction_year",
        "value",
        "currency",
    ]
    return pd.concat(rows, ignore_index=True)[keep]


def publisher_aliases(act: pd.DataFrame, publishers: pd.DataFrame) -> pd.DataFrame:
    """All identifiers a publisher folder is known by: the registry identifiers and
    every reporting-org ref used in its files. Columns: publisher_folder, alias."""
    reg = pd.concat(
        [
            publishers[["publisher_folder", c]].rename(columns={c: "alias"})
            for c in ["organisation_identifier", "iati_identifier"]
        ]
    )
    used = act[["publisher_folder", "reporting_org_ref"]].rename(columns={"reporting_org_ref": "alias"})
    a = pd.concat([reg, used])
    a["alias"] = common.norm_col(a["alias"])
    return a.dropna().drop_duplicates()


def check_named_org(
    links: pd.DataFrame, act: pd.DataFrame, aliases: pd.DataFrame, agency_prefixes: set[str] | None = None
) -> pd.Series:
    """Does the organisation named on the linked side publish the linked activity?

    "match"            the named ref equals the linked activity's reporting-org ref,
                       or any identifier of its publisher folder, or the linked
                       activity ID starts with "<ref>-", or "XM-DAC-<ref>" is its
                       reporting-org ref (bare DAC codes such as 41122). The
                       "starts with" test is not applied to a ref without a hyphen
                       ("GB") or to a bare registration-agency prefix ("XM-DAC",
                       agency_prefixes), which name no organisation.
    "other publisher"  the named ref is an identifier of a different publisher
    "unrecognised ref" a ref is named but it is no publisher's identifier (often a
                       name or a badly written code, e.g. "FCDO", "GB-GOV-01")
    "no org ref"       no ref is named, so it cannot be checked
    """
    a = act.set_index("iati_identifier")
    named = links["named_org_ref"]
    linked = links["linked_activity"]
    linked_pub = linked.map(a["reporting_org_ref"])
    linked_folder = linked.map(a["publisher_folder"])

    m_pub = (named == linked_pub) | (("XM-DAC-" + named) == linked_pub)
    m_alias = (
        pd.DataFrame({"publisher_folder": linked_folder, "alias": named})
        .merge(aliases.assign(_hit=True), on=["publisher_folder", "alias"], how="left")["_hit"]
        .fillna(False)
        .to_numpy(dtype=bool)
    )
    # "linked ID starts with <ref>-", done column-wise: group rows by prefix length.
    m_prefix = pd.Series(False, index=links.index)
    names_an_org = named.str.contains("-", regex=False) & ~named.isin(agency_prefixes or set())
    plen = named.str.len().where(names_an_org.fillna(False).astype(bool))
    for n in plen.dropna().unique():
        rows = plen == n
        m_prefix[rows] = linked[rows].str.slice(0, int(n) + 1) == named[rows] + "-"
    match = m_pub.fillna(False).to_numpy(dtype=bool) | m_alias | m_prefix.fillna(False).to_numpy(dtype=bool)
    known = named.isin(aliases["alias"]).to_numpy(dtype=bool)
    return pd.Series(
        np.select(
            [named.isna().to_numpy(), match, known],
            ["no org ref", "match", "other publisher"],
            default="unrecognised ref",
        ),
        index=links.index,
    )


CHECKS = ["match", "other publisher", "unrecognised ref", "no org ref"]  # strongest first


def side_check(links: pd.DataFrame, side: str) -> pd.Series:
    """Per edge, the org check on one side: the strongest result among that side's
    transactions, in the order of CHECKS (a match anywhere counts as a match)."""
    x = links[links["side"] == side]
    c = x.groupby(KEY + ["org_check"]).size().unstack("org_check", fill_value=0)
    for col in CHECKS:
        if col not in c:
            c[col] = 0
    return pd.Series(
        np.select([c[k] > 0 for k in CHECKS[:-1]], CHECKS[:-1], default="no org ref"), index=c.index
    )


def reverses_own_funding(edges: pd.DataFrame) -> pd.Series:
    """True for an edge X -> Y that only X reports (provider side), while X also
    reports receiving funds from Y (edge Y -> X reported by the receiver).
    This is the GALVmed pattern: a publisher puts its funder's activity ID in
    receiver-activity-id. Does not depend on organisation refs."""
    funders = edges.loc[edges["reported_by_receiver"], KEY]
    reverse = pd.MultiIndex.from_frame(funders[["to_activity", "from_activity"]].set_axis(KEY, axis=1))
    rev = pd.MultiIndex.from_frame(edges[KEY]).isin(reverse)
    return pd.Series(rev, index=edges.index) & edges["reported_by_provider"] & ~edges["reported_by_receiver"]


def is_plausible_link(edges: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Plausibility of each edge from the org checks on its two sides.

    Returns (plausibility label, plausible, plausible_strict). Labels, checked in order:
      "reverses own funding link"  reverses_own_funding() and no org match -> implausible
      "org matches"                a named org matches on at least one side
      "names another publisher"    a named ref belongs to a different publisher -> implausible
      "unrecognised org ref"       refs are given but are no publisher's identifier
      "no org ref"                 no ref on either side
    `plausible` follows config.PLAUSIBILITY_RULE:
      "known_publishers"  only the two implausible labels above are implausible
      "strict"            also "unrecognised org ref" (the named ref must match)
    `plausible_strict` is always the strict version, for comparison.
    """
    pc, rc = edges["provider_org_check"], edges["receiver_org_check"]

    def either(v: str) -> pd.Series:
        return (pc == v) | (rc == v)

    lab = pd.Series(
        np.select(
            [
                reverses_own_funding(edges) & ~either("match"),
                either("match"),
                either("other publisher"),
                either("unrecognised ref"),
            ],
            ["reverses own funding link", "org matches", "names another publisher", "unrecognised org ref"],
            default="no org ref",
        ),
        index=edges.index,
    )
    bad = ["reverses own funding link", "names another publisher"]
    plausible_strict = ~lab.isin(bad + ["unrecognised org ref"])
    plausible = plausible_strict if config.PLAUSIBILITY_RULE == "strict" else ~lab.isin(bad)
    return lab, plausible, plausible_strict


def values_by_currency(links: pd.DataFrame, key: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sums of value per edge, side, transaction type and currency. Values are never
    added across currencies, nor across types (a commitment and the disbursement
    that follows it are the same money).
    Returns (long table, wide text columns provider_/receiver_value_by_currency,
    e.g. "type 2 DKK 245000000; type 3 DKK 245000000")."""
    v = links.assign(currency=links["currency"].fillna("no currency"))
    by = key + ["side", "transaction_type", "currency"]
    long = v.groupby(by).agg(
        value=("value", "sum"), n_transactions=("value", "size"), _n_valued=("value", "count")
    )
    # A sum over values that are all unreadable is unknown (NA), not 0 (pandas gives 0)
    long["value"] = long["value"].where(long.pop("_n_valued") > 0)
    long = long.reset_index()
    txt = long.assign(
        _n=long["transaction_type"].astype(int),
        t="type "
        + long["transaction_type"]
        + " "
        + long["currency"]
        + " "
        + long["value"].round(0).astype("Int64").astype("string").fillna("NA"),
    )
    txt = txt.sort_values(key + ["side", "_n", "currency"]).groupby(key + ["side"])["t"].agg("; ".join)
    wide = txt.unstack("side").reindex(columns=["provider", "receiver"])
    wide.columns = ["provider_value_by_currency", "receiver_value_by_currency"]
    return long, wide


def joined_types(links: pd.DataFrame, key: list[str]) -> pd.Series:
    """Transaction types behind each edge, sorted numerically, e.g. "1;2;3"."""
    t = links[key + ["transaction_type"]].drop_duplicates()
    t = t.assign(_n=t["transaction_type"].astype(int)).sort_values(key + ["_n"])
    return t.groupby(key)["transaction_type"].agg(";".join)


def add_org_columns(df: pd.DataFrame, org: pd.DataFrame, cols: dict[str, str]) -> pd.DataFrame:
    """Add <prefix>_name and <prefix>_type for each org-ref column in cols
    ({ref column: prefix}) from organisations.parquet."""
    o = org.set_index("org_ref")
    for ref, prefix in cols.items():
        df[f"{prefix}_name"] = df[ref].map(o["org_name"])
        df[f"{prefix}_type"] = df[ref].map(o["org_type_label"])
    return df


def same_organisation(e: pd.DataFrame) -> pd.Series:
    """TRUE if both activities of a link belong to one organisation: the same
    reporting-org ref. If either ref is missing, the same publisher folder. (Before the
    code review a missing ref always counted as a different organisation.)"""
    both = e["from_org"].notna() & e["to_org"].notna()
    by_ref = e["from_org"] == e["to_org"]
    by_folder = e["from_publisher_folder"] == e["to_publisher_folder"]
    return by_ref.where(both, by_folder).fillna(False).astype(bool)


def aggregate_activity_edges(links: pd.DataFrame, act: pd.DataFrame, org: pd.DataFrame) -> tuple:
    """One row per (from_activity, to_activity) with evidence columns."""
    links = links.assign(
        _p=(links["side"] == "provider"),
        _r=(links["side"] == "receiver"),
    )
    g = links.groupby(KEY)
    e = g.agg(
        n_transactions=("side", "size"),
        n_provider_transactions=("_p", "sum"),
        n_receiver_transactions=("_r", "sum"),
        first_year=("transaction_year", "min"),
        last_year=("transaction_year", "max"),
    )
    e["reported_by_provider"] = e["n_provider_transactions"] > 0
    e["reported_by_receiver"] = e["n_receiver_transactions"] > 0
    e["transaction_types"] = joined_types(links, KEY)
    e["provider_org_check"] = side_check(links, "provider")
    e["receiver_org_check"] = side_check(links, "receiver")
    long, wide = values_by_currency(links, KEY)
    e = e.join(wide).reset_index()

    a = act.set_index("iati_identifier")
    for end in ["from", "to"]:
        e[f"{end}_org"] = e[f"{end}_activity"].map(a["reporting_org_ref"])
        e[f"{end}_publisher_folder"] = e[f"{end}_activity"].map(a["publisher_folder"])
        e[f"{end}_publisher_group"] = e[f"{end}_activity"].map(a["publisher_group"])
    e = add_org_columns(e, org, {"from_org": "from_org", "to_org": "to_org"})

    e["plausibility"], e["plausible"], e["plausible_strict"] = is_plausible_link(e)
    e["same_org"] = same_organisation(e)
    e["expenditure_only"] = e["transaction_types"] == "4"
    e["evidence"] = np.select(
        [~e["plausible"], e["reported_by_provider"] & e["reported_by_receiver"]],
        ["implausible", "confirmed"],
        default="one-sided",
    )
    e["in_network"] = (
        e["plausible"]
        & (config.INCLUDE_SAME_ORG_LINKS_IN_NETWORK | ~e["same_org"])
        & (config.INCLUDE_EXPENDITURE_LINKS_IN_NETWORK | ~e["expenditure_only"])
    )
    order = [
        "from_activity",
        "to_activity",
        "from_org",
        "from_org_name",
        "from_org_type",
        "from_publisher_group",
        "to_org",
        "to_org_name",
        "to_org_type",
        "to_publisher_group",
        "reported_by_provider",
        "reported_by_receiver",
        "evidence",
        "n_transactions",
        "n_provider_transactions",
        "n_receiver_transactions",
        "transaction_types",
        "expenditure_only",
        "first_year",
        "last_year",
        "provider_value_by_currency",
        "receiver_value_by_currency",
        "provider_org_check",
        "receiver_org_check",
        "plausibility",
        "plausible",
        "plausible_strict",
        "same_org",
        "in_network",
        "from_publisher_folder",
        "to_publisher_folder",
    ]
    return e[order], long


# =============================================================================
# Organisation links
# =============================================================================
def org_link_transactions(tx: pd.DataFrame) -> pd.DataFrame:
    """One row per transaction naming an organisation ref on the other side
    (same transaction types as the activity links; internal flows left out)."""
    rows = []
    for side, types, from_col, to_col in [
        ("provider", config.link_provider_types(), "reporting_org_ref", "receiver_org_ref"),
        ("receiver", config.link_receiver_types(), "provider_org_ref", "reporting_org_ref"),
    ]:
        hit = tx["transaction_type"].isin(types) & tx[from_col].notna() & tx[to_col].notna() & ~tx["internal"]
        x = tx.loc[
            hit.to_numpy(), [from_col, to_col, "transaction_type", "transaction_year", "value", "currency"]
        ]
        rows.append(
            x.set_axis(OKEY + ["transaction_type", "transaction_year", "value", "currency"], axis=1).assign(
                side=side
            )
        )
    links = pd.concat(rows, ignore_index=True)
    return links[links["from_org"] != links["to_org"]]


def aggregate_org_edges(olinks: pd.DataFrame, edges: pd.DataFrame, org: pd.DataFrame) -> tuple:
    """One row per (from_org, to_org): evidence from organisation refs, plus the
    number of activity links between the two organisations in the network."""
    olinks = olinks.assign(_p=olinks["side"] == "provider", _r=olinks["side"] == "receiver")
    g = olinks.groupby(OKEY)
    o = g.agg(
        n_transactions=("side", "size"),
        n_provider_transactions=("_p", "sum"),
        n_receiver_transactions=("_r", "sum"),
        first_year=("transaction_year", "min"),
        last_year=("transaction_year", "max"),
    )
    o["transaction_types"] = joined_types(olinks, OKEY)
    long, wide = values_by_currency(olinks, OKEY)
    o = o.join(wide)

    net = edges[edges["in_network"]]
    net = net.assign(_confirmed=net["evidence"] == "confirmed")
    act_links = net.groupby(OKEY).agg(
        n_activity_links=("evidence", "size"),
        n_activity_links_confirmed=("_confirmed", "sum"),
    )
    o = o.join(act_links, how="outer")
    for c in [
        "n_transactions",
        "n_provider_transactions",
        "n_receiver_transactions",
        "n_activity_links",
        "n_activity_links_confirmed",
    ]:
        o[c] = o[c].fillna(0).astype(int)
    o["reported_by_provider"] = o["n_provider_transactions"] > 0
    o["reported_by_receiver"] = o["n_receiver_transactions"] > 0
    o["has_activity_link"] = o["n_activity_links"] > 0
    o = o.reset_index()
    o = add_org_columns(o, org, {"from_org": "from_org", "to_org": "to_org"})
    oi = org.set_index("org_ref")
    for end in ["from", "to"]:
        o[f"{end}_is_publisher"] = o[f"{end}_org"].map(oi["is_publisher"]).fillna(False).astype(bool)
        o[f"{end}_well_formed"] = o[f"{end}_org"].map(oi["well_formed"]).fillna(False).astype(bool)
    return o, long


# =============================================================================
# Stage
# =============================================================================
def run(out_dir: Path) -> dict:
    """Run stage 3 on the stage 2 outputs in out_dir. Returns the stage summary."""
    log = common.setup_logging(out_dir, STAGE)
    t0 = time.time()
    s: dict = {}
    tx = pd.read_parquet(
        common.require_file(out_dir / "transactions_clean.parquet", "02_clean.py"), columns=TX_COLS
    )
    act = pd.read_parquet(
        out_dir / "activities_clean.parquet",
        columns=["iati_identifier", "reporting_org_ref", "publisher_folder", "publisher_group"],
    )
    org = pd.read_parquet(out_dir / "organisations.parquet")
    publishers = pd.read_parquet(out_dir / "publishers.parquet")

    s["exact_duplicate_transactions_left_out"] = int(tx["exact_duplicate"].sum())
    tx = tx[~tx["exact_duplicate"].to_numpy()].reset_index(drop=True)

    # Activity links
    links = link_transactions(tx, pd.Index(act["iati_identifier"]))
    agency_prefixes = set(common.load_codelists()[0])
    links["org_check"] = check_named_org(links, act, publisher_aliases(act, publishers), agency_prefixes)
    links.to_parquet(out_dir / "link_transactions.parquet", index=False)
    s["link_transactions"] = len(links)
    s["link_transactions_by_side"] = links["side"].value_counts().to_dict()
    s["link_transactions_by_org_check"] = links.groupby(["side", "org_check"]).size().unstack().to_dict()
    log.info("%d transactions name a published counterpart activity", len(links))

    edges, edge_values = aggregate_activity_edges(links, act, org)
    assert edges[KEY].notna().all().all() and not edges.duplicated(KEY).any()
    assert edges["n_transactions"].sum() == len(links)
    edges.to_parquet(out_dir / "activity_edges.parquet", index=False)
    edges.to_csv(out_dir / "activity_edges.csv", index=False)
    edge_values.to_parquet(out_dir / "activity_edge_values.parquet", index=False)
    s["activity_edges"] = len(edges)
    s["activity_edges_by_evidence"] = edges["evidence"].value_counts().to_dict()
    s["activity_edges_by_plausibility"] = edges["plausibility"].value_counts().to_dict()
    s["activity_edges_same_org"] = int(edges["same_org"].sum())
    s["activity_edges_expenditure_only"] = int(edges["expenditure_only"].sum())
    s["activity_edges_in_network"] = int(edges["in_network"].sum())
    s["activities_in_network"] = int(pd.concat([edges.loc[edges["in_network"], c] for c in KEY]).nunique())
    log.info("%d activity edges, %d in the network", len(edges), s["activity_edges_in_network"])

    # Organisation links
    olinks = org_link_transactions(tx)
    del tx
    s["org_link_transactions"] = len(olinks)
    oedges, oedge_values = aggregate_org_edges(olinks, edges, org)
    assert not oedges.duplicated(OKEY).any()
    oedges.to_parquet(out_dir / "org_edges.parquet", index=False)
    oedges.to_csv(out_dir / "org_edges.csv", index=False)
    oedge_values.to_parquet(out_dir / "org_edge_values.parquet", index=False)
    s["org_edges"] = len(oedges)
    s["org_edges_confirmed"] = int((oedges["reported_by_provider"] & oedges["reported_by_receiver"]).sum())
    s["org_edges_with_activity_link"] = int(oedges["has_activity_link"].sum())
    s["org_edges_to_publisher"] = int(oedges["to_is_publisher"].sum())
    s["organisations_in_org_network"] = int(pd.concat([oedges["from_org"], oedges["to_org"]]).nunique())

    s["seconds"] = round(time.time() - t0)
    common.write_summary(out_dir, STAGE, s)
    log.info("Done: %d org edges (%.0fs)", len(oedges), time.time() - t0)
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", type=Path, default=None)
    a = ap.parse_args()
    run(a.out_dir or config.output_dir())


if __name__ == "__main__":
    main()
