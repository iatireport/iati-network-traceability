"""Checks each stage against the hand-made fixtures in tests/fixtures/bulk.

The fixture world (expected answers worked out by hand):
  donor  GB-GOV-1    A1  funds B1 and G1
  ngo    GB-CHC-111  B1  receives from A1, funds C1;  B2 (also in an older file)
  aggr.  (secondary) republishes B1
  galv   GB-COH-222  G1  receives from A1; implausible link back to A1; pays B2
  local  UG-NGB-333  C1  version 1 file; receives from B1
  broken             cut-off file (excluded)

Run from the project folder:  .venv/bin/python -m pytest
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BULK = HERE / "fixtures" / "bulk"


def load_stage(filename: str):
    """Import a stage script (its name starts with a digit, so `import` cannot)."""
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), ROOT / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # lets the parallel workers find the module
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def out(tmp_path_factory) -> Path:
    """Run the stages once on the fixtures; every test reads the outputs."""
    d = tmp_path_factory.mktemp("out")
    load_stage("01_parse_bulk.py").run(BULK, d, n_workers=2)
    load_stage("02_clean.py").run(d)
    load_stage("03_links.py").run(d)
    load_stage("04_network.py").run(d)
    load_stage("05_indicators.py").run(d)
    load_stage("06_spatial.py").run(d)
    return d


# =============================================================================
# Stage 1
# =============================================================================
def test_stage1_counts(out):
    s = pd.read_json(out / "summaries" / "01_parse_bulk.json", typ="series")
    assert s["xml_files_found"] == 8
    assert s["organisation_files"] == 1
    assert s["version_1_activity_files"] == 1
    assert s["files_failed"] == 1
    assert s["transactions_read_all_files"] == 21  # 19 kept + 2 in the broken file
    assert s["rows_transactions"] == 19
    assert s["rows_activities"] == 7
    assert s["datasets_missing_from_copy"] == 1


def test_stage1_recoding(out):
    tx = pd.read_parquet(out / "transactions.parquet")
    v1 = tx[tx["iati_identifier"] == "UG-NGB-333-C1"].sort_values("tx_seq")
    assert list(v1["transaction_type"]) == ["1", "4", "3"]  # IF, E, D
    assert set(v1["type_recode"]) == {"v1 code"}
    a1 = tx[tx["iati_identifier"] == "GB-GOV-1-A1"].sort_values("tx_seq")
    assert list(a1["type_recode"]) == ["as published"] * 3 + ["name", "invalid"]
    assert a1["transaction_type"].iloc[3] == "3"  # "Disbursement"
    assert pd.isna(a1["transaction_type"].iloc[4])  # "2005-03-31"


def test_stage1_all_type_codes():
    """Every version 1 code and type name is recoded; sale of equity (9) is incoming."""
    import config

    s1 = load_stage("01_parse_bulk.py")
    v1 = ["IF", "C", "D", "E", "IR", "LR", "R", "QP", "QS", "CG", "d", " D ", "Sale of equity", "14", None]
    got = [s1.recode_transaction_type(x) for x in v1]
    assert [c for c, _ in got] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "3",
        "3",
        "9",
        None,
        None,
    ]
    assert [h for _, h in got][-3:] == ["name", "invalid", "missing"]
    assert "9" in config.INCOMING and "9" not in config.OUTGOING
    assert not (config.INCOMING & config.OUTGOING)


def test_stage1_defaults(out):
    tx = pd.read_parquet(out / "transactions.parquet")
    a1 = tx[tx["iati_identifier"] == "GB-GOV-1-A1"].sort_values("tx_seq")
    assert list(a1["currency"].iloc[:3]) == ["GBP", "USD", "GBP"]
    assert list(a1["currency_source"].iloc[:2]) == ["activity default", "transaction"]
    assert list(a1["finance_type"].iloc[:2]) == ["110", "410"]  # default, then own
    assert set(a1["flow_type"]) == {"10"}


def test_stage1_names(out):
    act = pd.read_parquet(out / "activities.parquet").set_index("copy_id")
    tx = pd.read_parquet(out / "transactions.parquet")
    # English narrative chosen over the French one listed first
    a1 = act[act["iati_identifier"] == "GB-GOV-1-A1"].iloc[0]
    assert a1["reporting_org_name"] == "Donor Ministry"
    # Version 1: name is the element text
    c1 = act[act["iati_identifier"] == "UG-NGB-333-C1"].iloc[0]
    assert c1["reporting_org_name"] == "Local Partner" and c1["title"] == "Schools C1"
    assert c1["start_date"] == "2025-03-01" and c1["end_date"] == "2025-12-31"
    v1 = tx[tx["iati_identifier"] == "UG-NGB-333-C1"].sort_values("tx_seq")
    assert v1["receiver_org_name"].iloc[1] == "Teachers"


def test_stage1_namespaced_file_is_reported(tmp_path):
    """A file whose root element is in an XML namespace is not read. It must be
    reported as failed, not passed as "ok" with no activities."""
    s1 = load_stage("01_parse_bulk.py")
    f = tmp_path / "pub" / "pub-activities.xml"
    f.parent.mkdir()
    f.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<iati-activities version="2.03" xmlns="http://iatistandard.org/activity-schema">'
        "<iati-activity><iati-identifier>XX-1-A</iati-identifier>"
        '<transaction><transaction-type code="3"/></transaction></iati-activity>'
        "</iati-activities>"
    )
    info = s1.parse_file(f, 0, tmp_path)
    assert info["status"] == "failed" and info["activities"] == 0
    assert "iatistandard.org/activity-schema" in info["error"]
    assert not list(tmp_path.glob("*.parquet"))  # nothing written


def test_stage1_secondary_and_files(out):
    act = pd.read_parquet(out / "activities.parquet")
    agg = act[act["publisher_folder"] == "aggregator"].iloc[0]
    assert bool(agg["secondary_reporter"]) is True
    assert not act.loc[act["publisher_folder"] == "ngo", "secondary_reporter"].any()
    assert (act["iati_identifier"] == "GB-CHC-555-Z1").sum() == 0  # broken file excluded
    rel = pd.read_parquet(out / "related_activities.parquet")
    assert len(rel) == 2 and set(rel["iati_identifier"]) == {"GB-CHC-111-B2"}


# =============================================================================
# Stage 2
# =============================================================================
def test_stage2_one_copy_per_activity(out):
    act = pd.read_parquet(out / "activities_clean.parquet")
    assert len(act) == 5 and act["iati_identifier"].is_unique
    kept = act.set_index("iati_identifier")
    assert kept.loc["GB-CHC-111-B1", "publisher_folder"] == "ngo"  # primary over secondary
    assert kept.loc["GB-CHC-111-B2", "file"] == "ngo-activities"  # newer over older
    rem = pd.read_csv(out / "duplicates_removed.csv").set_index("iati_identifier")
    assert rem.loc["GB-CHC-111-B1", "reason"] == "secondary reporter"
    assert rem.loc["GB-CHC-111-B2", "reason"] == "older last-updated-datetime"
    tx = pd.read_parquet(out / "transactions_clean.parquet")
    assert len(tx) == 17  # 19 minus one transaction in each removed copy


def test_stage2_identifiers_and_checks(out):
    tx = pd.read_parquet(out / "transactions_clean.parquet")
    a1 = tx[tx["iati_identifier"] == "GB-GOV-1-A1"].set_index("tx_seq")
    assert a1.loc[4, "receiver_org_ref"] == "GB-COH-222"  # " gb-coh-222 " normalised
    assert bool(a1.loc[4, "receiver_org_ref_changed"])
    assert a1.loc[1, "direction"] == "out" and a1.loc[5, "direction"] == "other"
    assert bool(a1.loc[1, "counterpart_activity_published"])
    c1 = tx[tx["iati_identifier"] == "UG-NGB-333-C1"].set_index("tx_seq")
    assert c1.loc[1, "direction"] == "in" and c1.loc[1, "counterpart_ref"] == "GB-CHC-111"
    assert not bool(c1.loc[3, "counterpart_activity_published"])  # UG-NGB-444-X9
    assert bool(c1.loc[3, "valid_counterpart_ref"])  # UG-NGB is a registered prefix
    b1 = tx[tx["iati_identifier"] == "GB-CHC-111-B1"].set_index("tx_seq")
    assert list(b1["exact_duplicate"]) == [False, False, False, False, True]
    assert tx["exact_duplicate"].sum() == 1
    assert not tx["internal"].any()


def test_stage2_organisations(out):
    org = pd.read_parquet(out / "organisations.parquet").set_index("org_ref")
    assert org.loc["GB-GOV-1", "org_name"] == "Donor Ministry"
    assert bool(org.loc["GB-COH-222", "is_publisher"])
    assert not bool(org.loc["GB-CHC-999", "is_publisher"])
    assert org.loc["GB-CHC-111", "n_activities_published"] == 2


# =============================================================================
# Stage 3
# =============================================================================
def edges_of(out) -> pd.DataFrame:
    return pd.read_parquet(out / "activity_edges.parquet").set_index(["from_activity", "to_activity"])


def test_stage3_activity_edges(out):
    e = edges_of(out)
    assert len(e) == 5
    a1b1 = e.loc[("GB-GOV-1-A1", "GB-CHC-111-B1")]
    assert a1b1["evidence"] == "confirmed"
    assert a1b1["n_transactions"] == 3 and a1b1["transaction_types"] == "1;2;3"
    assert a1b1["provider_value_by_currency"] == "type 2 GBP 1000000; type 3 USD 400000"
    assert a1b1["receiver_value_by_currency"] == "type 1 USD 400000"
    assert (a1b1["first_year"], a1b1["last_year"]) == (2024, 2025)
    assert e.loc[("GB-GOV-1-A1", "GB-COH-222-G1"), "evidence"] == "confirmed"
    assert e.loc[("GB-CHC-111-B1", "UG-NGB-333-C1"), "evidence"] == "confirmed"  # v1 receiver
    assert e.loc[("GB-CHC-111-B1", "UG-NGB-333-C1"), "to_org_name"] == "Local Partner"


def test_stage3_one_sided_and_implausible(out):
    e = edges_of(out)
    g1b2 = e.loc[("GB-COH-222-G1", "GB-CHC-111-B2")]
    assert g1b2["evidence"] == "one-sided" and bool(g1b2["plausible"])
    assert g1b2["plausibility"] == "org matches"
    galv = e.loc[("GB-COH-222-G1", "GB-GOV-1-A1")]
    assert galv["plausibility"] == "reverses own funding link"
    assert galv["evidence"] == "implausible" and not bool(galv["in_network"])
    assert e["in_network"].sum() == 4


def test_stage3_org_check_rules():
    """Each org check, including a bare agency prefix and a country code, which name
    no organisation and so are not a match by the "starts with" test."""
    s3 = load_stage("03_links.py")
    act = pd.DataFrame(
        {
            "iati_identifier": ["XM-DAC-7-1", "GB-GOV-1-A1"],
            "reporting_org_ref": ["XM-DAC-7", "GB-GOV-1"],
            "publisher_folder": ["nl", "uk"],
        }
    )
    aliases = pd.DataFrame(
        {"publisher_folder": ["nl", "uk", "uk"], "alias": ["XM-DAC-7", "GB-GOV-1", "GB-1"]}
    )
    named = ["XM-DAC-7", "7", "XM-DAC", "GB", "GB-1", "FCDO", None, "XM-DAC-7"]
    linked = ["XM-DAC-7-1", "XM-DAC-7-1", "XM-DAC-7-1", "GB-GOV-1-A1", "GB-GOV-1-A1", "GB-GOV-1-A1"]
    linked += ["GB-GOV-1-A1", "GB-GOV-1-A1"]
    links = pd.DataFrame({"named_org_ref": named, "linked_activity": linked}, dtype="string")
    got = s3.check_named_org(links, act, aliases, {"XM-DAC", "GB-GOV"}).tolist()
    assert got == [
        "match",  # the publisher's own ref
        "match",  # bare DAC code 7 -> XM-DAC-7
        "unrecognised ref",  # bare agency prefix (before the review: "match")
        "unrecognised ref",  # country code only (before the review: "match")
        "match",  # another ref used in the publisher's folder
        "unrecognised ref",  # a name
        "no org ref",
        "other publisher",  # the Netherlands named on a UK activity
    ]


def test_stage3_same_organisation():
    s3 = load_stage("03_links.py")
    e = pd.DataFrame(
        {
            "from_org": ["GB-1", "GB-1", None, None],
            "to_org": ["GB-1", "NL-1", None, "NL-1"],
            "from_publisher_folder": ["a", "a", "c", "d"],
            "to_publisher_folder": ["b", "a", "c", "e"],
        },
        dtype="string",
    )
    # same ref; different refs in one folder; refs missing in one folder; missing, two folders
    assert s3.same_organisation(e).tolist() == [True, False, True, False]


def test_stage3_unreadable_values_are_not_zero():
    """An edge whose values are all unreadable shows NA, not 0; readable values are
    summed per side, type and currency, never across them."""
    s3 = load_stage("03_links.py")
    links = pd.DataFrame(
        {
            "from_activity": ["A", "A", "A", "B"],
            "to_activity": ["X", "X", "X", "Y"],
            "side": ["provider"] * 4,
            "transaction_type": ["3", "3", "2", "3"],
            "currency": ["USD", "USD", "USD", "EUR"],
            "value": [100.0, 50.0, 70.0, None],
        }
    )
    long, wide = s3.values_by_currency(links, ["from_activity", "to_activity"])
    assert wide.loc[("A", "X"), "provider_value_by_currency"] == "type 2 USD 70; type 3 USD 150"
    assert wide.loc[("B", "Y"), "provider_value_by_currency"] == "type 3 EUR NA"
    assert pd.isna(long.loc[long["from_activity"] == "B", "value"]).all()


def test_stage3_org_edges(out):
    o = pd.read_parquet(out / "org_edges.parquet").set_index(["from_org", "to_org"])
    assert len(o) == 6
    d = o.loc[("GB-GOV-1", "GB-COH-222")]
    assert d["n_transactions"] == 3  # 2 from the donor (one recoded from a name) + 1 from G1
    assert bool(d["reported_by_provider"]) and bool(d["reported_by_receiver"])
    assert d["n_activity_links"] == 1
    assert not bool(o.loc[("GB-CHC-111", "GB-CHC-999"), "has_activity_link"])
    assert not bool(o.loc[("GB-CHC-111", "GB-CHC-999"), "to_is_publisher"])


# =============================================================================
# Stage 4
# =============================================================================
def test_stage4_chains(out):
    ch = pd.read_csv(out / "chains.csv").set_index("path_activities")
    assert len(ch) == 2
    c1 = ch.loc["GB-GOV-1-A1 > GB-CHC-111-B1 > UG-NGB-333-C1"]
    assert c1["steps"] == 2 and c1["weakest_evidence"] == "confirmed"
    assert c1["path_orgs"] == "Donor Ministry > Intermediary NGO > Local Partner"
    assert c1["countries"] == "UG" and c1["years"] == "2024-2025"
    assert c1["end_reason"] == "activity ID given but not published"
    c2 = ch.loc["GB-GOV-1-A1 > GB-COH-222-G1 > GB-CHC-111-B2"]
    assert c2["weakest_evidence"] == "one-sided" and c2["countries"] == "KE;UG"
    assert c2["end_reason"] == "receiver name only"
    steps = pd.read_csv(out / "chain_steps.csv")
    assert len(steps) == 4


def test_stage4_cycles_and_depth(out):
    s = pd.read_json(out / "network_summary.json", typ="series")
    assert s["cycles_including_implausible_links"] == 1  # A1 <-> G1 via the GALVmed-style link
    assert s["cycles_after_excluding_implausible_links"] == 0
    assert s["origins"] == 1 and s["longest_chain"] == 2
    d = pd.read_csv(out / "depth_distribution.csv")
    row = d[(d["unit"] == "chains") & (d["origin_group"] == "All publishers") & (d["steps"] == "2")]
    assert row["n"].item() == 2
    op = pd.read_csv(out / "org_paths.csv")
    assert len(op) == 2 and set(op["best_evidence"]) == {"confirmed", "one-sided"}
    assert set(d["unit"]) == {"chains", "origins (longest chain)", "organisation paths"}


def test_stage4_cycles_chain_limit_and_cycle_ends():
    """A -> B, with B <-> C a cycle; A -> D -> E and A -> F as other routes."""
    import networkx as nx

    s4 = load_stage("04_network.py")
    g = nx.DiGraph([("A", "B"), ("B", "C"), ("C", "B"), ("A", "D"), ("D", "E"), ("A", "F")])
    groups, inside = s4.find_cycles(g)
    assert groups == [{"B", "C"}] and sorted(inside) == [("B", "C"), ("C", "B")]
    dag = g.copy()
    dag.remove_edges_from(inside)
    steps, total = s4.enumerate_chains(dag, max_chains=10)
    assert total == 3 and steps["chain_id"].nunique() == 3
    ends = steps.groupby("chain_id")["to_activity"].last()
    assert sorted(ends) == ["B", "E", "F"]
    # The limit stops the list, not the count, and not the per-origin depth
    steps2, total2 = s4.enumerate_chains(dag, max_chains=2)
    assert total2 == 3 and steps2["chain_id"].nunique() == 2
    assert s4.longest_from(dag)["A"] == 2
    # B ends a chain but has an onward link (inside the cycle), so it has no end reason
    ends_r = pd.Series({"E": "receiver name only", "F": "no receiver named"})
    r = s4.chain_end_reason(pd.Series(["B", "E", "F", "Z"]), ends_r, {"B", "C"})
    assert r.tolist()[:3] == [s4.CYCLE_END, "receiver name only", "no receiver named"]
    assert pd.isna(r.iloc[3])  # not in a cycle and no reason: stays missing (stage 4 asserts)


def test_stage4_partner_codes_left_out():
    """UNICEF's partner codes stay in org_edges but are left out of the org network."""
    import common

    s4 = load_stage("04_network.py")
    refs = pd.Series(["XM-DAC-41122-VN-123", "XM-DAC-41122", "GB-CHC-1", None])
    assert common.is_partner_code(refs).tolist() == [True, False, False, False]
    oe = pd.DataFrame(
        {
            "from_org": ["XM-DAC-41122", "XM-DAC-41122", "XM-DAC-41122", "GB-GOV-1"],
            "to_org": ["XM-DAC-41122-VN-1", "XM-DAC-41122-VN-2", "GB-CHC-1", "XM-DAC-41122"],
        }
    )
    g, used, n = s4.organisation_network(oe)
    assert n == 2 and len(used) == 2
    assert sorted(g.nodes) == ["GB-CHC-1", "GB-GOV-1", "XM-DAC-41122"]


def test_stage4_multipliers():
    """Hand-worked: three organisation paths.
    1: F > M > A > B      M: next A, downstream A, B; upstream F.  A: next B, down B, up F, M
    2: F > M > C          M: next C, downstream C; upstream F
    3: G > M > D > M > E  M twice on one path: counted once; next D and E; downstream D, E
                          (not itself); upstream G, D.  D: next M, down M, E; up G, M
    So M: 3 paths, direct A, C, D, E (4), downstream A, B, C, D, E (5), upstream F, G, D (3)."""
    s4 = load_stage("04_network.py")
    paths = pd.DataFrame({
        "org_path_id": [1, 2, 3],
        "path_org_refs": ["F > M > A > B", "F > M > C", "G > M > D > M > E"],
        "path_orgs": ["Funder > Multi > Aa > Bb", "Funder > Multi > Cc", "Gg > Multi > Dd > Multi > Ee"],
        "path_org_types": ["Government > International NGO > National NGO > National NGO",
                           "Government > International NGO > National NGO",
                           "Government > International NGO > National NGO > International NGO > National NGO"],
    })
    m = s4.multipliers(paths).set_index("org_ref")
    assert set(m.index) == {"M", "A", "D"}
    assert m.loc["M", ["paths_through", "direct_recipients", "downstream_orgs", "upstream_orgs"]].tolist() == [3, 4, 5, 3]
    assert m.loc["A", ["paths_through", "direct_recipients", "downstream_orgs", "upstream_orgs"]].tolist() == [1, 1, 1, 2]
    assert m.loc["D", ["paths_through", "direct_recipients", "downstream_orgs", "upstream_orgs"]].tolist() == [1, 1, 2, 2]
    assert m.loc["M", "org_name"] == "Multi" and m.loc["M", "org_type"] == "International NGO"
    assert m.index[0] == "M"  # sorted by paths through


def test_stage4_multipliers_fixture(out):
    # Fixture chains A1 > B1 > C1 (GB-GOV-1 > GB-CHC-111 > UG-NGB-333) and A1 > G1 > B2
    # (GB-GOV-1 > GB-COH-222 > GB-CHC-111): one intermediary on each, each with one path,
    # one recipient, one downstream and one upstream organisation
    m = pd.read_csv(out / "multipliers.csv").set_index("org_ref")
    assert sorted(m.index) == ["GB-CHC-111", "GB-COH-222"]
    for r in m.index:
        assert m.loc[r, ["paths_through", "direct_recipients", "downstream_orgs", "upstream_orgs"]].tolist() == [1, 1, 1, 1]


def test_stage4_end_reasons_by_year():
    """Hand-worked: four receiving activities without onward link.
    P (in network, name only) has transactions in 2024 and 2025; Q (in network, no
    receiver named) in 2025 only; R (not in network, name only) in 2024; S (in network,
    no outgoing) in 2023 only.
    2025: chain ends P, Q (1 name only, 1 no receiver named); all receiving P, Q.
    2024: chain ends P; all receiving P, R (2 name only)."""
    s4 = load_stage("04_network.py")
    ends = pd.DataFrame({
        "iati_identifier": ["P", "Q", "R", "S"],
        "end_reason": ["receiver name only", "no receiver named", "receiver name only", "no outgoing transactions"],
        "in_network": [True, True, False, True],
    })
    act_years = pd.DataFrame({"iati_identifier": ["P", "P", "Q", "R", "S"], "transaction_year": [2024, 2025, 2025, 2024, 2023]})
    r = s4.end_reasons_by_year(ends, act_years, [2024, 2025]).set_index(["scope", "group", "end_reason"])["activities"]
    assert r[("all_2025", "chain ends (in network)", "receiver name only")] == 1
    assert r[("all_2025", "chain ends (in network)", "no receiver named")] == 1
    assert r[("all_2024", "chain ends (in network)", "receiver name only")] == 1
    assert r[("all_2024", "all receiving activities", "receiver name only")] == 2
    assert r[("all_2024", "chain ends (in network)", "no outgoing transactions")] == 0
    assert r.groupby(level=[0, 1]).sum().tolist() == [2, 1, 2, 2]  # 2024 all, 2024 ends, 2025 all, 2025 ends


def test_stage4_completeness_score():
    """Hand-worked: three links.
    A>B: funder reports it naming GB-COH-123 (well formed); receiver confirms: 3 points.
    B>C: only the receiver reports it (its provider ref does not count for point 1): 1 point.
    C>D: funder reports it, naming the receiver by a name in the ref field: 1 point.
    Chain 1 = A>B>C: (3 + 1) / (3 x 2) = 0.667. Chain 2 = C>D: 1 / 3 = 0.333."""
    s4 = load_stage("04_network.py")
    import common
    edges = pd.DataFrame({"from_activity": ["A", "B", "C"], "to_activity": ["B", "C", "D"],
                          "reported_by_provider": [True, False, True], "reported_by_receiver": [True, True, False]})
    link_tx = pd.DataFrame({"side": ["provider", "receiver", "receiver", "provider"],
                            "from_activity": ["A", "A", "B", "C"], "to_activity": ["B", "B", "C", "D"],
                            "named_org_ref": ["GB-COH-123", "GB-GOV-1", "GB-CHC-1", "Some Partner"]})
    prefixes, legacy = common.load_codelists()
    pts = s4.step_points(edges, link_tx, prefixes, legacy).set_index(["from_activity", "to_activity"])
    assert pts["step_points"].tolist() == [3, 1, 1]
    assert pts["receiver_ref_well_formed"].tolist() == [True, False, False]
    steps = pd.DataFrame({"chain_id": [1, 1, 2], "from_activity": ["A", "B", "C"], "to_activity": ["B", "C", "D"]})
    steps = steps.merge(pts.reset_index(), on=["from_activity", "to_activity"])
    sc = s4.completeness(steps)
    assert sc.loc[1] == 0.667 and sc.loc[2] == 0.333


def test_stage4_completeness_fixture(out):
    ch = pd.read_csv(out / "chains.csv")
    assert ch["completeness"].between(0, 1).all()
    st = pd.read_csv(out / "chain_steps.csv")
    assert set(st["step_points"]) <= {0, 1, 2, 3}


def test_stage4_org_network(out):
    on = pd.read_parquet(out / "org_nodes.parquet").set_index("org_ref")
    assert len(on) == 6
    assert on.loc["GB-CHC-111", "funds_n_orgs"] == 2  # UG-NGB-333 and GB-CHC-999
    assert not bool(on.loc["GB-CHC-999", "in_activity_network"])
    assert on.loc["GB-CHC-111", "activities_in_network"] == 2


# =============================================================================
# Stage 5
# =============================================================================
def indicator(out, scope: str, code: str, group: str = "All publishers") -> pd.Series:
    ind = pd.read_csv(out / "indicators.csv", dtype={"indicator": str})
    r = ind[(ind["scope"] == scope) & (ind["group"] == group) & (ind["indicator"] == code)]
    assert len(r) == 1, (scope, code, group)
    return r.iloc[0]


def test_stage5_global_transaction_indicators(out):
    # (numerator, denominator); exact duplicate left out, so 16 transactions
    expected = {
        "2": (3, 3),
        "2b": (0, 3),
        "3": (8, 10),
        "3b": (0, 10),
        "4": (9, 11),
        "5": (4, 6),
        "x1": (1, 10),
    }
    for code, (num, den) in expected.items():
        r = indicator(out, "global", code)
        assert (r["numerator"], r["denominator"]) == (num, den), code
        assert r["unit"] == "transactions"


def test_stage5_global_activity_and_network_indicators(out):
    assert (indicator(out, "global", "1")[["numerator", "denominator"]] == [5, 5]).all()
    assert indicator(out, "global", "7b")["unit"] == "organisations"
    assert indicator(out, "global", "6")["value"] == 2.0
    assert indicator(out, "global", "6e")["denominator"] == 2
    assert (indicator(out, "global", "D1")[["numerator", "denominator"]] == [1, 17]).all()
    assert (indicator(out, "global", "D1a")[["numerator", "denominator"]] == [2, 5]).all()
    assert (indicator(out, "global", "D2")[["numerator", "denominator"]] == [9, 9]).all()


def test_stage5_d2g_traced_by_group(out):
    # Traced transactions (published counterpart activity, not internal, not itself, no exact
    # duplicate) by publisher group: A1 (Government) 3; G1 and B1 (International NGO) 3 + 2;
    # C1 (National NGO) 1. Denominator: all 9 traced transactions, as in D2.
    expected = {"All publishers": 9, "Government": 3, "International NGO": 5, "National NGO": 1}
    for group, num in expected.items():
        r = indicator(out, "global", "D2g", group)
        assert (r["numerator"], r["denominator"]) == (num, 9), group
        assert r["unit"] == "transactions"


def test_stage5_indicators_by_publisher(out):
    # Publishers' numerators and denominators add up to the "All publishers" row, for
    # every transaction indicator and scope; the fixture has 4 reporting organisations.
    bp = pd.read_csv(out / "indicators_by_publisher.csv", dtype={"indicator": str})
    ind = pd.read_csv(out / "indicators.csv", dtype={"indicator": str})
    tot = bp.groupby(["scope", "indicator"])[["numerator", "denominator"]].sum()
    ref = ind[ind["group"] == "All publishers"].set_index(["scope", "indicator"]).loc[tot.index, ["numerator", "denominator"]]
    assert (tot.values == ref.values).all()
    assert bp.loc[bp["scope"] == "global", "reporting_org_ref"].nunique() == 4
    # Each row carries the publisher group of its reporting organisation
    r = bp[(bp["scope"] == "global") & (bp["indicator"] == "4") & (bp["reporting_org_ref"] == "GB-GOV-1")].iloc[0]
    assert r["numerator"] <= r["denominator"] and r["publisher_group"] == "Government"


def test_stage5_tentative_6f(out):
    # Fixture chains: A1 > B1 > C1, every step funder-reported with a well-formed receiver ref and
    # confirmed: 1.0. A1 > G1 > B2: G1 > B2 is not confirmed (B2 has only an expenditure): 5/6.
    # 6f = mean over the 2 chains, about 0.917 (rounded from the chains' three-decimal scores).
    r = indicator(out, "global", "6f")
    assert r["denominator"] == 2 and r["unit"] == "chains"
    assert abs(r["value"] - (1 + 5 / 6) / 2) < 0.001


def test_stage5_indicator3_by_publisher(out):
    # Default: publishers add up to indicator 3 for all publishers (8 of 10). Disbursements only:
    # GB-CHC-111 (B1) has three type-3 transactions once its exact duplicate is left out.
    i3 = pd.read_csv(out / "indicator3_by_publisher.csv").set_index("reporting_org_ref")
    r = indicator(out, "global", "3")
    assert (i3["valid"].sum(), i3["outgoing"].sum()) == (r["numerator"], r["denominator"])
    assert i3.loc["GB-CHC-111", "disb_outgoing"] == 3
    assert (i3["disb_outgoing"] <= i3["outgoing"]).all() and (i3["disb_valid"] <= i3["disb_outgoing"]).all()
    assert i3["has_disbursement"].equals(i3["disb_outgoing"] > 0)


def test_stage5_subset(out):
    # UG 2025: A1's 2024 commitment and B2 (Kenya only) drop out
    assert (indicator(out, "UG_2025", "1")[["numerator", "denominator"]] == [4, 4]).all()
    assert (indicator(out, "UG_2025", "3")[["numerator", "denominator"]] == [7, 9]).all()


def test_stage5_expenditure_setting(out, monkeypatch):
    import config

    mod = load_stage("05_indicators.py")
    tx = pd.read_parquet(out / "transactions_clean.parquet", columns=mod.TX_COLS)
    edges = pd.read_parquet(out / "activity_edges.parquet")
    tx = tx[~tx["exact_duplicate"]]
    assert mod.flag_transactions(tx.copy(), edges, set())["d3"].sum() == 10
    monkeypatch.setattr(config, "INDICATOR3_INCLUDE_EXPENDITURE", True)
    assert mod.flag_transactions(tx.copy(), edges, set())["d3"].sum() == 12  # + 2 expenditure


def test_stage5_publisher_ids_not_in_codelist():
    """Indicators 2b and 3b: refs that fail the codelist rule but are a publisher's own
    identifier (US-GOV-1). Valid refs, unknown refs and internal flows are not counted."""
    s5 = load_stage("05_indicators.py")
    tx = pd.DataFrame(
        {
            "transaction_type": ["1", "1", "1", "3", "3", "3", "3"],
            "direction": ["in"] * 3 + ["out"] * 4,
            "counterpart_ref": ["GB-GOV-1", "US-GOV-1", "FCDO", "GB-GOV-1", "US-GOV-1", "US-GOV-1", "XX"],
            "valid_provider_ref": [True, False, False, False, False, False, False],
            "valid_receiver_ref": [False, False, False, True, False, False, False],
            "internal": [False] * 5 + [True, False],
            "counterpart_activity_id": [None] * 7,
            "counterpart_activity_published": [False] * 7,
            "self_reference": [False] * 7,
            "iati_identifier": list("ABCDEFG"),
            "receiver_activity_id": [None] * 7,
        }
    )
    edges = pd.DataFrame({"from_activity": [], "to_activity": [], "reported_by_receiver": []}, dtype=object)
    f = s5.flag_transactions(tx, edges, publishers={"GB-GOV-1", "US-GOV-1"})
    assert f["n2"].tolist()[:3] == [True, False, False] and f["n2b"].tolist()[:3] == [False, True, False]
    assert f["d3"].tolist()[3:] == [True, True, False, True]  # the internal one is not outgoing to others
    assert f["n3"].tolist()[3:] == [True, False, False, False]
    assert f["n3b"].tolist()[3:] == [False, True, False, False]


def test_disbursements_only_rebuild(out, tmp_path, monkeypatch):
    """Stages 3-5 rerun with disbursements only, in a separate folder that holds
    only the stage 2 files listed in report/sensitivity.py (as the sensitivity
    check does). A1's commitment to B1 drops out of the link and the indicators."""
    import config

    sys.path.insert(0, str(ROOT / "report"))
    import sensitivity

    for f in sensitivity.STAGE2_FILES:
        (tmp_path / f).symlink_to(out / f)
    monkeypatch.setattr(config, "DISBURSEMENTS_ONLY", True)
    for stage in ("03_links.py", "04_network.py", "05_indicators.py"):
        load_stage(stage).run(tmp_path)

    e = pd.read_parquet(tmp_path / "activity_edges.parquet").set_index(["from_activity", "to_activity"])
    assert e.loc[("GB-GOV-1-A1", "GB-CHC-111-B1"), "transaction_types"] == "1;3"  # default "1;2;3"
    assert indicator(tmp_path, "global", "3")[["numerator", "denominator"]].tolist() == [7, 9]
    assert indicator(tmp_path, "global", "5")[["numerator", "denominator"]].tolist() == [3, 5]
    assert indicator(tmp_path, "global", "1")[["numerator", "denominator"]].tolist() == [5, 5]
    # the default outputs are unchanged
    assert indicator(out, "global", "3")[["numerator", "denominator"]].tolist() == [8, 10]


def test_sensitivity_restores_defaults_after_failure(out, tmp_path, monkeypatch):
    """If an alternative setting fails, report/sensitivity.py still reruns stage 5 with
    the settings of config.py, so indicators.csv is never left under an alternative."""
    import json
    import shutil

    import config

    sys.path.insert(0, str(ROOT / "report"))
    import sensitivity

    work = tmp_path / "out"
    shutil.copytree(out, work)
    monkeypatch.setenv("CH6_OUTPUT_DIR", str(work))
    bad = ("Broken alternative", {"INDICATOR1_RELATED_ACTIVITIES": "not a mode"}, False)
    monkeypatch.setattr(sensitivity, "ALTERNATIVES", [sensitivity.ALTERNATIVES[1], bad])
    with pytest.raises(AssertionError):
        sensitivity.main()
    settings = json.loads((work / "indicator_settings.json").read_text())
    assert settings["INDICATOR1_RELATED_ACTIVITIES"] == config.INDICATOR1_RELATED_ACTIVITIES == "none"
    assert settings["INDICATOR3_INCLUDE_EXPENDITURE"] is False
    assert indicator(work, "global", "3")[["numerator", "denominator"]].tolist() == [8, 10]


def test_read_csv_keeps_namibia(tmp_path):
    """ "NA" is Namibia's country code; only empty cells are missing."""
    import common

    f = tmp_path / "chains.csv"
    pd.DataFrame({"chain_id": [1, 2, 3], "countries": ["NA", "NA;ZA", None]}).to_csv(f, index=False)
    c = common.read_csv(f)
    assert c["countries"].tolist()[:2] == ["NA", "NA;ZA"] and pd.isna(c["countries"].iloc[2])
    assert pd.read_csv(f)["countries"].isna().sum() == 2  # the pandas default loses Namibia


def test_org_id_as_activity_id_rule():
    import common

    ids = pd.Series(["US-GOV-1", " us-gov-1 ", "GB-GOV-1-A1", "UG-NGB-444-X9", None])
    published = pd.Series([False, False, True, False, False])
    hit = common.org_id_as_activity_id(ids, published, {"US-GOV-1", "GB-GOV-1"})
    assert list(hit) == [True, True, False, False, False]


def test_stage5_x1b(out):
    r = indicator(out, "global", "x1b")
    assert (r["numerator"], r["denominator"]) == (0, 10)  # no such case in the fixtures
    assert indicator(out, "global", "x1")["denominator"] == 10


# =============================================================================
# Stage 6
# =============================================================================
def country_indicator(out, scope: str, country: str, rule: str, code: int) -> tuple[float, float]:
    ci = pd.read_csv(out / "country_indicators.csv")
    r = ci[
        (ci["scope"] == scope) & (ci["country"] == country) & (ci["rule"] == rule) & (ci["indicator"] == code)
    ]
    assert len(r) == 1
    return r["numerator"].iloc[0], r["denominator"].iloc[0]


def test_stage6_country_indicators(out):
    # Indicator 1 (activities): B1, G1, A1, C1 name UG; B2 and G1 (UG 60 / KE 40) name KE.
    assert country_indicator(out, "global", "UG", "every", 1) == (4, 4)
    assert country_indicator(out, "global", "KE", "every", 1) == (2, 2)
    assert country_indicator(out, "global", "UG", "single", 1) == (3, 3)
    assert country_indicator(out, "global", "KE", "split", 1) == (1.4, 1.4)
    # Indicator 3 (transactions), UG: A1 4/4 + G1 1/2 + C1 1/1 + B1 2/3 (duplicate left out).
    # G1's transaction naming KE takes the activity's countries (UG;KE).
    assert country_indicator(out, "global", "UG", "every", 3) == (8, 10)
    assert country_indicator(out, "global", "UG", "single", 3) == (7, 8)
    assert country_indicator(out, "global", "UG", "split", 3) == (7.6, 9.2)
    # 2025: A1's 2024 commitment drops out; equal to stage 5's UG_2025 subset.
    assert country_indicator(out, "all_2025", "UG", "every", 3) == (7, 9)
    assert indicator(out, "UG_2025", "3")[["numerator", "denominator"]].tolist() == [7, 9]
    lu = pd.read_csv(out / "spatial_links_unplaced.csv")
    assert lu.groupby("side")["links"].sum().tolist() == [4, 4]


def test_country_rule_shared_by_stages_5_and_6():
    """A transaction takes its activity's countries; its own only if the activity
    names none. Stage 5's country subsets use the same rule as stage 6's maps."""
    import common

    s5 = load_stage("05_indicators.py")
    act = pd.DataFrame({"copy_id": [1, 2, 3], "recipient_countries": ["UG", "GLOBAL", None]})
    tx = pd.DataFrame(
        {
            "copy_id": [1, 1, 2, 3],
            "tx_recipient_countries": ["KE", None, "KE", "TZ;KE"],
            "transaction_year": [2025] * 4,
        }
    )
    tx = s5.add_countries(tx, act)
    assert tx["countries"].tolist() == ["UG", "UG", "KE", "TZ;KE"]
    # KE 2025: not the KE transaction of the UG activity (the old rule counted it)
    assert s5.in_scope(tx, act, "KE", 2025).tolist() == [False, False, True, True]
    assert s5.in_scope(tx, act, "UG", 2025).tolist() == [True, True, False, False]
    own = common.valid_countries(pd.Series(["UG;GLOBAL", "998"]))
    assert own.fillna("-").tolist() == ["UG", "-"]


def test_stage6_country_rules():
    import common

    mod = load_stage("06_spatial.py")
    codes = pd.Series(["UG;GLOBAL", "998", "KE;KE;UG", None, "HTI"])
    assert common.valid_countries(codes).fillna("-").tolist() == ["UG", "-", "KE;UG", "-", "-"]
    c = pd.Series(["UG;KE", "UG;KE", "UG;KE;TZ", "UG"])
    p = pd.Series(["60;40", "60", "a;b;c", None])  # usable, wrong length, not numbers, none
    w = mod.country_weights(c, p, "split").groupby(["idx", "country"])["weight"].sum().round(3)
    assert w.to_dict() == {
        (0, "KE"): 0.4,
        (0, "UG"): 0.6,
        (1, "KE"): 0.5,
        (1, "UG"): 0.5,
        (2, "KE"): 0.333,
        (2, "TZ"): 0.333,
        (2, "UG"): 0.333,
        (3, "UG"): 1.0,
    }
    assert len(mod.country_weights(c, p, "every")) == 8
    assert mod.country_weights(c, p, "single")["idx"].tolist() == [3]


def test_stage6_locations(out):
    loc = pd.read_parquet(out / "locations.parquet").sort_values(["iati_identifier", "loc_seq"])
    # B1: Kampala (English name chosen) and a 0,0 placeholder; C1: version 1 coordinates.
    # The old copy of B2 (ngo-old.xml) is dropped in stage 2, so its location is not kept.
    assert loc["iati_identifier"].tolist() == ["GB-CHC-111-B1", "GB-CHC-111-B1", "UG-NGB-333-C1"]
    assert loc["name"].tolist() == ["Kampala", "Head office placeholder", "Gulu"]
    assert loc["point_status"].tolist() == ["valid", "out of range or 0,0", "valid"]
    assert loc["lat"].round(4).tolist()[::2] == [0.3476, 2.7724]
    assert loc.iloc[0][["n_administrative", "admin_code", "exactness"]].tolist() == [2, "232422", "1"]


def test_stage6_point_formats():
    mod = load_stage("06_spatial.py")
    pos = ["0.35 32.58", "-10.5, -37.3", "1.5,2.5", "0,35 32,58", ". .", "0 0", "23.4 7036377", None]
    locs = pd.DataFrame(
        {"pos": pos, "v1_latitude": [None] * 7 + ["2.77"], "v1_longitude": [None] * 7 + ["32.29"]}
    )
    p = mod.parse_points(locs)
    assert p["lat"].round(2).fillna(-999).tolist() == [0.35, -10.5, 1.5, 0.35, -999, 0, 23.4, 2.77]
    assert p["point_status"].tolist() == ["valid"] * 4 + ["unreadable"] + ["out of range or 0,0"] * 2 + [
        "valid"
    ]


def test_stage6_geo_chains(out):
    gc = pd.read_csv(out / "geo_chains.csv", na_filter=False)
    # Two chains of 2 steps: A1 > B1 > C1 ends at a local NGO with a usable point in
    # Uganda (showcase rank 1); the chain ending at B2 has no location at its end
    # (counter-example rank 1).
    cols = ["end_activity", "end_local", "end_points_usable", "end_problem", "showcase", "counterexample"]
    assert gc.sort_values("chain_id")[cols].values.tolist() == [
        ["UG-NGB-333-C1", True, 1, "", True, False],
        ["GB-CHC-111-B2", False, 0, "no location", False, True],
    ]
    gp = pd.read_csv(out / "geo_chain_points.csv")
    # A1 has no location; B1's 0,0 point is left out; Kampala and Gulu lie in Uganda
    assert gp[["iati_identifier", "position", "is_end", "point_usable"]].values.tolist() == [
        ["GB-CHC-111-B1", 1, False, True],
        ["UG-NGB-333-C1", 2, True, True],
    ]
    go = pd.read_csv(out / "geo_chain_orgs.csv", na_filter=False)
    assert go.loc[go["chain_id"] == 1, ["role", "org_ref", "org_country"]].values.tolist() == [
        ["funder", "GB-GOV-1", "GB"],
        ["intermediary", "GB-CHC-111", "GB"],
        ["implementer", "UG-NGB-333", "UG"],
    ]


def test_stage6_dac_codes_national_or_international():
    """XM-DAC donor codes below 900 are national governments; from 900 they are not."""
    mod = load_stage("06_spatial.py")
    refs = pd.Series(["XM-DAC-7", "XM-DAC-3-1", "XM-DAC-302", "XM-DAC-928", "XM-DAC-1601", "XM-DAC-30010"])
    no_registry = pd.DataFrame({"organisation_identifier": [], "hq_country": []}, dtype="string")
    oc = mod.org_countries(refs, no_registry, {"NL"})
    # Netherlands, Denmark, United States; WHO, Gates Foundation, Unitaid
    assert oc["international"].tolist() == [False, False, False, True, True, True]


def test_stage6_country_flows(out):
    cf = pd.read_csv(out / "country_flows.csv", na_filter=False).sort_values("to_place")
    # 4 network links, all funded by GB-registered organisations:
    #   A1 > B1 and B1 > C1 go to UG activities (both confirmed; only C1's publisher is
    #   registered in UG); G1 > B2 goes to a KE activity; A1 > G1 to an activity in UG and KE.
    cols = ["from_country", "to_place", "links", "links_confirmed", "links_receiver_registered_here"]
    assert cf[cols].values.tolist() == [
        ["GB", "KE", 1, 0, 0],
        ["GB", "UG", 2, 2, 1],
        ["GB", "several countries", 1, 1, 0],
    ]
    assert cf["domestic"].tolist() == [False, False, False]


def test_stage6_org_countries(out):
    # Every organisation in the fixture's organisation links has a country prefix (GB, UG)
    oc = pd.read_csv(out / "org_countries.csv", na_filter=False).set_index("org_ref")
    assert (oc["org_country_source"] == "identifier prefix").all()
    assert oc.loc["UG-NGB-333", "org_country"] == "UG"
    assert oc.loc["GB-GOV-1", "org_country"] == "GB"
    assert not oc["international"].any()


def test_stage6_country_flows_by_year():
    """Hand-worked: three network links from GB-registered funders.
    A>B to a KE activity, transactions in 2024 and 2025; C>D to a UG activity, 2025 only;
    E>F to a UG activity, 2023 only. 2025: GB>KE 1, GB>UG 1. 2024: GB>KE 1. 2023: GB>UG 1."""
    mod = load_stage("06_spatial.py")
    edges = pd.DataFrame({"from_activity": ["A", "C", "E"], "to_activity": ["B", "D", "F"], "from_org": ["GB-COH-1"] * 3,
                          "to_org": ["KE-NGO-1", "GB-CHC-2", "UG-NGB-3"], "evidence": ["confirmed", "one-sided", "one-sided"], "in_network": [True] * 3})
    act = pd.DataFrame({"iati_identifier": ["B", "D", "F"], "countries": ["KE", "UG", "UG"], "recipient_regions": [None] * 3})
    oc = pd.DataFrame({"org_ref": ["GB-COH-1", "KE-NGO-1", "GB-CHC-2", "UG-NGB-3"], "org_country": ["GB", "KE", "GB", "UG"], "international": [False] * 4})
    ly = pd.DataFrame({"from_activity": ["A", "A", "C", "E"], "to_activity": ["B", "B", "D", "F"], "transaction_year": [2024, 2025, 2025, 2023]})
    r = mod.country_flows_by_year(edges, act, oc, ly, [2023, 2024, 2025]).set_index(["year", "to_place"])
    assert r.loc[(2025, "KE"), "links"] == 1 and r.loc[(2025, "UG"), "links"] == 1
    assert r.loc[(2024, "KE"), "links"] == 1 and (2024, "UG") not in r.index
    assert r.loc[(2023, "UG"), "links_receiver_registered_here"] == 1
    assert r.loc[(2025, "KE"), "links_receiver_registered_here"] == 1 and r.loc[(2025, "KE"), "links_confirmed"] == 1


def test_stage6_local_link_steps():
    """Hand-worked: chain 1 A>B>C>D, chain 2 E>C>D (E another funder).
    C>D: received by a KE-registered organisation for a KE activity (local); step 3 in
    chain 1, step 2 in chain 2: fewest 2, origin funder that of chain 2 (US-1, US).
    B>C: received by a GB organisation for a KE activity: not local.
    X>Y: local (UG org, UG activity) but in no chain: no steps."""
    mod = load_stage("06_spatial.py")
    edges = pd.DataFrame({"from_activity": ["A", "B", "C", "E", "X"], "to_activity": ["B", "C", "D", "C", "Y"],
                          "from_org": ["GB-1", "GB-2", "GB-3", "US-1", "UG-9"], "to_org": ["GB-2", "GB-3", "KE-7", "GB-3", "UG-8"],
                          "in_network": [True] * 5})
    act = pd.DataFrame({"iati_identifier": ["B", "C", "D", "Y"], "countries": ["KE", "KE", "KE", "UG"], "recipient_regions": [None] * 4})
    oc = pd.DataFrame({"org_ref": ["GB-1", "GB-2", "GB-3", "KE-7", "US-1", "UG-8", "UG-9"],
                       "org_country": ["GB", "GB", "GB", "KE", "US", "UG", "UG"], "international": [False] * 7})
    steps = pd.DataFrame({"chain_id": [1, 1, 1, 2, 2], "step": [1, 2, 3, 1, 2],
                          "from_activity": ["A", "B", "C", "E", "C"], "to_activity": ["B", "C", "D", "C", "D"]})
    chains = pd.DataFrame({"chain_id": [1, 2], "path_org_refs": ["GB-1 > GB-2 > GB-3 > KE-7", "US-1 > GB-3 > KE-7"]})
    r = mod.local_link_steps(edges, act, oc, steps, chains).set_index(["from_activity", "to_activity"])
    assert set(r.index) == {("C", "D"), ("X", "Y")}
    assert r.loc[("C", "D"), "steps_from_origin"] == 2 and r.loc[("C", "D"), "origin_country"] == "US"
    assert pd.isna(r.loc[("X", "Y"), "steps_from_origin"])


def test_stage6_point_country_check():
    mod = load_stage("06_spatial.py")
    borders = mod.load_borders()
    locs = pd.DataFrame(
        {
            "copy_id": [1, 1, 2, 3],
            "lon": [32.58, -11.95, 38.76, 32.29],
            "lat": [0.35, 55.03, 9.0, 2.77],
            "point_status": ["valid"] * 4,
            "feature_designation": [None, None, "PCLI", None],
        }
    )
    act = pd.DataFrame({"copy_id": [1, 2, 3], "countries": ["UG;KE", "ET", None]})
    p = mod.check_points(locs, act, borders)
    # Kampala in UG; a point in the Atlantic is in neither UG nor KE; a whole-country
    # point in ET is inside but country-level; an activity without country: unknown
    assert p["in_recipient_country"].astype(object).where(
        p["in_recipient_country"].notna(), None
    ).tolist() == [
        True,
        False,
        True,
        None,
    ]
    assert p["point_usable"].tolist() == [True, False, False, False]
    oc = mod.org_countries(
        pd.Series(["GB-CHC-1", "XM-DAC-41122", "47045", "US-EIN-9", "XM-DAC-7", "XI-IATI-EC_INTPA"]),
        pd.DataFrame({"organisation_identifier": ["XM-DAC-41122", "XM-DAC-7"], "hq_country": ["US", "NL"]}),
        set(borders["geoms"]),
    )
    assert oc["org_country"].fillna("-").tolist() == ["GB", "US", "-", "US", "NL", "-"]
    assert oc["org_country_source"].tolist() == [
        "identifier prefix",
        "registry headquarters",
        "none",
        "identifier prefix",
        "registry headquarters",
        "none",
    ]
    # XM-DAC-7 is the Netherlands government's DAC donor code, not an international body
    assert oc["international"].tolist() == [False, True, False, False, False, True]


def test_data_dictionary_complete(out):
    """Every column of every output table is listed in docs/data_dictionary.md."""
    import re

    text = (ROOT / "docs" / "data_dictionary.md").read_text()
    documented: dict[str, set[str]] = {}
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("### "):
            current = [f.strip() for f in line[4:].split("/")]
            for f in current:
                documented[f] = set()
        elif line.startswith("| `") and current:
            first_cell = line.split("|")[1]
            for f in current:
                documented[f] |= set(re.findall(r"`([^`]+)`", first_cell))
    for path in sorted(out.glob("*.parquet")) + sorted(out.glob("*.csv")):
        cols = pd.read_parquet(path).columns if path.suffix == ".parquet" else pd.read_csv(path).columns
        assert path.name in documented, f"{path.name} not in the data dictionary"
        missing = set(cols) - documented[path.name]
        assert not missing, f"{path.name}: undocumented columns {sorted(missing)}"


def test_example_chains_skips_missing_kinds(out, tmp_path, monkeypatch, capsys):
    """report/example_chains.py leaves out a kind of example with no matching chain,
    instead of stopping. The fixtures have no chain of 2+ steps that ends at a
    receiver organisation ID only, so that kind is left out and the other four are kept."""
    import json
    import shutil

    import config

    sys.path.insert(0, str(ROOT / "report"))
    import example_chains

    work = tmp_path / "out"
    shutil.copytree(out, work)
    monkeypatch.setenv("CH6_OUTPUT_DIR", str(work))
    monkeypatch.setattr(config, "BULK_DIR", BULK)
    example_chains.main()
    kinds = [e["kind"] for e in json.loads((work / "examples" / "example_chains.json").read_text())]
    assert kinds == [
        "Confirmed at every step",
        "Implausible link",
        "Longest chain",
        "One origin, many chains",
    ]
    assert "left out: Broken by a missing activity ID" in capsys.readouterr().out


def test_missing_bulk_copy_gives_a_clear_message(tmp_path):
    """Without a bulk copy, the pipeline stops with one line that says what to do."""
    import config

    with pytest.raises(SystemExit, match="No Bulk Data Service copy found .* set CH6_BULK_DIR"):
        config.bulk_index_date(tmp_path / "iati-data")
