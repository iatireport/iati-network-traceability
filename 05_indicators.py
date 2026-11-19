"""Stage 5: the traceability indicators, overall and by publisher group, computed on
the global network and then for each subset in config.SUBSETS.

Input   <output dir>/transactions_clean.parquet, activities_clean.parquet,
        activity_edges.parquet, related_activities_clean.parquet,
        organisations.parquet, duplicates_removed.csv, chains.csv,
        chain_steps.csv          (stages 2-4)
Output  <output dir>/
          indicators.csv         one row per scope x publisher group x indicator,
                                 with numerator, denominator, percent and unit
          depth_by_scope.csv     chain depth in three units (chains, origins,
                                 organisation paths) for each scope and origin group
          indicators_by_publisher.csv  the transaction indicators (2 to 5, x1, x1b)
                                 for each reporting organisation and scope
          indicator3_by_publisher.csv  indicator 3 for each reporting organisation, all
                                 years: default and disbursements only
          indicator_settings.json  the settings used

Definitions are in docs/indicators.md and in TX_INDICATORS below (one place).
Every indicator is computed from columns flagged once per transaction or activity
(flag_transactions(), connected_activities()); a scope is a filter on those rows,
and a group is a groupby. Exact duplicate transactions are left out.

Scopes: "global" (all countries, all years), then each (country, year) subset.
  Transactions in a subset: the transaction's countries include the country, where
  a transaction's countries are its activity's activity-level countries, or its own
  if the activity names none (add_countries(); the same rule as stage 6). And
  (config.YEAR_BY_TRANSACTION_DATE) the transaction is dated in the year, or
  (otherwise) the activity's period overlaps the year.
  Activities in a subset: activities with at least one transaction in the subset.
The network itself is always the global one, so a link to an activity outside the
subset still counts.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

import common
import config

STAGE = "05_indicators"
ALL = "All publishers"

# code, name, unit, numerator flag, denominator flag (transaction-level indicators)
TX_INDICATORS = [
    ("2", "Incoming transactions with a valid provider ID", "transactions", "n2", "d2"),
    (
        "2b",
        "Incoming: provider ID is a publisher's identifier not in the codelist",
        "transactions",
        "n2b",
        "d2",
    ),
    ("3", "Outgoing transactions with a valid receiver ID", "transactions", "n3", "d3"),
    (
        "3b",
        "Outgoing: receiver ID is a publisher's identifier not in the codelist",
        "transactions",
        "n3b",
        "d3",
    ),
    ("4", "Counterpart IDs that match an IATI publisher", "transactions", "n4", "d4"),
    ("5", "Outgoing links to published activities confirmed by the receiver", "transactions", "n5", "d5"),
    ("x1", "Counterpart activity ID given but not published", "transactions", "nx1", "dx1"),
    ("x1b", "Organisation ID given in the activity-ID field", "transactions", "nx1b", "dx1b"),
]

TX_COLS = [
    "copy_id",
    "iati_identifier",
    "reporting_org_ref",
    "publisher_group",
    "transaction_type",
    "transaction_year",
    "direction",
    "internal",
    "exact_duplicate",
    "self_reference",
    "valid_provider_ref",
    "valid_receiver_ref",
    "counterpart_ref",
    "counterpart_activity_id",
    "counterpart_activity_published",
    "receiver_activity_id",
    "tx_recipient_countries",
]


# =============================================================================
# Flags (computed once, for all transactions)
# =============================================================================
def indicator3_types() -> set[str]:
    """Transaction types counted as outgoing in indicators 3, 4 and x1."""
    return config.flow_out_types() | (config.EXPENDITURE if config.INDICATOR3_INCLUDE_EXPENDITURE else set())


def indicator3_flags(tx: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Denominator and numerator of indicator 3 (also used by 06_spatial.py):
    outgoing transactions, not internal, and those whose receiver ref is well
    formed. Needs the columns transaction_type, internal, valid_receiver_ref."""
    d3 = tx["transaction_type"].isin(indicator3_types()) & ~tx["internal"]
    return d3, d3 & tx["valid_receiver_ref"]


def flag_transactions(
    tx: pd.DataFrame, edges: pd.DataFrame, publishers: set[str], org_refs: set[str] | None = None
) -> pd.DataFrame:
    """Add one boolean column per indicator numerator (n*) and denominator (d*).

    With config.DISBURSEMENTS_ONLY, "commitment or disbursement" below means
    disbursement (3) only, and "incoming funds or incoming commitment" means
    incoming funds (1) only; d4 and dx1b are limited to those types.

    d2  incoming funds or incoming commitment (types 1, 11)
    n2  ... whose provider ref is well formed
    d3  outgoing commitment or disbursement (2, 3), plus expenditure (4) if
        config.INDICATOR3_INCLUDE_EXPENDITURE, not internal
    n3  ... whose receiver ref is well formed
    n2b, n3b  (same denominators as 2 and 3) refs that are not well formed but are
        the identifier of an IATI publisher, e.g. USAID's "US-GOV-1" (US-GOV is not
        in the registration-agency codelist). 2 + 2b is the share naming either a
        valid ID or a publisher's own identifier (decided November 2026, review M3).
    d4  incoming or outgoing transactions naming a counterpart ref, not internal
        (type 4 only if the expenditure setting is on)
    n4  ... whose counterpart ref belongs to an IATI publisher
    d5  outgoing commitment or disbursement, not internal, naming a published
        receiver activity other than its own
    n5  ... where the receiver also reports the link (edge reported_by_receiver)
    dx1b incoming or outgoing transactions naming a counterpart activity ID other
        than their own (type 4 only if the expenditure setting is on)
    nx1b ... where that "activity ID" is an organisation identifier
        (common.org_id_as_activity_id), e.g. USAID's "US-GOV-1"
    dx1 as dx1b, without the organisation identifiers
    nx1 ... where the ID is not a published activity
    traced  names a published counterpart activity, not internal, not itself (D2)
    """
    t = tx["transaction_type"]
    ext = ~tx["internal"]
    exp_ok = config.INDICATOR3_INCLUDE_EXPENDITURE | (t != "4")
    out_types = indicator3_types()
    in_out = tx["direction"].isin(["in", "out"])
    if config.DISBURSEMENTS_ONLY:  # indicators 4 and x1: incoming funds and disbursements only
        in_out &= t.isin(config.flow_in_types() | out_types)

    tx["d2"] = t.isin(config.flow_in_types())
    tx["n2"] = tx["d2"] & tx["valid_provider_ref"]
    tx["d3"], tx["n3"] = indicator3_flags(tx)
    # Types 1 and 11 are incoming, so their counterpart is the provider; types 2, 3
    # and 4 are outgoing, so their counterpart is the receiver.
    known_publisher = tx["counterpart_ref"].isin(publishers)
    tx["n2b"] = tx["d2"] & ~tx["valid_provider_ref"] & known_publisher
    tx["n3b"] = tx["d3"] & ~tx["n3"] & known_publisher
    tx["d4"] = in_out & tx["counterpart_ref"].notna() & ext & exp_ok
    tx["n4"] = tx["d4"] & tx["counterpart_ref"].isin(publishers)
    tx["d5"] = (
        t.isin(config.flow_out_types()) & ext & tx["counterpart_activity_published"] & ~tx["self_reference"]
    )
    confirmed = edges.loc[edges["reported_by_receiver"], ["from_activity", "to_activity"]]
    key = pd.MultiIndex.from_frame(confirmed)
    pair = pd.MultiIndex.from_arrays([tx["iati_identifier"], tx["receiver_activity_id"]])
    tx["n5"] = tx["d5"] & pair.isin(key)
    org_as_act = common.org_id_as_activity_id(
        tx["counterpart_activity_id"], tx["counterpart_activity_published"], org_refs or set()
    )
    tx["dx1b"] = in_out & tx["counterpart_activity_id"].notna() & ~tx["self_reference"] & exp_ok
    tx["nx1b"] = tx["dx1b"] & org_as_act
    tx["dx1"] = tx["dx1b"] & ~org_as_act
    tx["nx1"] = tx["dx1"] & ~tx["counterpart_activity_published"]
    tx["traced"] = tx["counterpart_activity_published"] & ext & ~tx["self_reference"]
    for c in [
        "d2",
        "n2",
        "n2b",
        "d3",
        "n3",
        "n3b",
        "d4",
        "n4",
        "d5",
        "n5",
        "dx1",
        "nx1",
        "dx1b",
        "nx1b",
        "traced",
    ]:
        tx[c] = tx[c].fillna(False).astype(bool)
    return tx


def connected_activities(edges: pd.DataFrame, related: pd.DataFrame) -> set[str]:
    """Activities connected to another IATI activity (indicator 1): an end of an
    edge in the network, plus related-activity links if
    config.INDICATOR1_RELATED_ACTIVITIES is "other_publishers" or "all"."""
    net = edges[edges["in_network"]]
    out = set(net["from_activity"]) | set(net["to_activity"])
    mode = config.INDICATOR1_RELATED_ACTIVITIES
    assert mode in ("none", "other_publishers", "all"), mode
    if mode != "none":
        r = related[related["related_published"]]
        if mode == "other_publishers":
            r = r[~r["same_publisher"]]
        out |= set(r["iati_identifier"]) | set(r["related_activity_ref"])
    return out


# =============================================================================
# Scopes
# =============================================================================
def names_country(s: pd.Series, country: str) -> pd.Series:
    """True where a ";"-separated country list contains the country."""
    return s.fillna("").str.contains(rf"(?:^|;){country}(?:;|$)", regex=True)


def add_countries(tx: pd.DataFrame, act: pd.DataFrame) -> pd.DataFrame:
    """Add tx["countries"]: the countries of each transaction, by the rule shared with
    stage 6 (common.transaction_countries()): the activity's activity-level countries;
    only if the activity names none, the transaction's own."""
    level = common.valid_countries(act.set_index("copy_id")["recipient_countries"])
    own = common.valid_countries(tx["tx_recipient_countries"])
    tx["countries"] = common.transaction_countries(tx["copy_id"], own, level)
    return tx


def in_scope(tx: pd.DataFrame, act: pd.DataFrame, country: str | None, year: int | None) -> np.ndarray:
    """Boolean mask of transactions in a (country, year) subset (see module docstring).
    Needs tx["countries"] from add_countries()."""
    keep = np.ones(len(tx), dtype=bool)
    if country:
        keep &= names_country(tx["countries"], country).to_numpy(dtype=bool)
    if year:
        if config.YEAR_BY_TRANSACTION_DATE:
            keep &= (tx["transaction_year"] == year).fillna(False).to_numpy(dtype=bool)
        else:
            keep &= (
                tx["copy_id"].map(activity_overlaps_year(act, tx, year)).fillna(False).to_numpy(dtype=bool)
            )
    return keep


def activity_overlaps_year(act: pd.DataFrame, tx: pd.DataFrame, year: int) -> pd.Series:
    """True if the activity's period overlaps the year. Period: start and end date;
    a missing date is replaced by the year of its first or last transaction."""
    yrs = tx.groupby("copy_id")["transaction_year"].agg(["min", "max"])
    a = act.set_index("copy_id")
    start = pd.to_numeric(a["start_date"].str[:4], errors="coerce").fillna(yrs["min"].reindex(a.index))
    end = pd.to_numeric(a["end_date"].str[:4], errors="coerce").fillna(yrs["max"].reindex(a.index))
    return (start <= year) & (end >= year)


# =============================================================================
# Indicators for one scope
# =============================================================================
def share(num: float, den: float) -> float | None:
    return round(100 * num / den, 1) if den else None


def row(scope: dict, group: str, code: str, name: str, unit: str, num, den, value=None) -> dict:
    return {
        **scope,
        "group": group,
        "indicator": code,
        "name": name,
        "unit": unit,
        "numerator": None if num is None else int(num),
        "denominator": None if den is None else int(den),
        "percent": share(num, den) if den is not None and num is not None else None,
        "value": value,
        "small_base": bool(den is not None and den < config.SMALL_BASE),
    }


def scope_indicators(
    scope: dict,
    tx: pd.DataFrame,
    connected: set[str],
    removed_copies: pd.Series,
) -> list[dict]:
    """Indicators 1-5, 7a, 7b, x1, x1b, D1, D1a, D1b, D2, D2g for one scope, overall and by group.
    tx holds the scope's transactions, exact duplicates included (for D1)."""
    rows: list[dict] = []
    dup = tx["exact_duplicate"].to_numpy()
    t = tx[~dup]
    acts = t[["iati_identifier", "reporting_org_ref", "publisher_group"]].drop_duplicates("iati_identifier")
    acts = acts.assign(connected=acts["iati_identifier"].isin(connected))

    # Every flag once (2 and 2b share d2, 3 and 3b share d3)
    flags = list(dict.fromkeys(f for _, _, _, n, d in TX_INDICATORS for f in (n, d)))
    sums = {ALL: t[flags].sum()} | dict(iter(t.groupby("publisher_group")[flags].sum().iterrows()))
    for group, s in sums.items():
        a = acts if group == ALL else acts[acts["publisher_group"] == group]
        n_conn = int(a["connected"].sum())
        orgs = a.groupby("reporting_org_ref")["connected"].any()
        rows.append(
            row(
                scope,
                group,
                "1",
                "Activities connected to another IATI activity",
                "activities",
                n_conn,
                len(a),
            )
        )
        for code, name, unit, n, d in TX_INDICATORS:
            rows.append(row(scope, group, code, name, unit, s[n], s[d]))
        rows.append(row(scope, group, "7a", "Isolated activities", "activities", len(a) - n_conn, len(a)))
        rows.append(
            row(
                scope,
                group,
                "7b",
                "Isolated publishing organisations",
                "organisations",
                int((~orgs).sum()),
                len(orgs),
            )
        )

    # Data-quality measures, all publishers only
    n_scope_acts = len(acts)
    rows.append(
        row(
            scope,
            ALL,
            "D1",
            "Exact duplicate transactions (left out)",
            "transactions",
            int(dup.sum()),
            len(tx),
        )
    )
    rows.append(
        row(
            scope,
            ALL,
            "D1a",
            "Activities with a duplicate copy removed in stage 2",
            "activities",
            int(acts["iati_identifier"].isin(removed_copies).sum()),
            n_scope_acts,
        )
    )
    both = t["n5"]
    rows.append(
        row(
            scope,
            ALL,
            "D1b",
            "Outgoing transactions whose flow the receiver also reports (double-count risk)",
            "transactions",
            int(both.sum()),
            int(t["d5"].sum()),
        )
    )
    traced = t[t["traced"]].groupby("reporting_org_ref").size().sort_values(ascending=False)
    rows.append(
        row(
            scope,
            ALL,
            "D2",
            "Traced transactions held by the top 10 publishers",
            "transactions",
            int(traced.head(10).sum()),
            int(traced.sum()),
        )
    )
    # D2g: share of all traced transactions in the scope held by each publisher group
    # (organisation type of the reporting organisation). Added for the working paper.
    # Every group with transactions in the scope gets a row, 0 if it has no traced transactions.
    by_group = t[t["traced"]].groupby("publisher_group").size()
    for group in [ALL] + sorted(t["publisher_group"].dropna().unique()):
        n = int(traced.sum()) if group == ALL else int(by_group.get(group, 0))
        rows.append(
            row(
                scope,
                group,
                "D2g",
                "Traced transactions held by the publisher group",
                "transactions",
                n,
                int(traced.sum()),
            )
        )
    return rows


def publisher_indicators(scope: dict, tx: pd.DataFrame) -> pd.DataFrame:
    """The transaction indicators (TX_INDICATORS) for each reporting organisation in the
    scope, with the same flags as indicators.csv, so publishers' numerators and
    denominators add up to the "All publishers" row. Exact duplicates left out; rows
    with a zero denominator are dropped. Added November 2026 for the working paper."""
    t = tx[~tx["exact_duplicate"].to_numpy()]
    flags = list(dict.fromkeys(f for _, _, _, n, d in TX_INDICATORS for f in (n, d)))
    by = t.groupby("reporting_org_ref", dropna=False)  # keep transactions without a reporting-org ref, so rows add up
    sums = by[flags].sum()
    group = by["publisher_group"].first()
    out = []
    for code, _, _, n, d in TX_INDICATORS:
        x = pd.DataFrame({"numerator": sums[n].astype(int), "denominator": sums[d].astype(int)})
        x = x[x["denominator"] > 0]
        out.append(x.assign(indicator=code, publisher_group=group.reindex(x.index)).reset_index())
    res = pd.concat(out, ignore_index=True)
    res.insert(0, "scope", scope["scope"])
    res["percent"] = (100 * res["numerator"] / res["denominator"]).round(1)
    return res[["scope", "reporting_org_ref", "publisher_group", "indicator", "numerator", "denominator", "percent"]]


def indicator3_by_publisher(tx: pd.DataFrame) -> pd.DataFrame:
    """Indicator 3 for each reporting organisation (all years, exact duplicates left out),
    under two settings: the default (indicator3_types(): commitments and disbursements
    pooled) and disbursements only (type 3). Same rule as indicators.csv: outgoing, not
    internal; numerator: a well-formed receiver identifier. For comparison with the
    publisher-level figures of McCarthy et al. (2026). Added November 2026."""
    t = tx[~tx["exact_duplicate"].to_numpy()]
    d3, n3 = indicator3_flags(t)
    disb = t["transaction_type"] == "3"
    f = pd.DataFrame({"reporting_org_ref": t["reporting_org_ref"], "publisher_group": t["publisher_group"],
                      "outgoing": d3, "valid": n3, "disb_outgoing": d3 & disb, "disb_valid": n3 & disb})
    out = f.groupby("reporting_org_ref", dropna=False).agg(  # keep transactions without a reporting-org ref
        publisher_group=("publisher_group", "first"), outgoing=("outgoing", "sum"), valid=("valid", "sum"),
        disb_outgoing=("disb_outgoing", "sum"), disb_valid=("disb_valid", "sum"),
    ).reset_index()
    out = out[out["outgoing"] > 0]
    for c in ["outgoing", "valid", "disb_outgoing", "disb_valid"]:
        out[c] = out[c].astype(int)
    out["rate"] = (100 * out["valid"] / out["outgoing"]).round(1)
    out["disb_rate"] = (100 * out["disb_valid"] / out["disb_outgoing"].where(out["disb_outgoing"] > 0)).round(1)
    out["has_disbursement"] = out["disb_outgoing"] > 0
    return out.sort_values("outgoing", ascending=False).reset_index(drop=True)


def of_group(df: pd.DataFrame, group: str) -> pd.DataFrame:
    """Rows of one origin publisher group (all rows for "All publishers")."""
    return df if group == ALL else df[df["origin_publisher_group"] == group]


def scope_depth(
    scope: dict, scope_acts: set[str] | None, chains: pd.DataFrame, steps: pd.DataFrame
) -> tuple[list[dict], pd.DataFrame]:
    """Indicator 6 (and 6b-6e) and the depth distribution in three units for the
    chains that pass through at least one activity of the scope (all chains for
    the global scope). By origin publisher group."""
    if scope_acts is None:
        ch = chains
    else:
        hit = steps["from_activity"].isin(scope_acts) | steps["to_activity"].isin(scope_acts)
        ch = chains[chains["chain_id"].isin(steps.loc[hit, "chain_id"])]
    origins = ch.groupby("origin_activity").agg(
        longest_steps=("steps", "max"), origin_publisher_group=("origin_publisher_group", "first")
    )
    paths = ch.drop_duplicates("path_org_refs")
    rows, dist = [], []
    for group in [ALL] + sorted(ch["origin_publisher_group"].dropna().unique()):
        c, o, p = (of_group(df, group) for df in (ch, origins, paths))
        rows += [
            row(
                scope,
                group,
                "6",
                "Average observable chain depth: longest chain per origin (steps)",
                "origins",
                None,
                len(o),
                round(float(o["longest_steps"].mean()), 2) if len(o) else None,
            ),
            row(
                scope,
                group,
                "6b",
                "Origins whose longest chain has 2+ steps",
                "origins",
                int((o["longest_steps"] >= 2).sum()),
                len(o),
            ),
            row(
                scope,
                group,
                "6c",
                "Longest observed chain (steps)",
                "chains",
                None,
                len(c),
                int(c["steps"].max()) if len(c) else None,
            ),
            row(scope, group, "6d", "Chains with 2+ steps", "chains", int((c["steps"] >= 2).sum()), len(c)),
            row(
                scope,
                group,
                "6e",
                "Organisation paths with 2+ steps",
                "organisation paths",
                int((p["steps"] >= 2).sum()),
                len(p),
            ),
            # 6f is TENTATIVE (November 2026): it may be renamed or removed, because the IATI
            # Data Quality Dashboard already reports a different "completeness" measure.
            row(
                scope,
                group,
                "6f",
                "Chain completeness score, mean over chains (tentative)",
                "chains",
                None,
                len(c),
                round(float(c["completeness"].mean()), 3) if len(c) and "completeness" in c else None,
            ),
        ]
        for unit, df, col in [
            ("chains", c, "steps"),
            ("origins (longest chain)", o, "longest_steps"),
            ("organisation paths", p, "steps"),
        ]:
            b = df[col].clip(upper=4).astype(int).astype(str).replace("4", "4+")
            n = b.value_counts().reindex(["1", "2", "3", "4+"], fill_value=0)
            dist.append(
                pd.DataFrame(
                    {
                        **scope,
                        "origin_group": group,
                        "unit": unit,
                        "steps": n.index,
                        "n": n.to_numpy(),
                        "percent": (100 * n / max(n.sum(), 1)).round(1).to_numpy(),
                    }
                )
            )
    return rows, pd.concat(dist)


# =============================================================================
# Stage
# =============================================================================
def run(out_dir: Path) -> dict:
    """Run stage 5 on the outputs of stages 2-4 in out_dir. Returns the summary."""
    log = common.setup_logging(out_dir, STAGE)
    t0 = time.time()
    settings = {
        "INDICATOR3_INCLUDE_EXPENDITURE": config.INDICATOR3_INCLUDE_EXPENDITURE,
        "INDICATOR1_RELATED_ACTIVITIES": config.INDICATOR1_RELATED_ACTIVITIES,
        "YEAR_BY_TRANSACTION_DATE": config.YEAR_BY_TRANSACTION_DATE,
        "PLAUSIBILITY_RULE": config.PLAUSIBILITY_RULE,
        "INCLUDE_SAME_ORG_LINKS_IN_NETWORK": config.INCLUDE_SAME_ORG_LINKS_IN_NETWORK,
        "INCLUDE_EXPENDITURE_LINKS_IN_NETWORK": config.INCLUDE_EXPENDITURE_LINKS_IN_NETWORK,
        "DISBURSEMENTS_ONLY": config.DISBURSEMENTS_ONLY,
        "SUBSETS": config.SUBSETS,
    }
    log.info("Settings: %s", settings)

    edges = pd.read_parquet(common.require_file(out_dir / "activity_edges.parquet", "03_links.py"))
    related = pd.read_parquet(out_dir / "related_activities_clean.parquet")
    act = pd.read_parquet(
        out_dir / "activities_clean.parquet",
        columns=["copy_id", "iati_identifier", "recipient_countries", "start_date", "end_date"],
    )
    org = pd.read_parquet(out_dir / "organisations.parquet")
    removed = common.read_csv(out_dir / "duplicates_removed.csv", usecols=["iati_identifier"])[
        "iati_identifier"
    ]
    chains = common.read_csv(common.require_file(out_dir / "chains.csv", "04_network.py"))
    steps = common.read_csv(out_dir / "chain_steps.csv", usecols=["chain_id", "from_activity", "to_activity"])

    tx = pd.read_parquet(out_dir / "transactions_clean.parquet", columns=TX_COLS)
    publishers = set(org.loc[org["is_publisher"], "org_ref"])
    tx = flag_transactions(tx, edges, publishers, set(org["org_ref"]))
    tx = tx.drop(columns=["counterpart_ref", "counterpart_activity_id", "receiver_activity_id"])
    tx = add_countries(tx, act).drop(columns="tx_recipient_countries")
    connected = connected_activities(edges, related)
    log.info("%d transactions flagged; %d activities connected", len(tx), len(connected))

    scopes = [(None, None)] + [tuple(s) for s in config.SUBSETS]
    rows, dists, counts, by_pub = [], [], {}, []
    for country, year in scopes:
        label = "global" if country is None and year is None else f"{country or 'all'}_{year or 'all'}"
        scope = {"scope": label, "country": country or "all", "year": year or "all"}
        mask = in_scope(tx, act, country, year)
        sub = tx[mask]
        rows += scope_indicators(scope, sub, connected, removed)
        by_pub.append(publisher_indicators(scope, sub))
        sacts = None if label == "global" else set(sub.loc[~sub["exact_duplicate"], "iati_identifier"])
        r6, d6 = scope_depth(scope, sacts, chains, steps)
        rows += r6
        dists.append(d6)
        counts[label] = {"transactions": int(mask.sum()), "activities": int(sub["iati_identifier"].nunique())}
        log.info("Scope %s: %d transactions, %d activities", label, *counts[label].values())

    ind = pd.DataFrame(rows)
    order = {
        c: k
        for k, c in enumerate(
            [
                "1",
                "2",
                "2b",
                "3",
                "3b",
                "4",
                "5",
                "6",
                "6b",
                "6c",
                "6d",
                "6e",
                "6f",
                "7a",
                "7b",
                "x1",
                "x1b",
                "D1",
                "D1a",
                "D1b",
                "D2",
                "D2g",
            ]
        )
    }
    ind = ind.sort_values(
        ["scope", "group", "indicator"],
        key=lambda c: c.map(order) if c.name == "indicator" else c,
        kind="stable",
    )
    assert ind["indicator"].isin(order).all()
    assert (ind["numerator"].fillna(0) <= ind["denominator"].fillna(np.inf)).all()
    ind.to_csv(out_dir / "indicators.csv", index=False)
    pd.concat(dists).to_csv(out_dir / "depth_by_scope.csv", index=False)
    pd.concat(by_pub).to_csv(out_dir / "indicators_by_publisher.csv", index=False)
    bp_all = pd.concat(by_pub)
    tot = bp_all.groupby(["scope", "indicator"])[["numerator", "denominator"]].sum()
    ref = ind[ind["group"] == ALL].set_index(["scope", "indicator"]).loc[tot.index, ["numerator", "denominator"]]
    assert (tot.to_numpy() == ref.to_numpy()).all(), "indicators_by_publisher.csv does not add up to indicators.csv"
    i3 = indicator3_by_publisher(tx)
    i3.to_csv(out_dir / "indicator3_by_publisher.csv", index=False)
    g3 = ind[(ind["scope"] == "global") & (ind["group"] == ALL) & (ind["indicator"] == "3")].iloc[0]
    assert (i3["valid"].sum(), i3["outgoing"].sum()) == (g3["numerator"], g3["denominator"])
    (out_dir / "indicator_settings.json").write_text(json.dumps(settings, indent=1))

    s = {
        "settings": settings,
        "scopes": counts,
        "indicator_rows": len(ind),
        "seconds": round(time.time() - t0),
    }
    common.write_summary(out_dir, STAGE, s)
    log.info("Done: %d indicator rows (%.0fs)", len(ind), time.time() - t0)
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", type=Path, default=None)
    a = ap.parse_args()
    run(a.out_dir or config.output_dir())


if __name__ == "__main__":
    main()
