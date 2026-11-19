"""Location check for the working paper (CC-13): are published points mostly at capital
cities rather than project sites? Rerun each year after the pipeline:

    .venv/bin/python report/location_precision.py        (from the project folder)

Points: the usable points of locations.parquet (stage 6): a valid coordinate, inside one
of the activity's recipient countries, and not coded as the whole country.

Measures, for each group of points:
  within_10km, within_25km  the point is within 10 / 25 km of a national capital of one of
                            the activity's recipient countries (great-circle distance to the
                            nearest such capital; capitals from Natural Earth 1:50m populated
                            places, report/reference/ne_capitals.csv, the same source as the
                            borders). Points whose countries have no capital listed are left
                            out of these two measures.
  feature_code_given        a GeoNames feature code is published (location-class / feature-
                            designation)
  pplc, ppla, pplc_or_ppla  of the points with a feature code: PPLC (capital of a political
                            entity) or PPLA (seat of a first-order administrative division)
  exact, approximate, exactness_missing   exactness code 1, 2, or none
Groups: all usable points; each publisher group (IATI organisation type of the reporting
organisation); points of activities in the network; points of the end activities of chains
of 2 or more steps.

Output: report/figures/wp/location_precision.csv (group, measure, numerator, denominator,
percent).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import common  # noqa: E402
import config  # noqa: E402

CAPITALS = HERE / "reference" / "ne_capitals.csv"
OUT = HERE / "figures" / "wp" / "location_precision.csv"
RADII_KM = (10, 25)
EARTH_KM = 6371.0088


def nearest_capital_km(pts: pd.DataFrame, caps: pd.DataFrame) -> pd.Series:
    """Distance (km) from each point (lat, lon, countries "KE;UG") to the nearest capital of
    any of its countries; NaN if none of its countries has a capital listed."""
    cap_by = {c: g[["lat", "lon"]].to_numpy() for c, g in caps.groupby("iso2")}
    out = np.full(len(pts), np.nan)
    lat = np.radians(pts["lat"].to_numpy(float)); lon = np.radians(pts["lon"].to_numpy(float))
    for i, cs in enumerate(pts["countries"].fillna("").str.split(";")):
        cc = [cap_by[c] for c in cs if c in cap_by]
        if not cc:
            continue
        c = np.radians(np.vstack(cc))
        a = np.sin((c[:, 0] - lat[i]) / 2) ** 2 + np.cos(lat[i]) * np.cos(c[:, 0]) * np.sin((c[:, 1] - lon[i]) / 2) ** 2
        out[i] = (2 * EARTH_KM * np.arcsin(np.sqrt(a))).min()
    return pd.Series(out, index=pts.index)


def measures(p: pd.DataFrame) -> list[tuple[str, int, int]]:
    """(measure, numerator, denominator) for one group of points with columns dist_km,
    feature_designation, exactness."""
    d = p["dist_km"].dropna()
    code = p["feature_designation"].astype("string").str.strip().str.upper()
    has = code.notna() & (code != "")
    ex = p["exactness"].astype("string").str.replace(r"\.0$", "", regex=True)
    rows = [(f"within_{r}km", int((d <= r).sum()), len(d)) for r in RADII_KM]
    rows += [("feature_code_given", int(has.sum()), len(p)),
             ("pplc", int((code[has] == "PPLC").sum()), int(has.sum())),
             ("ppla", int((code[has] == "PPLA").sum()), int(has.sum())),
             ("pplc_or_ppla", int(code[has].isin(["PPLC", "PPLA"]).sum()), int(has.sum())),
             ("exact", int((ex == "1").sum()), len(p)),
             ("approximate", int((ex == "2").sum()), len(p)),
             ("exactness_missing", int(ex.isna().sum()), len(p))]
    return rows


def precision_table(pts: pd.DataFrame, groups: dict[str, pd.Series]) -> pd.DataFrame:
    """measures() for each named group (a boolean mask over pts)."""
    out = []
    for g, mask in groups.items():
        for m, n, d in measures(pts[mask]):
            out.append({"group": g, "measure": m, "numerator": n, "denominator": d,
                        "percent": round(100 * n / d, 1) if d else np.nan})
    return pd.DataFrame(out)


def load_stage(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    out_dir = config.output_dir()
    locs = pd.read_parquet(out_dir / "locations.parquet",
                           columns=["copy_id", "iati_identifier", "lat", "lon", "exactness", "feature_designation", "point_usable"])
    pts = locs[locs["point_usable"]].copy()
    # The activity's countries, by the rule stage 6 uses to check the points
    s6 = load_stage("06_spatial")
    act = pd.read_parquet(out_dir / "activities_clean.parquet",
                          columns=["copy_id", "iati_identifier", "recipient_countries", "recipient_country_percentages", "publisher_group"])
    act = act[act["copy_id"].isin(pts["copy_id"])]
    tx = pd.read_parquet(out_dir / "transactions_clean.parquet", columns=["copy_id", "tx_recipient_countries"],
                         filters=[("copy_id", "in", list(act["copy_id"].unique()))]).rename(columns={"tx_recipient_countries": "tx_countries"})
    ac = s6.activity_countries(act, tx)
    pts = pts.merge(ac[["copy_id", "countries"]], on="copy_id", how="left").merge(act[["copy_id", "publisher_group"]], on="copy_id", how="left")
    caps = common.read_csv(CAPITALS)
    pts["dist_km"] = nearest_capital_km(pts, caps)
    edges = pd.read_parquet(out_dir / "activity_edges.parquet", columns=["from_activity", "to_activity", "in_network"])
    net = set(edges.loc[edges["in_network"], "from_activity"]) | set(edges.loc[edges["in_network"], "to_activity"])
    ch = common.read_csv(out_dir / "chains.csv", usecols=["steps", "end_activity"])
    ends2 = set(ch.loc[ch["steps"] >= 2, "end_activity"])
    groups = {"All usable points": pd.Series(True, index=pts.index),
              "Network activities": pts["iati_identifier"].isin(net),
              "End activities of chains of 2+ steps": pts["iati_identifier"].isin(ends2)}
    for g in sorted(pts["publisher_group"].dropna().unique()):
        groups[f"Publisher type: {g}"] = pts["publisher_group"] == g
    tab = precision_table(pts, groups)
    tab["points_without_capital"] = int(pts["dist_km"].isna().sum())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tab.to_csv(OUT, index=False)
    print(tab[tab["group"].isin(["All usable points", "Network activities", "End activities of chains of 2+ steps"])].to_string(index=False))


if __name__ == "__main__":
    main()
