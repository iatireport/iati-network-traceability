"""Stage 6: the spatial view of traceability.
Part A: indicators 1 and 3 by recipient country, for the country maps.
Part D: activity locations, and chains of several steps that end at an activity
with a mapped location (example chains for the report).
Part B: network links by the funder's country of registration and the receiving
activity's recipient country (unit: links).

Run time about 4 minutes (2 November 2026), plus about 1 minute the first time after
stage 2, when the <location> elements are read from the bulk copy. Part A alone used
about 3.5 GB of memory; the peak with parts D and B has not been measured.

Input   <output dir>/activities_clean.parquet, transactions_clean.parquet (only the
        columns needed), activity_edges.parquet, related_activities_clean.parquet,
        files.csv, chains.csv, chain_steps.csv, org_edges.parquet,
        publishers.parquet (stages 1-4); for part D also the <location> elements of
        the bulk copy used by stage 1, and config.COUNTRY_BORDERS
Output  <output dir>/
          country_indicators.csv     one row per scope x country x multi-country
                                     rule x indicator (1: activities, 3: transactions)
          spatial_unplaced.csv       activities and transactions that cannot be put
                                     on a country map, by reason and code
          spatial_links_unplaced.csv network links by whether each end has a country
          locations.parquet          one row per <location> of a kept activity copy
          geo_chains.csv             one row per chain of SHOWCASE_MIN_STEPS+ steps,
                                     with its locations and showcase rank
          geo_chain_points.csv       the valid points of every activity on the chains
                                     whose end activity has one
          geo_chain_orgs.csv         the organisations of the example chains, with
                                     their country of registration
          geo_chain_context.csv      other organisation links of those organisations
                                     (grey context lines)
          country_flows.csv          part B: network links per (funder country,
                                     recipient place)
          country_flows_by_year.csv  the same for the links with a transaction dated
                                     in each year of config.SUBSETS
          local_links_steps.csv      every link received by a local organisation, with
                                     the fewest steps from a chain origin to it
          org_countries.csv          country of registration of every organisation in
                                     the organisation links (org_countries()), for
                                     the report's tables and chain diagrams

Placing records in countries (docs/methods_decisions.md, section 7):
  An activity's countries are its activity-level recipient countries; if it names
  none, the countries named on its transactions.
  A transaction's countries are its activity's activity-level countries; if the
  activity names none, the transaction's own (common.transaction_countries(), also
  used by stage 5's country subsets).
  Only codes matching config.COUNTRY_CODE_PATTERN are countries.
  A record naming several countries is counted under each rule in
  config.MULTI_COUNTRY_RULES (every / single / split); the maps use
  config.MULTI_COUNTRY_RULE.
Indicators use the stage 5 definitions (connected_activities(), indicator3_flags()),
with exact duplicate transactions left out. The network is the global one.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
import shapely.geometry
from lxml import etree

import common
import config

STAGE = "06_spatial"
HERE = Path(__file__).resolve().parent

TX_COLS = [
    "copy_id",
    "transaction_type",
    "transaction_year",
    "internal",
    "exact_duplicate",
    "valid_receiver_ref",
    "tx_recipient_countries",
]
ACT_COLS = [
    "copy_id",
    "iati_identifier",
    "recipient_countries",
    "recipient_country_percentages",
    "recipient_regions",
]


def load_stage(name: str):
    """Import another stage script (its name starts with a digit, so `import` cannot)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_stage5():
    """Import 05_indicators.py, to reuse its indicator definitions."""
    return load_stage("05_indicators")


# =============================================================================
# Country lists
# =============================================================================
def activity_countries(act: pd.DataFrame, tx: pd.DataFrame) -> pd.DataFrame:
    """Countries of each activity (copy_id): activity level, else the union of the
    countries on its transactions. Returns copy_id, countries, percentages, source
    ("activity", "transactions" or None); percentages only for activity level.
    Country codes are filtered with common.valid_countries()."""
    a = act.set_index("copy_id")
    own = common.valid_countries(a["recipient_countries"])
    t = tx.loc[tx["tx_countries"].notna(), ["copy_id", "tx_countries"]]
    t = t.assign(c=t["tx_countries"].str.split(";")).explode("c").drop_duplicates(["copy_id", "c"])
    from_tx = t.sort_values("c").groupby("copy_id")["c"].agg(";".join).reindex(a.index)
    # Percentages are kept only if the activity-level list is unchanged by the
    # country filter (otherwise positions no longer match).
    pct = a["recipient_country_percentages"].where(own == a["recipient_countries"].str.replace(" ", ""))
    return pd.DataFrame(
        {
            "countries": own.fillna(from_tx),
            "percentages": pct.where(own.notna()),
            "source": np.select([own.notna(), from_tx.notna()], ["activity", "transactions"], default=None),
        },
        index=a.index,
    ).reset_index()


def country_weights(countries: pd.Series, percentages: pd.Series, rule: str) -> pd.DataFrame:
    """One row per record x country, with the record's weight in that country.

    every   weight 1 in each listed country
    single  weight 1, records naming exactly one country only
    split   the published percentage / their sum, where every listed country has a
            usable percentage (a number, total above 0); otherwise 1 / number of
            countries. Weights of one record sum to 1.
    Returns columns: idx (the index of the input), country, weight."""
    assert rule in config.MULTI_COUNTRY_RULES, rule
    c = countries.dropna().str.split(";").rename_axis("idx")
    k = c.str.len()
    if rule == "single":
        c = c[k == 1]
    long = c.explode().rename("country").reset_index()
    long["k"] = long["idx"].map(k)
    if rule in ("every", "single"):
        return long.assign(weight=1.0)[["idx", "country", "weight"]]
    # split: match each country to the percentage in the same position
    long["pos"] = long.groupby("idx").cumcount()
    p = percentages.reindex(c.index).fillna("").str.split(";")
    p = p[p.str.len() == k]  # a list of a different length cannot be matched
    pl = p.explode().rename("pct").reset_index()
    pl["pos"] = pl.groupby("idx").cumcount()
    long = long.merge(pl, on=["idx", "pos"], how="left")
    v = pd.to_numeric(long["pct"], errors="coerce")
    g = long["idx"]
    total = v.groupby(g).transform("sum")
    usable = v.notna().groupby(g).transform("all") & (total > 0)
    long["weight"] = np.where(usable, v / total, 1 / long["k"])
    return long[["idx", "country", "weight"]]


# =============================================================================
# Part A: indicators by country
# =============================================================================
def scope_of(year: int | None) -> dict:
    """Scope labels as in indicators.csv."""
    return {"scope": "global" if year is None else f"all_{year}", "year": "all" if year is None else year}


def country_rows(w: pd.DataFrame, flags: pd.DataFrame, num: str, den: str) -> pd.DataFrame:
    """Weighted numerator and denominator per country. w: idx, country, weight;
    flags: indexed like idx, with boolean columns num and den."""
    f = flags.reindex(w["idx"].to_numpy())
    d = pd.DataFrame(
        {
            "country": w["country"].to_numpy(),
            "numerator": w["weight"].to_numpy() * f[num].to_numpy(dtype=float),
            "denominator": w["weight"].to_numpy() * f[den].to_numpy(dtype=float),
            "records": f[den].to_numpy(dtype=int),
        }
    )
    return d.groupby("country", as_index=False).sum()


def scope_activities(act: pd.DataFrame, t: pd.DataFrame) -> pd.DataFrame:
    """Activities with at least one transaction in the scope (t). An activity that
    names no country itself takes the countries of its transactions in the scope,
    as in stage 5's in_scope()."""
    a = act[act["copy_id"].isin(t["copy_id"])].copy()
    from_tx = a["source"] != "activity"
    tt = t.loc[
        t["copy_id"].isin(a.loc[from_tx, "copy_id"]) & t["countries"].notna(), ["copy_id", "countries"]
    ]
    tt = tt.assign(c=tt["countries"].str.split(";")).explode("c").drop_duplicates(["copy_id", "c"])
    union = tt.sort_values("c").groupby("copy_id")["c"].agg(";".join)
    a.loc[from_tx, "countries"] = a.loc[from_tx, "copy_id"].map(union)
    return a


def indicators_by_country(
    act: pd.DataFrame, tx: pd.DataFrame, connected: set[str], year: int | None
) -> pd.DataFrame:
    """Indicators 1 (activities) and 3 (transactions) per recipient country, under
    each multi-country rule, for one scope (all years, or one transaction year)."""
    t = tx if year is None else tx[tx["transaction_year"] == year]
    a = scope_activities(act, t)
    a = a.assign(d1=True, n1=a["iati_identifier"].isin(connected)).set_index("copy_id")
    t = t[t["d3"]]
    rows = []
    for rule in config.MULTI_COUNTRY_RULES:
        wa = country_weights(a["countries"], a["percentages"], rule)
        wt = country_weights(t["countries"], t["percentages"], rule)
        for code, name, unit, w, flags, n, d in [
            ("1", "Activities connected to another IATI activity", "activities", wa, a, "n1", "d1"),
            ("3", "Outgoing transactions with a valid receiver ID", "transactions", wt, t, "n3", "d3"),
        ]:
            r = country_rows(w, flags, n, d)
            rows.append(r.assign(rule=rule, indicator=code, name=name, unit=unit))
    out = pd.concat(rows, ignore_index=True)
    out = out[out["records"] > 0]
    out["percent"] = (100 * out["numerator"] / out["denominator"]).round(1)
    out["numerator"] = out["numerator"].round(2)
    out["denominator"] = out["denominator"].round(2)
    out["small_base"] = out["denominator"] < config.SMALL_BASE
    out["default_rule"] = out["rule"] == config.MULTI_COUNTRY_RULE
    out = out.assign(**scope_of(year))
    cols = ["scope", "year", "country", "rule", "default_rule", "indicator", "name", "unit"]
    cols += ["numerator", "denominator", "percent", "records", "small_base"]
    return out[cols].sort_values(["indicator", "rule", "country"])


def unplaced(act: pd.DataFrame, tx: pd.DataFrame, connected: set[str], year: int | None) -> pd.DataFrame:
    """Activities in the scope with no country, and outgoing transactions (indicator 3
    denominator) with no country, by reason: region only (per region code, counted
    in every region named), non-standard country code only (per code), or nothing.
    """
    t = tx if year is None else tx[tx["transaction_year"] == year]
    a = scope_activities(act, t)
    a = a[a["countries"].isna()].copy()
    a["connected"] = a["iati_identifier"].isin(connected)
    td = t[t["d3"] & t["countries"].isna()]
    td = td.groupby("copy_id").agg(
        outgoing_transactions=("d3", "size"), outgoing_valid_receiver=("n3", "sum")
    )
    a = a.join(td, on="copy_id").fillna({"outgoing_transactions": 0, "outgoing_valid_receiver": 0})
    raw = a["recipient_countries"].fillna("")
    a["reason"] = np.select(
        [a["recipient_regions"].notna(), raw != ""],
        ["region only", "non-standard country code only"],
        default="no country or region",
    )
    a["code"] = np.select(
        [a["reason"] == "region only", a["reason"] == "non-standard country code only"],
        [a["recipient_regions"], raw],
        default="",
    )
    a = a.assign(code=a["code"].str.split(";")).explode("code")
    out = a.groupby(["reason", "code"], as_index=False).agg(
        activities=("iati_identifier", "size"),
        activities_connected=("connected", "sum"),
        outgoing_transactions=("outgoing_transactions", "sum"),
        outgoing_valid_receiver=("outgoing_valid_receiver", "sum"),
    )
    out = out.assign(**scope_of(year)).sort_values(["reason", "activities"], ascending=[True, False])
    return out[
        ["scope", "year", "reason", "code", "activities", "activities_connected"]
        + ["outgoing_transactions", "outgoing_valid_receiver"]
    ]


def activity_place(act: pd.DataFrame, one_country_code: bool = False) -> pd.Series:
    """Where each activity (by iati_identifier) can be placed: "several countries",
    "one country" (or, with one_country_code, the country code itself), "region
    only" or "no country or region". Countries as placed in part A, all years."""
    a = act.set_index("iati_identifier")
    one = a["countries"] if one_country_code else "one country"
    return pd.Series(
        np.select(
            [
                a["countries"].str.contains(";", na=False),
                a["countries"].notna(),
                a["recipient_regions"].notna(),
            ],
            ["several countries", one, "region only"],
            default="no country or region",
        ),
        index=a.index,
    )


def links_unplaced(edges: pd.DataFrame, act: pd.DataFrame) -> pd.DataFrame:
    """Network links by whether the funder and the receiver activity have a country
    (one / several), only a region, or nothing."""
    place = activity_place(act)
    net = edges[edges["in_network"]]
    rows = []
    for side, col in [("funder", "from_activity"), ("receiver", "to_activity")]:
        n = net[col].map(place).fillna("no country or region").value_counts()
        rows.append(pd.DataFrame({"side": side, "placement": n.index, "links": n.to_numpy()}))
    return pd.concat(rows, ignore_index=True)


# =============================================================================
# Part D: locations
# =============================================================================
LOC_COLS = [
    "copy_id",
    "iati_identifier",
    "loc_seq",
    "location_ref",
    "name",
    "location_reach",
    "location_id_vocabulary",
    "location_id_code",
    "n_administrative",
    "admin_vocabulary",
    "admin_level",
    "admin_code",
    "pos",
    "v1_latitude",
    "v1_longitude",
    "v1_precision",
    "exactness",
    "location_class",
    "feature_designation",
]


def child_attr(el: etree._Element, tag: str, attr: str = "code") -> str | None:
    """Attribute of the first child with this tag (None if absent)."""
    c = el.find(tag)
    return None if c is None else c.get(attr)


def location_row(loc: etree._Element, lang: str | None, s1) -> dict:
    """One <location> as a dict of LOC_COLS (without copy_id, identifier, seq).
    Version 2 gives a point as <point><pos>lat lon</pos></point> and areas as
    <administrative vocabulary level code>; version 1 gives
    <coordinates latitude longitude precision> and <administrative adm1 adm2>.
    s1 is stage 1's module (its name rules: strip_or_none, pick_name)."""
    adm = loc.findall("administrative")
    a0 = adm[0] if adm else None
    v1_adm = a0 is not None and a0.get("code") is None and (a0.get("adm1") or a0.get("adm2"))
    coords = loc.find("coordinates")
    return {
        "location_ref": s1.strip_or_none(loc.get("ref")),
        "name": s1.pick_name(loc.find("name"), lang),
        "location_reach": child_attr(loc, "location-reach"),
        "location_id_vocabulary": child_attr(loc, "location-id", "vocabulary"),
        "location_id_code": child_attr(loc, "location-id"),
        "n_administrative": len(adm),
        "admin_vocabulary": "v1" if v1_adm else (None if a0 is None else a0.get("vocabulary")),
        "admin_level": None if a0 is None or v1_adm else a0.get("level"),
        "admin_code": (a0.get("adm2") or a0.get("adm1"))
        if v1_adm
        else (None if a0 is None else a0.get("code")),
        "pos": s1.strip_or_none(loc.findtext("point/pos")),
        "v1_latitude": None if coords is None else coords.get("latitude"),
        "v1_longitude": None if coords is None else coords.get("longitude"),
        "v1_precision": None if coords is None else coords.get("precision"),
        "exactness": child_attr(loc, "exactness"),
        "location_class": child_attr(loc, "location-class"),
        "feature_designation": child_attr(loc, "feature-designation"),
    }


def read_file_locations(path: str, file_idx: int) -> pd.DataFrame | None:
    """All <location> elements of one XML file. Each gets the copy_id that stage 1
    gives its activity (file_idx x 1,000,000 + position in the file), so it can be
    joined to the copy kept in stage 2. Files without "<location" are skipped
    without parsing; a file that fails to parse returns None (stage 1 excluded it)."""
    if b"<location" not in Path(path).read_bytes():
        return None
    s1 = load_stage("01_parse_bulk")
    rows: dict[str, list] = {c: [] for c in LOC_COLS}
    position = 0
    try:
        for _, act in etree.iterparse(
            path, tag="iati-activity", huge_tree=True, resolve_entities=False, no_network=True
        ):
            ident = s1.strip_or_none(act.findtext("iati-identifier"))
            lang = act.get(s1.XML_LANG)
            for seq, loc in enumerate(act.iterchildren("location")):
                r = location_row(loc, lang, s1)
                r.update(copy_id=file_idx * 1_000_000 + position, iati_identifier=ident, loc_seq=seq)
                for c in LOC_COLS:
                    rows[c].append(r[c])
            position += 1
            act.clear(keep_tail=True)  # free memory as we go
            while act.getprevious() is not None:
                del act.getparent()[0]
    except etree.XMLSyntaxError, OSError, UnicodeDecodeError, ValueError:
        return None
    return pd.DataFrame(rows) if rows["copy_id"] else None


def parse_points(locs: pd.DataFrame) -> pd.DataFrame:
    """Latitude and longitude from <pos>, else from version 1 <coordinates>.
    <pos> is read as "lat,lon" or "lat, lon" if it holds exactly one comma between
    two numbers; otherwise as "lat lon", where a decimal comma is read as a point
    ("0,35 32,58"). point_status "valid": both numbers present and in range, and
    not 0,0 (a common placeholder)."""
    s = locs["pos"].fillna("").str.strip()
    by_comma = s.str.extract(r"^([^,\s]+)\s*,\s*([^,\s]+)$")
    by_space = s.str.extract(r"^(\S+)\s+(\S+)$")
    lat = pd.to_numeric(by_comma[0].fillna(by_space[0].str.replace(",", ".")), errors="coerce")
    lon = pd.to_numeric(by_comma[1].fillna(by_space[1].str.replace(",", ".")), errors="coerce")
    lat = lat.fillna(pd.to_numeric(locs["v1_latitude"], errors="coerce"))
    lon = lon.fillna(pd.to_numeric(locs["v1_longitude"], errors="coerce"))
    given = locs["pos"].notna() | locs["v1_latitude"].notna() | locs["v1_longitude"].notna()
    valid = lat.between(-90, 90) & lon.between(-180, 180) & ~((lat == 0) & (lon == 0))
    return locs.assign(
        lat=lat,
        lon=lon,
        point_status=np.select(
            [~given, valid, lat.isna() | lon.isna()],
            ["no point", "valid", "unreadable"],
            default="out of range or 0,0",
        ),
    )


def read_locations(bulk_dir: Path, out_dir: Path, kept: pd.DataFrame, log) -> pd.DataFrame:
    """Locations of the kept activity copies (kept: copy_id, iati_identifier), read
    from the files stage 1 parsed successfully, in parallel."""
    files = common.read_csv(
        out_dir / "files.csv", usecols=["file_idx", "publisher_folder", "file", "kind", "status"]
    )
    files = files[(files["kind"] == "activity") & (files["status"] == "ok")]
    files = files[files["file_idx"].isin(kept["copy_id"] // 1_000_000)]
    paths = [
        str(bulk_dir / "datasets" / f / f"{n}.xml")
        for f, n in zip(files["publisher_folder"], files["file"], strict=True)
    ]
    parts = []
    with ProcessPoolExecutor(max_workers=config.N_WORKERS) as pool:
        for k, df in enumerate(pool.map(read_file_locations, paths, files["file_idx"], chunksize=8), 1):
            if df is not None:
                parts.append(df)
            if k % 2000 == 0:
                log.info("Locations: %d / %d files read", k, len(paths))
    locs = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=LOC_COLS)
    n_all = len(locs)
    locs = locs.merge(kept, on="copy_id", how="inner", suffixes=("", "_kept"))
    # The copy_id must point to the same activity as in stage 1
    assert (locs["iati_identifier"].isna() | (locs["iati_identifier"] == locs["iati_identifier_kept"])).all()
    locs = locs.drop(columns="iati_identifier").rename(columns={"iati_identifier_kept": "iati_identifier"})
    log.info("Locations: %d read, %d in kept activity copies", n_all, len(locs))
    return locs[["copy_id", "iati_identifier", *LOC_COLS[2:]]]


def locations_up_to_date(out_dir: Path) -> bool:
    """True if locations.parquet exists and is newer than stage 2's activities."""
    f = out_dir / "locations.parquet"
    return f.exists() and f.stat().st_mtime > (out_dir / "activities_clean.parquet").stat().st_mtime


# =============================================================================
# Part D: checking points against countries
# =============================================================================
def load_borders() -> dict:
    """Country borders (config.COUNTRY_BORDERS), widened by
    config.POINT_COUNTRY_TOLERANCE_DEG, as {iso2: prepared shapely geometry}, plus
    the Natural Earth label point of each country as a DataFrame."""
    d = json.loads(Path(config.COUNTRY_BORDERS).read_text())
    geoms, labels = {}, []
    for f in d["features"]:
        p = f["properties"]
        g = shapely.make_valid(shapely.geometry.shape(f["geometry"])).buffer(
            config.POINT_COUNTRY_TOLERANCE_DEG
        )
        shapely.prepare(g)
        geoms[p["iso2"]] = g
        labels.append(
            {"iso2": p["iso2"], "country_name": p["name"], "lon": p["label_lon"], "lat": p["label_lat"]}
        )
    return {"geoms": geoms, "labels": pd.DataFrame(labels)}


def check_points(locs: pd.DataFrame, act: pd.DataFrame, borders: dict) -> pd.DataFrame:
    """Add to each location:
    in_recipient_country  TRUE if a valid point lies inside one of its activity's
                          recipient countries (stage 6 placement, part A); NA if the
                          point is not valid or the activity has no country
    country_level         TRUE if coded as a whole country (feature PCL...)
    point_usable          valid, in a recipient country, and not country-level"""
    valid = locs["point_status"] == "valid"
    # One row per point x recipient country; "row" is the point's row in locs
    pairs = locs.loc[valid, ["copy_id", "lon", "lat"]].rename_axis("row").reset_index()
    pairs = pairs.merge(act[["copy_id", "countries"]], on="copy_id").dropna(subset=["countries"])
    pairs = pairs.assign(c=pairs["countries"].str.split(";")).explode("c").reset_index(drop=True)
    pairs["hit"] = False
    for code, g in pairs.groupby("c"):  # one vectorised test per country
        if code in borders["geoms"]:
            pairs.loc[g.index, "hit"] = shapely.contains_xy(
                borders["geoms"][code], g["lon"].to_numpy(), g["lat"].to_numpy()
            )
    inside = pairs.groupby("row")["hit"].any()
    out = locs.copy()
    out["in_recipient_country"] = inside.reindex(out.index).astype("boolean")
    out["country_level"] = out["feature_designation"].fillna("").str.upper().str.startswith("PCL")
    out["point_usable"] = (
        valid & out["in_recipient_country"].fillna(False).astype(bool) & ~out["country_level"]
    )
    return out


def org_countries(refs: pd.Series, publishers: pd.DataFrame, iso2: set[str]) -> pd.DataFrame:
    """Country of registration of each organisation ref (see config.INTERNATIONAL_PREFIXES):
    the identifier's two-letter prefix if it is a country; otherwise the Registry's
    headquarters country. Returns org_ref, org_country, org_country_source
    ("identifier prefix", "registry headquarters" or "none"), international
    (XI-/XR-/XM- identifiers, except XM-DAC donor codes of national governments,
    below config.DAC_INTERNATIONAL_FROM)."""
    r = pd.Series(refs.dropna().unique(), name="org_ref")
    prefix = r.str.extract(r"^([A-Z]{2})-")[0]
    dac = pd.to_numeric(r.str.extract(r"^XM-DAC-(\d+)")[0], errors="coerce")
    no_country = prefix.isin(config.INTERNATIONAL_PREFIXES)
    national_dac = (dac < config.DAC_INTERNATIONAL_FROM).fillna(False).astype(bool)  # NA if no DAC number
    international = (no_country & ~national_dac).astype(bool)
    by_prefix = prefix.where(prefix.isin(iso2) & ~no_country)
    pub = publishers.assign(k=publishers["organisation_identifier"].str.strip().str.upper())
    hq = pub.dropna(subset=["hq_country"]).drop_duplicates("k").set_index("k")["hq_country"]
    by_registry = r.map(hq).astype("string").where(lambda s: s.isin(iso2))  # text even if hq is empty
    return pd.DataFrame(
        {
            "org_ref": r,
            "org_country": by_prefix.fillna(by_registry),
            "org_country_source": np.select(
                [by_prefix.notna(), by_registry.notna()],
                ["identifier prefix", "registry headquarters"],
                "none",
            ),
            "international": international,
        }
    )


# =============================================================================
# Part D: chains that end at a mapped location
# =============================================================================
def local_type_labels() -> set[str]:
    """Labels (as written in chains.csv) of the organisation types counted as local."""
    return {config.ORG_TYPE[c] for c in config.LOCAL_ORG_TYPES}


def end_problem(c: pd.DataFrame, locs: pd.DataFrame) -> pd.Series:
    """Why a chain's end activity has no usable point (None if it has one)."""
    flags = locs.assign(
        any_loc=1,
        valid=locs["point_status"] == "valid",
        inside=locs["in_recipient_country"].fillna(False).astype(bool),
        usable=locs["point_usable"],
    )
    b = flags.groupby("iati_identifier")[["any_loc", "valid", "inside", "usable"]].sum()
    b = b.reindex(c["end_activity"]).fillna(0).set_axis(c.index)
    reason = np.select(
        [b["any_loc"] == 0, b["valid"] == 0, b["inside"] == 0],
        ["no location", "no valid point", "point outside recipient country"],
        default="country-level point only",
    )
    return pd.Series(reason, index=c.index).where(b["usable"] == 0)


def geo_chains(chains: pd.DataFrame, locs: pd.DataFrame) -> pd.DataFrame:
    """Chains of config.SHOWCASE_MIN_STEPS+ steps, with the points of their end
    activity and of all their activities, the showcase rank and the counter-example
    rank (see rank_chains())."""
    valid = locs[locs["point_status"] == "valid"].groupby("iati_identifier").size()
    usable = locs[locs["point_usable"]].groupby("iati_identifier").size()
    adm = locs[locs["n_administrative"] > 0].groupby("iati_identifier").size()
    c = chains[chains["steps"] >= config.SHOWCASE_MIN_STEPS].copy()
    for out_col, path_col in [
        ("end_org", "path_orgs"),
        ("end_org_ref", "path_org_refs"),
        ("end_org_type", "path_org_types"),
    ]:
        c[out_col] = c[path_col].str.split(" > ").str[-1]
    c["end_local"] = c["end_org_type"].isin(local_type_labels())
    c["end_points"] = c["end_activity"].map(valid).fillna(0).astype(int)
    c["end_points_usable"] = c["end_activity"].map(usable).fillna(0).astype(int)
    c["end_admin_areas"] = c["end_activity"].map(adm).fillna(0).astype(int)
    c["end_problem"] = end_problem(c, locs)
    acts = c[["chain_id", "path_activities"]].assign(a=c["path_activities"].str.split(" > ")).explode("a")
    c["activities_with_points"] = c["chain_id"].map(
        acts.assign(p=acts["a"].isin(usable.index)).groupby("chain_id")["p"].sum()
    )
    c["showcase_rank"] = rank_chains(c[c["end_points_usable"] > 0]).reindex(c.index)
    c["showcase"] = c["showcase_rank"] <= config.SHOWCASE_N
    c["counterexample_rank"] = rank_chains(c[c["end_points_usable"] == 0]).reindex(c.index)
    c["counterexample"] = c["counterexample_rank"] <= config.COUNTEREXAMPLE_N
    cols = ["chain_id", "steps", "path_activities", "path_orgs", "path_org_refs", "path_org_types"]
    cols += ["weakest_evidence", "n_confirmed_steps", "countries", "first_year", "last_year", "end_activity"]
    cols += ["end_org", "end_org_ref", "end_org_type", "end_local", "end_points", "end_points_usable"]
    cols += ["end_admin_areas", "end_problem", "activities_with_points"]
    cols += ["showcase_rank", "showcase", "counterexample_rank", "counterexample"]
    return c[cols].sort_values(["showcase_rank", "counterexample_rank", "chain_id"], na_position="last")


def rank_chains(e: pd.DataFrame) -> pd.Series:
    """Rank chains as examples. Order: end publisher of a local type
    (config.LOCAL_ORG_TYPES) first, then more steps confirmed by both sides, then
    more steps, then more activities with usable points. To show different routes,
    only the best-ranked chain is kept for each organisation path, each end
    publisher, and each route before the end publisher (funder and intermediaries).
    A chain whose end publisher already appears earlier on it (money returning to
    an organisation it passed through) is not ranked.
    Used for the showcase (end activity has a usable point) and for the
    counter-example (it has none)."""
    e = e.sort_values(
        ["end_local", "n_confirmed_steps", "steps", "activities_with_points", "chain_id"],
        ascending=[False, False, False, False, True],
    )
    route = e["path_org_refs"].str.rsplit(" > ", n=1).str[0]
    returns = pd.Series(
        [end in r.split(" > ") for end, r in zip(e["end_org_ref"], route, strict=True)], index=e.index
    )  # per chain: a few thousand rows at most
    e = e[~returns]
    route = route[~returns]
    e = e.drop_duplicates("path_org_refs").drop_duplicates("end_org_ref")
    e = e[~route.reindex(e.index).duplicated()]
    return pd.Series(np.arange(1, len(e) + 1), index=e.index)


def chain_activities(g: pd.DataFrame) -> pd.DataFrame:
    """The activities of chains g in long form: chain_id, position (0 = origin), is_end,
    iati_identifier, org_ref."""
    a = g[["chain_id"]].assign(
        iati_identifier=g["path_activities"].str.split(" > "), org_ref=g["path_org_refs"].str.split(" > ")
    )
    a = a.explode(["iati_identifier", "org_ref"])
    a["position"] = a.groupby("chain_id").cumcount()
    a["is_end"] = a["position"] == a["chain_id"].map(g.set_index("chain_id")["steps"])
    return a


def geo_chain_points(gc: pd.DataFrame, locs: pd.DataFrame, steps: pd.DataFrame) -> pd.DataFrame:
    """The valid points of every activity on the chains whose end activity has a valid
    point, and on the counter-example chains, with the check result of each point.
    position: 0 = origin activity, then 1, 2, ... along the chain."""
    g = gc[(gc["end_points"] > 0) | gc["counterexample"]]
    a = chain_activities(g).merge(g[["chain_id", "showcase_rank", "counterexample_rank"]], on="chain_id")
    names = pd.concat(
        [
            steps[["from_activity", "from_org_name"]].set_axis(["a", "n"], axis=1),
            steps[["to_activity", "to_org_name"]].set_axis(["a", "n"], axis=1),
        ]
    ).drop_duplicates("a")
    a["org_name"] = a["iati_identifier"].map(names.set_index("a")["n"])
    keep = ["iati_identifier", "loc_seq", "name", "lat", "lon", "exactness", "location_reach"]
    keep += ["feature_designation", "in_recipient_country", "country_level", "point_usable"]
    p = locs.loc[locs["point_status"] == "valid", keep]
    out = a.drop(columns="org_ref").merge(p, on="iati_identifier", how="inner")
    return out.rename(columns={"name": "location_name"}).sort_values(["chain_id", "position", "loc_seq"])


def geo_chain_orgs(gc: pd.DataFrame, oc: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """The organisations of the showcase and counter-example chains, with their role,
    their country of registration and the map position of that country."""
    g = gc[gc["showcase"] | gc["counterexample"]]
    a = chain_activities(g)
    assert (g["path_orgs"].str.count(" > ") == g["steps"]).all(), "an organisation name contains ' > '"
    a["org_name"] = g.assign(n=g["path_orgs"].str.split(" > ")).explode("n")["n"].to_numpy()
    a["org_type"] = g.assign(t=g["path_org_types"].str.split(" > ")).explode("t")["t"].to_numpy()
    a["role"] = np.select([a["position"] == 0, a["is_end"]], ["funder", "implementer"], "intermediary")
    a = a.merge(oc, on="org_ref", how="left").merge(
        labels.rename(columns={"iso2": "org_country"}), on="org_country", how="left"
    )
    cols = ["chain_id", "position", "role", "iati_identifier", "org_ref", "org_name", "org_type"]
    cols += ["org_country", "org_country_source", "international", "country_name", "lon", "lat"]
    return a[cols].sort_values(["chain_id", "position"])


def geo_chain_context(chain_orgs: pd.DataFrame, org_edges: pd.DataFrame, oc: pd.DataFrame) -> pd.DataFrame:
    """Other organisation links of each example chain's organisations (grey context
    lines): their config.CONTEXT_FLOWS_N largest outgoing links by transactions, to
    organisations not on the chain and with a known country."""
    pairs = chain_orgs[["chain_id", "org_ref"]].rename(columns={"org_ref": "from_org"})
    e = pairs.merge(org_edges, on="from_org")
    on_chain = e.set_index(["chain_id", "to_org"]).index.isin(
        chain_orgs.set_index(["chain_id", "org_ref"]).index
    )
    e = e[~on_chain].merge(
        oc[["org_ref", "org_country"]].rename(columns={"org_ref": "to_org", "org_country": "to_country"}),
        on="to_org",
    )
    e = e.dropna(subset=["to_country"]).sort_values(["chain_id", "n_transactions"], ascending=[True, False])
    e = e.groupby("chain_id").head(config.CONTEXT_FLOWS_N)
    return e[["chain_id", "from_org", "to_org", "to_org_name", "to_country", "n_transactions"]]


# =============================================================================
# Part B: flows between countries
# =============================================================================
def country_flows(edges: pd.DataFrame, act: pd.DataFrame, oc: pd.DataFrame) -> pd.DataFrame:
    """Network links (unit: links) by the funder organisation's country of
    registration and the receiving activity's recipient country.

    from_country   country of registration of the funding organisation (from_org),
                   as in org_countries(); "unknown" if none
    to_place       the receiving activity's recipient country if it names exactly
                   one; otherwise "several countries", "region only" or
                   "no country or region"
    Counts per pair: links, links_confirmed (reported by both sides),
    links_receiver_registered_here (the receiving organisation is registered in
    to_place: a local implementer), and whether the pair is domestic."""
    net = edges[edges["in_network"]]
    org = oc.set_index("org_ref")
    f = pd.DataFrame(
        {
            "from_country": net["from_org"].map(org["org_country"]).fillna("unknown").to_numpy(),
            "from_international": net["from_org"]
            .map(org["international"])
            .fillna(False)
            .astype(bool)
            .to_numpy(),
            "to_place": net["to_activity"]
            .map(activity_place(act, one_country_code=True))
            .fillna("no country or region")
            .to_numpy(),
            "to_org_country": net["to_org"].map(org["org_country"]).to_numpy(),
            "confirmed": (net["evidence"] == "confirmed").to_numpy(),
        }
    )
    f["registered_here"] = f["to_org_country"] == f["to_place"]
    out = f.groupby(["from_country", "from_international", "to_place"], as_index=False).agg(
        links=("confirmed", "size"),
        links_confirmed=("confirmed", "sum"),
        links_receiver_registered_here=("registered_here", "sum"),
    )
    out["to_is_country"] = out["to_place"].str.fullmatch(config.COUNTRY_CODE_PATTERN)
    out["domestic"] = out["from_country"] == out["to_place"]
    assert out["links"].sum() == len(net)
    return out.sort_values("links", ascending=False)


def country_flows_by_year(
    edges: pd.DataFrame, act: pd.DataFrame, oc: pd.DataFrame, link_years: pd.DataFrame, years: list[int]
) -> pd.DataFrame:
    """country_flows() for the network links with at least one link transaction dated
    in each year (link_years: from_activity, to_activity, transaction_year, from
    link_transactions.parquet). The network is the global one; a year only selects
    links. Added November 2026 for the working paper."""
    out = []
    for y in years:
        keys = link_years.loc[link_years["transaction_year"] == y, ["from_activity", "to_activity"]].drop_duplicates()
        e = edges.merge(keys, on=["from_activity", "to_activity"], how="inner")
        out.append(country_flows(e, act, oc).assign(year=y))
    res = pd.concat(out, ignore_index=True)
    return res[["year", *[c for c in res.columns if c != "year"]]]


def local_link_steps(
    edges: pd.DataFrame, act: pd.DataFrame, oc: pd.DataFrame, steps: pd.DataFrame, chains: pd.DataFrame
) -> pd.DataFrame:
    """Every network link received by a local organisation (the receiving organisation is
    registered in the receiving activity's recipient country, which must be exactly one; as
    links_receiver_registered_here in country_flows()), with the number of steps from a
    chain origin to that organisation: the position of the link in the chain (1 = funded
    directly by the chain's first funder, 2 = through one intermediary, ...). Where several
    chains pass through the link, the fewest steps; the origin funder is that of the chain
    with the fewest steps (lowest chain_id if tied). Links in no chain (inside a cycle) have
    no steps. steps: chain_steps.csv (chain_id, step, from_activity, to_activity);
    chains: chains.csv (chain_id, path_org_refs). Added November 2026."""
    net = edges[edges["in_network"]]
    org = oc.set_index("org_ref")
    place = net["to_activity"].map(activity_place(act, one_country_code=True))
    loc = net.assign(to_place=place.to_numpy(), to_org_country=net["to_org"].map(org["org_country"]).to_numpy())
    loc = loc[loc["to_place"].str.fullmatch(config.COUNTRY_CODE_PATTERN, na=False) & (loc["to_org_country"] == loc["to_place"])]
    st = steps.merge(chains[["chain_id", "path_org_refs"]], on="chain_id")
    st["origin_org"] = st["path_org_refs"].str.split(" > ").str[0]
    best = st.sort_values(["step", "chain_id"]).drop_duplicates(["from_activity", "to_activity"])
    out = loc[["from_activity", "to_activity", "from_org", "to_org", "to_place"]].merge(
        best[["from_activity", "to_activity", "step", "origin_org"]], on=["from_activity", "to_activity"], how="left"
    ).rename(columns={"step": "steps_from_origin"})
    out["origin_country"] = out["origin_org"].map(org["org_country"])
    out["origin_international"] = out["origin_org"].map(org["international"]).astype("boolean")
    out["steps_from_origin"] = out["steps_from_origin"].astype("Int64")
    return out.sort_values(["to_place", "steps_from_origin", "from_activity"]).reset_index(drop=True)


# =============================================================================
# Stage
# =============================================================================
def run(out_dir: Path) -> dict:
    """Run stage 6 on the outputs of stages 2-3 in out_dir. Returns the summary."""
    log = common.setup_logging(out_dir, STAGE)
    t0 = time.time()
    s5 = load_stage5()
    assert config.MULTI_COUNTRY_RULE in config.MULTI_COUNTRY_RULES

    edges = pd.read_parquet(
        common.require_file(out_dir / "activity_edges.parquet", "03_links.py"),
        columns=["from_activity", "to_activity", "from_org", "to_org", "evidence", "in_network"],
    )
    related = pd.read_parquet(
        out_dir / "related_activities_clean.parquet",
        columns=["iati_identifier", "related_activity_ref", "related_published", "same_publisher"],
    )
    connected = s5.connected_activities(edges, related)
    act = pd.read_parquet(out_dir / "activities_clean.parquet", columns=ACT_COLS)
    tx = pd.read_parquet(out_dir / "transactions_clean.parquet", columns=TX_COLS)
    tx = tx[~tx["exact_duplicate"].fillna(False)].drop(columns="exact_duplicate")
    tx["d3"], tx["n3"] = s5.indicator3_flags(tx)
    tx["d3"] = tx["d3"].fillna(False).astype(bool)
    tx["n3"] = tx["n3"].fillna(False).astype(bool)
    tx = tx.drop(columns=["transaction_type", "internal", "valid_receiver_ref"])
    tx["tx_countries"] = common.valid_countries(tx["tx_recipient_countries"])
    log.info("%d activities, %d transactions (exact duplicates left out)", len(act), len(tx))

    act = act.merge(activity_countries(act, tx), on="copy_id", how="left")
    # A transaction takes its activity's activity-level countries, else its own
    # (common.transaction_countries(), the rule stage 5 uses for its country subsets).
    own = act.set_index("copy_id")
    level = own["countries"].where(own["source"] == "activity")
    tx["countries"] = common.transaction_countries(tx["copy_id"], tx["tx_countries"], level)
    tx["percentages"] = tx["copy_id"].map(own["percentages"]).where(tx["copy_id"].map(level).notna())
    tx = tx.drop(columns=["tx_recipient_countries", "tx_countries"])
    assert act["iati_identifier"].is_unique

    ci, un = [], []
    for year in config.SPATIAL_YEARS:
        ci.append(indicators_by_country(act, tx, connected, year))
        un.append(unplaced(act, tx, connected, year))
    ci, un = pd.concat(ci, ignore_index=True), pd.concat(un, ignore_index=True)
    lu = links_unplaced(edges, act)
    assert (ci["numerator"] <= ci["denominator"] + 1e-9).all()
    assert lu.groupby("side")["links"].sum().nunique() == 1  # both sides count every network link
    # Under "split" each placed record has total weight 1, so the country
    # denominators add up to the number of placed records.
    sp = ci[(ci["scope"] == "global") & (ci["rule"] == "split") & (ci["indicator"] == "1")]
    assert (
        abs(sp["denominator"].sum() - (act["countries"].notna() & act["copy_id"].isin(tx["copy_id"])).sum())
        < 2
    )

    ci.to_csv(out_dir / "country_indicators.csv", index=False)
    un.to_csv(out_dir / "spatial_unplaced.csv", index=False)
    lu.to_csv(out_dir / "spatial_links_unplaced.csv", index=False)
    del tx  # part D does not need the transactions (frees memory, ~ rm() in R)

    # --- Part D: locations and chains that end at a mapped location
    # The XML is read only if stage 2 has been rerun; the points are always re-derived
    # from the published text, so a change to parse_points() needs no new read.
    if locations_up_to_date(out_dir):
        locs = pd.read_parquet(
            out_dir / "locations.parquet", columns=["copy_id", "iati_identifier", *LOC_COLS[2:]]
        )
        log.info("Locations: reusing locations.parquet (%d rows)", len(locs))
    else:
        bulk_dir = Path(common.read_summary(out_dir, "01_parse_bulk")["bulk_dir"])
        locs = read_locations(bulk_dir, out_dir, act[["copy_id", "iati_identifier"]], log)
    borders = load_borders()
    locs = check_points(parse_points(locs), act, borders)
    locs.to_parquet(out_dir / "locations.parquet", index=False)
    chains = common.read_csv(common.require_file(out_dir / "chains.csv", "04_network.py"))
    steps = common.read_csv(
        out_dir / "chain_steps.csv", usecols=["from_activity", "from_org_name", "to_activity", "to_org_name"]
    )
    gc = geo_chains(chains, locs)
    gp = geo_chain_points(gc, locs, steps)
    org_edges = pd.read_parquet(
        out_dir / "org_edges.parquet", columns=["from_org", "to_org", "to_org_name", "n_transactions"]
    )
    publishers = pd.read_parquet(
        out_dir / "publishers.parquet", columns=["organisation_identifier", "hq_country"]
    )
    oc = org_countries(
        pd.concat([org_edges["from_org"], org_edges["to_org"]]), publishers, set(borders["geoms"])
    )
    oc.to_csv(out_dir / "org_countries.csv", index=False)  # added for the working paper
    go = geo_chain_orgs(gc, oc, borders["labels"])
    gx = geo_chain_context(go, org_edges, oc)
    assert gc["showcase"].sum() <= config.SHOWCASE_N
    assert gc["counterexample"].sum() <= config.COUNTEREXAMPLE_N
    assert (gc.loc[gc["showcase"], "end_points_usable"] > 0).all()
    assert set(go["chain_id"]) == set(gc.loc[gc["showcase"] | gc["counterexample"], "chain_id"])
    gc.to_csv(out_dir / "geo_chains.csv", index=False)
    gp.to_csv(out_dir / "geo_chain_points.csv", index=False)
    go.to_csv(out_dir / "geo_chain_orgs.csv", index=False)
    gx.to_csv(out_dir / "geo_chain_context.csv", index=False)

    # --- Part B: flows between countries
    cf = country_flows(edges, act, oc)
    cf.to_csv(out_dir / "country_flows.csv", index=False)
    link_years = pd.read_parquet(out_dir / "link_transactions.parquet", columns=["from_activity", "to_activity", "transaction_year"])
    years = sorted({y for c, y in config.SUBSETS if c is None and y is not None})
    country_flows_by_year(edges, act, oc, link_years, years).to_csv(out_dir / "country_flows_by_year.csv", index=False)
    cs = common.read_csv(out_dir / "chain_steps.csv", usecols=["chain_id", "step", "from_activity", "to_activity"])
    ll = local_link_steps(edges, act, oc, cs, chains)
    ll.to_csv(out_dir / "local_links_steps.csv", index=False)
    assert len(ll) == int(cf["links_receiver_registered_here"].sum())
    cross = cf[cf["to_is_country"] & ~cf["domestic"] & (cf["from_country"] != "unknown")]
    valid = locs["point_status"] == "valid"
    net_acts = set(edges.loc[edges["in_network"], "from_activity"]) | set(
        edges.loc[edges["in_network"], "to_activity"]
    )

    g = ci[(ci["scope"] == "global") & ci["default_rule"]]
    s = {
        "multi_country_rule": config.MULTI_COUNTRY_RULE,
        "spatial_years": config.SPATIAL_YEARS,
        "activities_by_country_source": act["source"].fillna("none").value_counts().to_dict(),
        "countries_indicator1": int((g["indicator"] == "1").sum()),
        "countries_indicator1_base_30_plus": int(((g["indicator"] == "1") & ~g["small_base"]).sum()),
        "countries_indicator3": int((g["indicator"] == "3").sum()),
        "countries_indicator3_base_30_plus": int(((g["indicator"] == "3") & ~g["small_base"]).sum()),
        "rows_country_indicators": len(ci),
        "locations": len(locs),
        "locations_by_point_status": locs["point_status"].value_counts().to_dict(),
        "activities_with_location": int(locs["iati_identifier"].nunique()),
        "activities_with_valid_point": int(locs.loc[valid, "iati_identifier"].nunique()),
        "activities_with_administrative_area": int(
            locs.loc[locs["n_administrative"] > 0, "iati_identifier"].nunique()
        ),
        "network_activities": len(net_acts),
        "network_activities_with_valid_point": len(net_acts & set(locs.loc[valid, "iati_identifier"])),
        "chains_min_steps": int(len(gc)),
        "chains_min_steps_end_point": int((gc["end_points"] > 0).sum()),
        "chains_min_steps_end_point_local": int(((gc["end_points"] > 0) & gc["end_local"]).sum()),
        "valid_points_in_recipient_country": locs.loc[valid, "in_recipient_country"]
        .astype(str)
        .value_counts()
        .to_dict(),
        "valid_points_country_level": int((valid & locs["country_level"]).sum()),
        "usable_points": int(locs["point_usable"].sum()),
        "activities_with_usable_point": int(locs.loc[locs["point_usable"], "iati_identifier"].nunique()),
        "chains_min_steps_end_usable": int((gc["end_points_usable"] > 0).sum()),
        "chains_min_steps_end_usable_local": int(((gc["end_points_usable"] > 0) & gc["end_local"]).sum()),
        "chains_min_steps_by_end_problem": gc["end_problem"].fillna("usable point").value_counts().to_dict(),
        "showcase_candidates": int(gc["showcase_rank"].notna().sum()),
        "showcase_chains": gc.loc[gc["showcase"], "chain_id"].astype(int).tolist(),
        "counterexample_chains": gc.loc[gc["counterexample"], "chain_id"].astype(int).tolist(),
        "organisations_by_country_source": oc["org_country_source"].value_counts().to_dict(),
        "rows_geo_chain_points": len(gp),
        "rows_geo_chain_orgs": len(go),
        "rows_geo_chain_context": len(gx),
        "rows_country_flows": len(cf),
        "flows_links": int(cf["links"].sum()),
        "flows_links_funder_country_unknown": int(cf.loc[cf["from_country"] == "unknown", "links"].sum()),
        "flows_links_funder_international": int(cf.loc[cf["from_international"], "links"].sum()),
        "flows_links_to_one_country": int(cf.loc[cf["to_is_country"], "links"].sum()),
        "flows_links_domestic": int(cf.loc[cf["domestic"], "links"].sum()),
        "flows_links_cross_border": int(cross["links"].sum()),
        "flows_links_receiver_registered_in_recipient_country": int(
            cf.loc[cf["to_is_country"], "links_receiver_registered_here"].sum()
        ),
        "seconds": round(time.time() - t0),
    }
    common.write_summary(out_dir, STAGE, s)
    log.info("Done: %s", s)
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", type=Path, default=None)
    a = ap.parse_args()
    run(a.out_dir or config.output_dir())


if __name__ == "__main__":
    main()
