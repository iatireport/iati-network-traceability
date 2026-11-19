"""Stage 4: the activity network built globally, its chains, and the organisation network.

Input   <output dir>/activity_edges.parquet, org_edges.parquet,
        activities_clean.parquet, transactions_clean.parquet, organisations.parquet
Output  <output dir>/
          cycles.csv                one row per cycle (strongly connected group) left
                                    after excluding implausible links
          chains.csv                one row per maximal chain, with its completeness score
          chain_steps.csv           the same chains, one row per step (long format)
          chain_origins.csv         one row per chain origin with its longest chain
          org_paths.csv             one row per distinct sequence of organisations
          multipliers.csv           one row per organisation that is an intermediary on at
                                    least one organisation path (network multipliers)
          depth_distribution.csv    depth by unit (chains, origins, organisation paths),
                                    number of steps and origin group
          chain_ends.parquet        why each receiving activity has no onward link
          chain_end_reasons.csv     the same, summarised
          chain_end_reasons_by_year.csv  the same for activities with a transaction
                                    dated in each year of config.SUBSETS
          network_nodes.parquet     one row per activity in the network
          org_nodes.parquet/.csv    one row per organisation in the organisation network
          network_summary.json      sizes, components, cycles

Definitions (named functions below):
  build_network()        directed graph from edges with in_network = TRUE
  find_cycles()          groups of activities that can reach each other; the edges
                         inside them are set aside so depth is measured on a graph
                         without loops (not by merging them, as the pilot did)
  enumerate_chains()     every path from an origin (an activity with no traced funder)
                         to an end (an activity with no traced recipient)
  why_chain_stops()      for activities that receive funds but have no onward link,
                         the best evidence their outgoing transactions give
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

import common
import config

STAGE = "04_network"
EVIDENCE_RANK = {"confirmed": 2, "one-sided": 1, "implausible": 0}
END_REASONS = [  # strongest evidence first
    "link only to own organisation or implausible",
    "activity ID given but not published",
    "organisation ID given as activity ID",
    "receiver organisation ID only",
    "receiver name only",
    "outgoing only to itself",
    "no receiver named",
    "no outgoing transactions",
]


# =============================================================================
# Graph, cycles, chains
# =============================================================================
def build_network(edges: pd.DataFrame) -> nx.DiGraph:
    """Directed activity graph from the edges marked in_network (plausible, and
    by default between different organisations)."""
    net = edges[edges["in_network"]]
    return nx.from_pandas_edgelist(net, "from_activity", "to_activity", create_using=nx.DiGraph)


def find_cycles(g: nx.DiGraph) -> tuple[list[set[str]], list[tuple[str, str]]]:
    """Strongly connected groups of 2+ activities (each one is, or contains, a
    cycle), and the list of edges inside them."""
    groups = [c for c in nx.strongly_connected_components(g) if len(c) > 1]
    inside = [e for c in groups for e in g.subgraph(c).edges()]
    return groups, inside


def enumerate_chains(dag: nx.DiGraph, max_chains: int) -> tuple[pd.DataFrame, int]:
    """All maximal paths in a graph without cycles: from an origin (no incoming
    edge) to an end (no outgoing edge). Returns (chain_steps, total number of chains).

    chain_steps has one row per step: chain_id, step (1, 2, ...), from_activity,
    to_activity. Enumeration stops after max_chains chains; the total is counted
    exactly beforehand."""
    n_paths: dict[str, int] = {}
    for v in reversed(list(nx.topological_sort(dag))):
        succ = list(dag.successors(v))
        n_paths[v] = sum(n_paths[s] for s in succ) if succ else 1
    origins = sorted(v for v in dag if dag.in_degree(v) == 0 and dag.out_degree(v) > 0)
    total = sum(n_paths[o] for o in origins)

    cid, sid, frm, to = [], [], [], []
    chain_id = 0
    for o in origins:
        stack = [(o, [o])]
        while stack and chain_id < max_chains:
            node, path = stack.pop()
            succ = sorted(dag.successors(node), reverse=True)
            if not succ:
                chain_id += 1
                for k in range(len(path) - 1):
                    cid.append(chain_id)
                    sid.append(k + 1)
                    frm.append(path[k])
                    to.append(path[k + 1])
                continue
            stack.extend((s, path + [s]) for s in succ)
    steps = pd.DataFrame({"chain_id": cid, "step": sid, "from_activity": frm, "to_activity": to})
    return steps, total


def longest_from(dag: nx.DiGraph) -> pd.Series:
    """Number of steps of the longest chain starting at each activity."""
    out: dict[str, int] = {}
    for v in reversed(list(nx.topological_sort(dag))):
        succ = list(dag.successors(v))
        out[v] = 1 + max(out[s] for s in succ) if succ else 0
    return pd.Series(out, name="longest_steps")


def depth_bin(steps: pd.Series) -> pd.Series:
    """Steps as "1", "2", "3" or "4+"."""
    return steps.clip(upper=4).astype(int).astype(str).replace("4", "4+")


def organisation_paths(chains: pd.DataFrame) -> pd.DataFrame:
    """One row per distinct sequence of organisations (path_org_refs) among the
    chains. Many chains can follow the same organisations through different
    activities, e.g. one funder activity paying into many activities of one partner.

    Columns: org_path_id, path_org_refs, path_orgs, path_org_types, steps, n_chains,
    n_origin_activities, n_chains_confirmed, best_evidence (confirmed if at least
    one chain on the path is confirmed at every step), first_year, last_year,
    countries, origin_publisher_group (of the first chain listed)."""
    by = chains.groupby("path_org_refs", sort=True)
    p = by.agg(
        path_orgs=("path_orgs", "first"),
        path_org_types=("path_org_types", "first"),
        steps=("steps", "first"),
        n_chains=("chain_id", "size"),
        n_origin_activities=("origin_activity", "nunique"),
        first_year=("first_year", "min"),
        last_year=("last_year", "max"),
        origin_publisher_group=("origin_publisher_group", "first"),
    )
    p["n_chains_confirmed"] = (
        (chains["weakest_evidence"] == "confirmed").groupby(chains["path_org_refs"]).sum()
    )
    p["best_evidence"] = np.where(p["n_chains_confirmed"] > 0, "confirmed", "one-sided")
    c = chains.dropna(subset=["countries"])
    c = c.assign(c=c["countries"].str.split(";")).explode("c").drop_duplicates(["path_org_refs", "c"])
    p["countries"] = c.sort_values("c").groupby("path_org_refs")["c"].agg(";".join)
    p = p.sort_values(["steps", "n_chains"], ascending=[False, False]).reset_index()
    p.insert(0, "org_path_id", range(1, len(p) + 1))
    return p


def multipliers(paths: pd.DataFrame) -> pd.DataFrame:
    """Network multipliers: organisations that receive traced funding and pass it on
    within the same chain, i.e. that are neither first nor last on an organisation path.
    One row per organisation identifier. Counted only on the paths where the
    organisation is an intermediary, each path once:
      paths_through      organisation paths on which it is an intermediary
      direct_recipients  distinct organisations right after it on those paths
      downstream_orgs    distinct organisations after it on those paths
      upstream_orgs      distinct organisations before it on those paths
    The organisation itself is never counted as its own partner (a path can pass
    through it twice). org_name and org_type are as on the first path listed.
    Added November 2026 for the working paper."""
    cols = ["org_ref", "org_name", "org_type", "paths_through", "direct_recipients", "downstream_orgs", "upstream_orgs"]
    rows = []
    for pid, refs, names, types in zip(
        paths["org_path_id"], paths["path_org_refs"].str.split(" > "),
        paths["path_orgs"].str.split(" > "), paths["path_org_types"].str.split(" > "),
    ):
        for k in range(1, len(refs) - 1):
            r = refs[k]
            rows.append((r, names[k], types[k], pid, refs[k + 1],
                         {x for x in refs[k + 1:] if x != r}, {x for x in refs[:k] if x != r}))
    if not rows:
        return pd.DataFrame(columns=cols)
    m = pd.DataFrame(rows, columns=["org_ref", "org_name", "org_type", "pid", "next", "down", "up"])
    union = lambda s: len(set().union(*s))  # noqa: E731
    out = m.groupby("org_ref", sort=False).agg(
        org_name=("org_name", "first"),
        org_type=("org_type", "first"),
        paths_through=("pid", "nunique"),
        direct_recipients=("next", "nunique"),
        downstream_orgs=("down", union),
        upstream_orgs=("up", union),
    ).reset_index()
    return out.sort_values(["paths_through", "downstream_orgs", "org_ref"], ascending=[False, False, True])[cols]


# =============================================================================
# Why chains stop
# =============================================================================
def why_chain_stops(
    tx: pd.DataFrame,
    receivers: pd.Index,
    with_onward_edge: pd.Index,
    with_any_edge_out: pd.Index,
    org_refs: set[str],
) -> pd.DataFrame:
    """For each receiving activity without an onward link in the network, the best
    evidence that its outgoing transactions (config.link_provider_types()) give
    about where the money went. Reasons, strongest first (END_REASONS):

      link only to own organisation or implausible   an onward edge exists, but none
                                                     enters the network
      activity ID given but not published            a receiver-activity-id that is
                                                     not a published activity
      organisation ID given as activity ID           the receiver-activity-id holds an
                                                     organisation identifier
                                                     (common.org_id_as_activity_id)
      receiver organisation ID only                  a receiver ref, no activity ID
      receiver name only                             a receiver name, no ref
      outgoing only to itself                        every receiver is the publisher
      no receiver named                              outgoing transactions name no one
      no outgoing transactions
    """
    out = tx[tx["transaction_type"].isin(config.link_provider_types()).to_numpy()]
    out = out[out["iati_identifier"].isin(receivers).to_numpy()]
    ext = out[~out["internal"].to_numpy()]
    org_as_act = common.org_id_as_activity_id(
        ext["receiver_activity_id"], ext["counterpart_activity_published"], org_refs
    )
    unpublished = (
        ext["receiver_activity_id"].notna()
        & ~ext["counterpart_activity_published"]
        & ~ext["self_reference"]
        & ~org_as_act
    )
    flags = pd.DataFrame(
        {
            "iati_identifier": ext["iati_identifier"],
            "unpublished_id": unpublished,
            "org_as_activity": org_as_act,
            "org_id": ext["receiver_org_ref"].notna(),
            "name": ext["receiver_org_name"].notna(),
        }
    )
    per = flags.groupby("iati_identifier").any()
    n_out = out.groupby("iati_identifier").size()
    n_ext = ext.groupby("iati_identifier").size()

    a = pd.DataFrame(index=receivers.rename("iati_identifier"))
    a["n_outgoing_transactions"] = n_out.reindex(a.index).fillna(0).astype(int)
    a["n_outgoing_to_others"] = n_ext.reindex(a.index).fillna(0).astype(int)
    per = per.reindex(a.index, fill_value=False)
    a["end_reason"] = np.select(
        [
            a.index.isin(with_any_edge_out),
            per["unpublished_id"],
            per["org_as_activity"],
            per["org_id"],
            per["name"],
            (a["n_outgoing_transactions"] > 0) & (a["n_outgoing_to_others"] == 0),
            a["n_outgoing_transactions"] > 0,
        ],
        END_REASONS[:-1],
        default=END_REASONS[-1],
    )
    return a[~a.index.isin(with_onward_edge)].reset_index()


def step_points(edges: pd.DataFrame, link_tx: pd.DataFrame, prefixes: list[str], legacy: set[str]) -> pd.DataFrame:
    """The three points of the chain completeness score for each link (from_activity,
    to_activity), one point each for:
      receiver_ref_well_formed  a transaction reported by the funder names a well-formed
                                receiver organisation identifier (common.well_formed())
      funder_names_activity     the funder reports the link: its transaction names the
                                receiver's activity identifier, which resolves to a
                                published activity (edge reported_by_provider)
      receiver_confirms         the receiver reports the link: an incoming transaction
                                naming the funder's activity (edge reported_by_receiver)
    link_tx: link_transactions.parquet (side, from_activity, to_activity, named_org_ref).
    Added November 2026 for the working paper."""
    prov = link_tx[link_tx["side"] == "provider"]
    ok = common.well_formed_col(prov["named_org_ref"].astype("string").str.strip().str.upper(), prefixes, legacy)
    wf = prov.assign(ok=ok.to_numpy()).groupby(["from_activity", "to_activity"])["ok"].any()
    e = edges[["from_activity", "to_activity", "reported_by_provider", "reported_by_receiver"]].copy()
    e["receiver_ref_well_formed"] = pd.MultiIndex.from_frame(e[["from_activity", "to_activity"]]).map(wf.to_dict().get)
    e["receiver_ref_well_formed"] = e["receiver_ref_well_formed"].fillna(False).astype(bool)
    e["funder_names_activity"] = e["reported_by_provider"].astype(bool)
    e["receiver_confirms"] = e["reported_by_receiver"].astype(bool)
    e["step_points"] = e[["receiver_ref_well_formed", "funder_names_activity", "receiver_confirms"]].sum(axis=1)
    return e[["from_activity", "to_activity", "receiver_ref_well_formed", "funder_names_activity", "receiver_confirms", "step_points"]]


def completeness(steps: pd.DataFrame) -> pd.Series:
    """Chain completeness score: the points of all steps (step_points(), 0 to 3 each)
    divided by three times the number of steps. Range 0 to 1; 1 means every step names
    a well-formed receiver identifier, names the receiver's published activity and is
    confirmed by the receiver. By chain_id. Proposed for the working paper with
    reference to the fields traced by EyeOpenerWorks and Open Works (2020)."""
    by = steps.groupby("chain_id")
    return (by["step_points"].sum() / (3 * by.size())).round(3).rename("completeness")


def end_reasons_summary(ends: pd.DataFrame, scope: str) -> pd.DataFrame:
    """Chain ends in the network, and all receiving activities, by end reason (END_REASONS order)."""
    out = []
    for group, sub in [("chain ends (in network)", ends[ends["in_network"]]), ("all receiving activities", ends)]:
        c = sub["end_reason"].value_counts().reindex(END_REASONS, fill_value=0)
        out.append(pd.DataFrame({
            "scope": scope, "group": group, "end_reason": c.index, "activities": c.to_numpy(),
            "percent": (100 * c / c.sum()).round(1).to_numpy() if c.sum() else np.nan,
        }))
    return pd.concat(out, ignore_index=True)


def end_reasons_by_year(ends: pd.DataFrame, act_years: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    """end_reasons_summary() for the activities with at least one transaction dated in
    each year (exact duplicates left out), as stage 5 selects activities for a year
    subset. act_years: one row per (iati_identifier, transaction_year). Scope labels
    as in indicators.csv ("all_2025"). Added November 2026 for the working paper."""
    return pd.concat(
        [end_reasons_summary(ends[ends["iati_identifier"].isin(
            act_years.loc[act_years["transaction_year"] == y, "iati_identifier"])], f"all_{y}") for y in years],
        ignore_index=True,
    )


def organisation_network(oe: pd.DataFrame) -> tuple[nx.DiGraph, pd.DataFrame, int]:
    """Directed organisation graph from the organisation links (org_edges), without
    the links of partner codes: identifiers a publisher gives its own partners, which
    are not organisation identifiers in the usual sense (common.is_partner_code(),
    config.PARTNER_CODE_PATTERN; decision of November 2026, code review M9).
    Returns (graph, the org_edges rows used, number of partner codes left out)."""
    p_from, p_to = common.is_partner_code(oe["from_org"]), common.is_partner_code(oe["to_org"])
    codes = set(oe.loc[p_from, "from_org"]) | set(oe.loc[p_to, "to_org"])
    used = oe[~(p_from | p_to).to_numpy()]
    g = nx.from_pandas_edgelist(used, "from_org", "to_org", create_using=nx.DiGraph)
    return g, used, len(codes)


CYCLE_END = "onward link set aside (inside a cycle)"


def chain_end_reason(end_activity: pd.Series, ends_r: pd.Series, in_cycle: set[str]) -> pd.Series:
    """Why each chain stops: the end_reason of its end activity (why_chain_stops()).
    An end activity whose onward links were all set aside because they lie inside a
    cycle has an onward link in the network, so why_chain_stops() gives it no reason;
    it gets CYCLE_END."""
    r = end_activity.map(ends_r)
    return r.where(r.notna() | ~end_activity.isin(in_cycle), CYCLE_END)


# =============================================================================
# Stage
# =============================================================================
def activity_countries(act: pd.DataFrame, tx: pd.DataFrame) -> pd.Series:
    """Recipient countries of each activity: activity level if given, otherwise all
    countries named on its transactions. ";"-separated, sorted."""
    t = tx.dropna(subset=["tx_recipient_countries"])
    t = t.assign(c=t["tx_recipient_countries"].str.split(";")).explode("c")
    from_tx = (
        t.drop_duplicates(["iati_identifier", "c"])
        .sort_values("c")
        .groupby("iati_identifier")["c"]
        .agg(";".join)
    )
    a = act.set_index("iati_identifier")["recipient_countries"]
    a = a.str.split(";").map(sorted, na_action="ignore").str.join(";")
    return a.fillna(from_tx.reindex(a.index))


def run(out_dir: Path) -> dict:
    """Run stage 4 on the stage 3 outputs in out_dir. Returns the stage summary."""
    log = common.setup_logging(out_dir, STAGE)
    t0 = time.time()
    s: dict = {}
    edges = pd.read_parquet(common.require_file(out_dir / "activity_edges.parquet", "03_links.py"))
    act = pd.read_parquet(
        out_dir / "activities_clean.parquet",
        columns=["iati_identifier", "reporting_org_ref", "publisher_group", "recipient_countries"],
    )
    org = pd.read_parquet(out_dir / "organisations.parquet").set_index("org_ref")

    # --- Network and cycles
    g = build_network(edges)
    groups, inside = find_cycles(g)
    candidate = edges[(config.INCLUDE_SAME_ORG_LINKS_IN_NETWORK | ~edges["same_org"])]
    g_all = nx.from_pandas_edgelist(candidate, "from_activity", "to_activity", create_using=nx.DiGraph)
    s["cycles_including_implausible_links"] = sum(
        1 for c in nx.strongly_connected_components(g_all) if len(c) > 1
    )
    s["cycles_after_excluding_implausible_links"] = len(groups)
    s["edges_inside_cycles_set_aside"] = len(inside)
    a_idx = act.set_index("iati_identifier")
    cyc = []
    for k, c in enumerate(sorted(groups, key=lambda c: (-len(c), min(c))), 1):
        members = sorted(c)
        sub = edges[
            edges["from_activity"].isin(members) & edges["to_activity"].isin(members) & edges["in_network"]
        ]
        cyc.append(
            {
                "cycle_id": k,
                "n_activities": len(members),
                "n_edges": len(sub),
                "activities": " | ".join(members),
                "orgs": " | ".join(sorted(a_idx.loc[members, "reporting_org_ref"].fillna("?").unique())),
                "edges": " | ".join(sub["from_activity"] + " > " + sub["to_activity"]),
                "evidence": " | ".join(sub["evidence"]),
            }
        )
    cycle_cols = ["cycle_id", "n_activities", "n_edges", "activities", "orgs", "edges", "evidence"]
    pd.DataFrame(cyc, columns=cycle_cols).to_csv(out_dir / "cycles.csv", index=False)
    dag = g.copy()
    dag.remove_edges_from(inside)
    assert nx.is_directed_acyclic_graph(dag)
    log.info(
        "Network: %d activities, %d edges; %d cycles (%d before excluding implausible links)",
        g.number_of_nodes(),
        g.number_of_edges(),
        len(groups),
        s["cycles_including_implausible_links"],
    )

    # --- Chains
    steps, total = enumerate_chains(dag, config.MAX_CHAINS)
    s["chains_total"] = total
    s["chains_listed"] = int(steps["chain_id"].nunique())
    if total > config.MAX_CHAINS:
        log.warning("%d chains; only the first %d are listed (config.MAX_CHAINS)", total, config.MAX_CHAINS)
    e_idx = edges.set_index(["from_activity", "to_activity"])
    cols = [
        "from_org",
        "from_org_name",
        "to_org",
        "to_org_name",
        "evidence",
        "transaction_types",
        "first_year",
        "last_year",
    ]
    steps = steps.join(e_idx[cols], on=["from_activity", "to_activity"])
    assert steps["evidence"].notna().all()
    link_tx = pd.read_parquet(out_dir / "link_transactions.parquet", columns=["side", "from_activity", "to_activity", "named_org_ref"])
    prefixes, legacy = common.load_codelists()
    pts = step_points(edges[edges["in_network"]], link_tx, prefixes, legacy)
    steps = steps.merge(pts, on=["from_activity", "to_activity"], how="left")
    assert steps["step_points"].notna().all()
    del link_tx

    tx = pd.read_parquet(
        out_dir / "transactions_clean.parquet",
        columns=[
            "iati_identifier",
            "transaction_type",
            "receiver_org_ref",
            "receiver_org_name",
            "receiver_activity_id",
            "counterpart_activity_published",
            "self_reference",
            "internal",
            "exact_duplicate",
            "tx_recipient_countries",
            "transaction_year",
        ],
    )
    tx = tx[~tx["exact_duplicate"].to_numpy()]
    year_list = sorted({y for c, y in config.SUBSETS if c is None and y is not None})
    act_years = tx.loc[tx["transaction_year"].isin(year_list).to_numpy(), ["iati_identifier", "transaction_year"]].drop_duplicates()
    countries = activity_countries(act[act["iati_identifier"].isin(list(g.nodes))], tx)

    # --- Why chains stop
    receives_tx = tx.loc[
        tx["transaction_type"].isin(config.flow_in_types()).to_numpy(), "iati_identifier"
    ].unique()
    receivers = pd.Index(sorted(set(receives_tx) | {v for v in g if g.in_degree(v) > 0}))
    with_onward = pd.Index([v for v in g if g.out_degree(v) > 0])
    any_out = pd.Index(edges["from_activity"].unique())
    ends = why_chain_stops(tx, receivers, with_onward, any_out, set(org.index))
    del tx
    ends["in_network"] = ends["iati_identifier"].isin(list(g.nodes))
    ends = ends.merge(
        act[["iati_identifier", "reporting_org_ref", "publisher_group"]], on="iati_identifier", how="left"
    )
    ends.to_parquet(out_dir / "chain_ends.parquet", index=False)
    summ = []
    for scope, sub in [
        ("chain ends (in network)", ends[ends["in_network"]]),
        ("all receiving activities", ends),
    ]:
        c = sub["end_reason"].value_counts().reindex(END_REASONS, fill_value=0)
        summ.append(
            pd.DataFrame(
                {
                    "scope": scope,
                    "end_reason": c.index,
                    "activities": c.to_numpy(),
                    "percent": (100 * c / c.sum()).round(1).to_numpy(),
                }
            )
        )
    pd.concat(summ).to_csv(out_dir / "chain_end_reasons.csv", index=False)
    end_reasons_by_year(ends, act_years, year_list).to_csv(out_dir / "chain_end_reasons_by_year.csv", index=False)
    s["receiving_activities_without_onward_link"] = len(ends)
    s["chain_ends_in_network"] = int(ends["in_network"].sum())

    # --- Chain table
    ends_r = ends.set_index("iati_identifier")["end_reason"]
    grp = act.set_index("iati_identifier")["publisher_group"]
    nodes_long = pd.concat(
        [
            steps.loc[steps["step"] == 1, ["chain_id", "from_activity"]]
            .set_axis(["chain_id", "activity"], axis=1)
            .assign(pos=0),
            steps[["chain_id", "to_activity", "step"]].set_axis(["chain_id", "activity", "pos"], axis=1),
        ]
    ).sort_values(["chain_id", "pos"])
    nodes_long["org"] = nodes_long["activity"].map(a_idx["reporting_org_ref"]).fillna("?")
    nodes_long["org_name"] = nodes_long["org"].map(org["org_name"]).fillna(nodes_long["org"])
    nodes_long["org_type"] = nodes_long["org"].map(org["org_type_label"]).fillna("Unknown")
    nodes_long["countries"] = nodes_long["activity"].map(countries)
    by = nodes_long.groupby("chain_id")
    chains = pd.DataFrame(
        {
            "steps": steps.groupby("chain_id").size(),
            "origin_activity": by["activity"].first(),
            "end_activity": by["activity"].last(),
            "path_activities": by["activity"].agg(" > ".join),
            "path_orgs": by["org_name"].agg(" > ".join),
            "path_org_refs": by["org"].agg(" > ".join),
            "path_org_types": by["org_type"].agg(" > ".join),
        }
    )
    rank = steps["evidence"].map(EVIDENCE_RANK)
    weakest = rank.groupby(steps["chain_id"]).min()
    chains["weakest_evidence"] = weakest.map({v: k for k, v in EVIDENCE_RANK.items()})
    chains["n_confirmed_steps"] = (steps["evidence"] == "confirmed").groupby(steps["chain_id"]).sum()
    cl = nodes_long.dropna(subset=["countries"])
    cl = cl.assign(c=cl["countries"].str.split(";")).explode("c").drop_duplicates(["chain_id", "c"])
    chains["countries"] = cl.sort_values("c").groupby("chain_id")["c"].agg(";".join)
    chains["first_year"] = steps.groupby("chain_id")["first_year"].min()
    chains["last_year"] = steps.groupby("chain_id")["last_year"].max()
    fy = chains["first_year"].astype("Int64").astype("string")
    ly = chains["last_year"].astype("Int64").astype("string")
    chains["years"] = fy.where(fy == ly, fy + "-" + ly)
    chains["origin_publisher_group"] = chains["origin_activity"].map(grp)
    chains["end_reason"] = chain_end_reason(chains["end_activity"], ends_r, {v for c in groups for v in c})
    chains["completeness"] = completeness(steps)
    chains = chains.reset_index()
    assert chains["completeness"].between(0, 1).all()
    assert chains["steps"].min() >= 1 and chains["chain_id"].is_unique
    assert chains["end_reason"].notna().all()
    chains.to_csv(out_dir / "chains.csv", index=False)
    steps.to_csv(out_dir / "chain_steps.csv", index=False)
    s["chains_by_weakest_evidence"] = chains["weakest_evidence"].value_counts().to_dict()
    s["chains_by_steps"] = depth_bin(chains["steps"]).value_counts().sort_index().to_dict()

    # --- Depth: per chain and per origin (the pilot's indicator 6 used origins)
    lf = longest_from(dag)
    origins = pd.DataFrame(
        {"origin_activity": sorted(v for v in dag if dag.in_degree(v) == 0 and dag.out_degree(v) > 0)}
    )
    origins["longest_steps"] = origins["origin_activity"].map(lf)
    origins["n_chains"] = (
        origins["origin_activity"].map(chains.groupby("origin_activity").size()).fillna(0).astype(int)
    )
    origins["origin_publisher_group"] = origins["origin_activity"].map(grp)
    origins.to_csv(out_dir / "chain_origins.csv", index=False)
    org_paths = organisation_paths(chains)
    org_paths.to_csv(out_dir / "org_paths.csv", index=False)
    mult = multipliers(org_paths)
    mult.to_csv(out_dir / "multipliers.csv", index=False)
    s["multipliers"] = len(mult)
    s["multipliers_by_type"] = mult["org_type"].fillna("unknown").value_counts().to_dict()
    s["organisation_paths"] = len(org_paths)
    s["organisation_paths_by_steps"] = depth_bin(org_paths["steps"]).value_counts().sort_index().to_dict()
    dist = []
    units = [
        ("chains", chains, "steps"),
        ("origins (longest chain)", origins, "longest_steps"),
        ("organisation paths", org_paths, "steps"),
    ]
    for unit, df, col in units:
        d = df.assign(steps_bin=depth_bin(df[col]))
        for group, sub in [("All publishers", d)] + list(d.groupby("origin_publisher_group")):
            c = sub["steps_bin"].value_counts().reindex(["1", "2", "3", "4+"], fill_value=0)
            dist.append(
                pd.DataFrame(
                    {
                        "unit": unit,
                        "origin_group": group,
                        "steps": c.index,
                        "n": c.to_numpy(),
                        "percent": (100 * c / c.sum()).round(1).to_numpy(),
                    }
                )
            )
    pd.concat(dist).to_csv(out_dir / "depth_distribution.csv", index=False)
    s["origins"] = len(origins)
    s["mean_longest_chain_per_origin"] = round(float(origins["longest_steps"].mean()), 2)
    s["longest_chain"] = int(origins["longest_steps"].max())

    # --- Activity nodes and components
    wcc = sorted(nx.weakly_connected_components(g), key=len, reverse=True)
    comp = {v: k for k, c in enumerate(wcc, 1) for v in c}
    nodes = pd.DataFrame({"iati_identifier": list(g.nodes)})
    nodes["in_degree"] = nodes["iati_identifier"].map(dict(g.in_degree()))
    nodes["out_degree"] = nodes["iati_identifier"].map(dict(g.out_degree()))
    nodes["component"] = nodes["iati_identifier"].map(comp)
    nodes["in_cycle"] = nodes["iati_identifier"].isin({v for c in groups for v in c})
    nodes["longest_steps_from_here"] = nodes["iati_identifier"].map(lf)
    nodes = nodes.merge(act, on="iati_identifier", how="left")
    nodes["countries"] = nodes["iati_identifier"].map(countries)
    nodes.to_parquet(out_dir / "network_nodes.parquet", index=False)
    s["activity_network"] = {
        "activities": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "components": len(wcc),
        "largest_components": [len(c) for c in wcc[:5]],
    }

    # --- Organisation network (without partner codes; see organisation_network())
    oe_all = pd.read_parquet(out_dir / "org_edges.parquet")
    og, oe, n_partner_codes = organisation_network(oe_all)
    owcc = sorted(nx.weakly_connected_components(og), key=len, reverse=True)
    ocomp = {v: k for k, c in enumerate(owcc, 1) for v in c}
    net = edges[edges["in_network"]]
    ag = nx.from_pandas_edgelist(
        net.dropna(subset=["from_org", "to_org"]), "from_org", "to_org", create_using=nx.DiGraph
    )
    acomp = {
        v: k
        for k, c in enumerate(sorted(nx.weakly_connected_components(ag), key=len, reverse=True), 1)
        for v in c
    }
    on = pd.DataFrame({"org_ref": list(og.nodes)})
    on["org_name"] = on["org_ref"].map(org["org_name"])
    on["org_type"] = on["org_ref"].map(org["org_type_label"])
    on["is_publisher"] = on["org_ref"].map(org["is_publisher"]).fillna(False).astype(bool)
    on["funds_n_orgs"] = on["org_ref"].map(dict(og.out_degree()))
    on["funded_by_n_orgs"] = on["org_ref"].map(dict(og.in_degree()))
    on["component"] = on["org_ref"].map(ocomp)
    on["in_activity_network"] = on["org_ref"].isin(list(ag.nodes))
    on["activity_network_component"] = on["org_ref"].map(acomp).astype("Int64")
    n_act = nodes.groupby("reporting_org_ref").size()
    on["activities_in_network"] = on["org_ref"].map(n_act).fillna(0).astype(int)
    on.to_parquet(out_dir / "org_nodes.parquet", index=False)
    on.to_csv(out_dir / "org_nodes.csv", index=False)
    s["org_network"] = {
        "organisations": og.number_of_nodes(),
        "edges": og.number_of_edges(),
        "components": len(owcc),
        "largest_components": [len(c) for c in owcc[:5]],
        "organisations_also_in_activity_network": int(on["in_activity_network"].sum()),
        "org_edges_also_traced_by_activity_link": int(oe["has_activity_link"].sum()),
        # Left out of the organisation network (config.PARTNER_CODE_PATTERN); still in org_edges
        "partner_codes_left_out": n_partner_codes,
        "edges_with_partner_codes_left_out": len(oe_all) - len(oe),
    }
    s["activity_network_projected_to_orgs"] = {
        "organisations": ag.number_of_nodes(),
        "edges": ag.number_of_edges(),
    }

    s["seconds"] = round(time.time() - t0)
    (out_dir / "network_summary.json").write_text(json.dumps(s, indent=1, default=str))
    common.write_summary(out_dir, STAGE, s)
    log.info("Done: %d chains, longest %d steps (%.0fs)", total, s["longest_chain"], time.time() - t0)
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", type=Path, default=None)
    a = ap.parse_args()
    run(a.out_dir or config.output_dir())


if __name__ == "__main__":
    main()
