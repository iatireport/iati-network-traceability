"""Tests of the working paper report scripts (report/). These scripts exist only on the
local working-paper branch, so their tests are kept out of tests/test_pipeline.py.

Run from the project folder:  .venv/bin/python -m pytest
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

def test_case_pair_rule():
    """Hand-worked case pair (report/case_pair.py). Chains:
    1 FCDO > X > Y > local NGO, 3 steps, score 0.8, sector 151, end country ET: traceable.
    2 Other donor > Z > W > local NGO, score 0.9, same sector: ranked after 1 (not a preferred origin).
    3 broken, sector 151, countries ET;KE, stops at a name only, EUR 100.
    4 broken, sector 151, countries ET, name only, USD 500 (larger, but another currency).
    5 broken, sector 140 (other sector).
    Chain 1 is chosen; its partner is 3 (same currency EUR first, then value)."""
    spec = importlib.util.spec_from_file_location("case_pair", ROOT / "report" / "case_pair.py")
    cp = importlib.util.module_from_spec(spec); spec.loader.exec_module(cp)
    ch = pd.DataFrame({
        "chain_id": [1, 2, 3, 4, 5], "steps": [3, 3, 1, 2, 1], "completeness": [0.8, 0.9, 0.333, 0.5, 0.333],
        "path_org_refs": ["GB-GOV-1 > X > Y > L", "DE-1 > Z > W > L2", "NL > U", "NL > V > R", "NL > Q"],
        "path_org_types": ["Government > A > B > National NGO"] * 2 + ["Government > Multilateral", "Government > A > B", "Government > C"],
        "countries": ["ET", "ET", "ET;KE", "ET", "ET"],
        "end_reason": ["no outgoing transactions", "no outgoing transactions", "receiver name only", "receiver name only", "receiver name only"],
        "origin_sector": ["151", "151", "151", "151", "140"], "end_country": ["ET", "ET", "ET", "ET", "ET"],
        "origin_value": [10.0, 20.0, 100.0, 500.0, 900.0], "currency": ["EUR", "EUR", "EUR", "USD", "EUR"],
    })
    res = cp.select_pair(ch, {"National NGO"})
    assert res["traceable"]["chain_id"] == 1 and res["broken"]["chain_id"] == 3
    # No chain of 3+ steps reaches NEAR_ONE: the failure is reported by condition
    res = cp.select_pair(ch.assign(completeness=0.5), {"National NGO"})
    assert "failed" in res and res["failed"]["T2 completeness >= NEAR_ONE"] == 0


def test_location_precision():
    """Hand-worked (report/location_precision.py). Capital: Nairobi (KE) at -1.28, 36.81.
    A at Nairobi (0 km), code PPLC, exact. B 0.15 degrees of latitude south (about 16.7 km),
    no feature code, approximate. C at Mombasa (about 440 km), code PPLA, no exactness.
    D in a country with no capital listed (left out of the distance measures), code PPL,
    exactness "1.0". Within 10 km: 1 of 3; within 25 km: 2 of 3; feature code given 3 of 4;
    of those PPLC 1, PPLA 1; exact 2 of 4, approximate 1, missing 1."""
    spec = importlib.util.spec_from_file_location("location_precision", ROOT / "report" / "location_precision.py")
    lp = importlib.util.module_from_spec(spec); spec.loader.exec_module(lp)
    caps = pd.DataFrame({"iso2": ["KE"], "lat": [-1.28], "lon": [36.81]})
    pts = pd.DataFrame({"lat": [-1.28, -1.43, -4.04, 10.0], "lon": [36.81, 36.81, 39.67, 10.0],
                        "countries": ["KE", "KE;UG", "KE", "XX"],
                        "feature_designation": ["PPLC", None, "ppla", "PPL"], "exactness": ["1", "2", None, "1.0"]})
    pts["dist_km"] = lp.nearest_capital_km(pts, caps)
    assert pts["dist_km"].iloc[0] < 0.01 and 15 < pts["dist_km"].iloc[1] < 18 and pts["dist_km"].iloc[2] > 400 and pd.isna(pts["dist_km"].iloc[3])
    t = lp.precision_table(pts, {"all": pd.Series(True, index=pts.index)}).set_index("measure")
    assert t.loc["within_10km", ["numerator", "denominator"]].tolist() == [1, 3]
    assert t.loc["within_25km", ["numerator", "denominator"]].tolist() == [2, 3]
    assert t.loc["feature_code_given", ["numerator", "denominator"]].tolist() == [3, 4]
    assert t.loc["pplc", "numerator"] == 1 and t.loc["ppla", "numerator"] == 1 and t.loc["pplc_or_ppla", "denominator"] == 3
    assert t.loc["exact", "numerator"] == 2 and t.loc["approximate", "numerator"] == 1 and t.loc["exactness_missing", "numerator"] == 1


def test_d2_top_publishers():
    """Hand-worked (report/top_publishers.py). Traced transactions: A 3, B 2, C 2, D 1;
    one untraced transaction of E. Top 2: A (3), then B before C (tie, by identifier).
    Shares of all 8 traced: 37.5% and 25.0%."""
    spec = importlib.util.spec_from_file_location("top_publishers", ROOT / "report" / "top_publishers.py")
    d2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(d2)
    t = pd.DataFrame({"reporting_org_ref": list("AAABBCCDE"), "publisher_group": ["G"] * 9,
                      "traced": [True] * 8 + [False]})
    top = d2.top_publishers(t, pd.Series({"A": "Org A", "B": "Org B", "C": "Org C"}), n=2)
    assert top["reporting_org_ref"].tolist() == ["A", "B"]
    assert top["traced"].tolist() == [3, 2] and top["traced_all"].tolist() == [8, 8]
    assert top["percent"].tolist() == [37.5, 25.0] and top["org_name"].tolist() == ["Org A", "Org B"]
    # top_by_flag: the same counts for any flag (here: traced), 5 at most, total over all flagged rows
    tb = d2.top_by_flag(t, "traced", pd.Series({"A": "Org A"}), n=1)
    assert tb[["reporting_org_ref", "n", "total", "percent"]].values.tolist() == [["A", 3, 8, 37.5]]
