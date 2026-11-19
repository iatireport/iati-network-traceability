"""Stage 1: read the bulk XML files into three tables.

Input   config.BULK_DIR (datasets/<publisher folder>/<dataset>.xml, plus the index files)
Output  <output dir>/
          activities.parquet          one row per activity copy (an activity in one file)
          transactions.parquet        one row per transaction
          related_activities.parquet  one row per <related-activity> element
          publishers.parquet          the registry's publisher list (reporting-orgs.json)
          files.csv                   one row per XML file: kind, version, counts, status
          datasets_missing.csv        datasets in the index with no file in the copy

Rules applied here (each is a named function below):
  - activity-level defaults (currency, flow, finance, aid and tied-status types)
    are copied onto transactions that do not give their own: apply_default()
  - transaction-type codes are recoded to version 2 numbers: recode_transaction_type()
  - names: English narrative if present, otherwise the first one: pick_name()
  - files that cannot be parsed are logged and excluded (config.KEEP_PARTIAL_FILES)

No row is dropped here apart from failed files. Duplicate activities are handled
in stage 2.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

import common
import config

STAGE = "01_parse_bulk"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Activity-date types: version 2 codes and version 1 names.
START_PLANNED, START_ACTUAL = {"1", "start-planned"}, {"2", "start-actual"}
END_PLANNED, END_ACTUAL = {"3", "end-planned"}, {"4", "end-actual"}

# ---------------------------------------------------------------------------
# Output schemas (fixed, so every part file has the same column types)
# ---------------------------------------------------------------------------
STR, INT, FLT, BOOL = pa.string(), pa.int64(), pa.float64(), pa.bool_()

ACTIVITY_SCHEMA = pa.schema(
    [
        ("copy_id", INT),
        ("publisher_folder", STR),
        ("file", STR),
        ("position_in_file", INT),
        ("iati_version", STR),
        ("iati_identifier", STR),
        ("reporting_org_ref", STR),
        ("reporting_org_type", STR),
        ("reporting_org_name", STR),
        ("secondary_reporter", BOOL),
        ("last_updated_datetime", STR),
        ("hierarchy", STR),
        ("activity_status", STR),
        ("title", STR),
        ("default_currency", STR),
        ("default_flow_type", STR),
        ("default_finance_type", STR),
        ("default_aid_type", STR),
        ("default_tied_status", STR),
        ("recipient_countries", STR),
        ("recipient_country_percentages", STR),
        ("recipient_regions", STR),
        ("start_date", STR),
        ("end_date", STR),
        ("n_transactions", INT),
    ]
)

TRANSACTION_SCHEMA = pa.schema(
    [
        ("copy_id", INT),
        ("tx_seq", INT),
        ("publisher_folder", STR),
        ("file", STR),
        ("iati_identifier", STR),
        ("reporting_org_ref", STR),
        ("transaction_ref", STR),
        ("transaction_type_raw", STR),
        ("transaction_type", STR),
        ("type_recode", STR),
        ("transaction_date", STR),
        ("transaction_year", INT),
        ("date_valid", BOOL),
        ("value", FLT),
        ("value_valid", BOOL),
        ("currency", STR),
        ("currency_source", STR),
        ("value_date", STR),
        ("flow_type", STR),
        ("finance_type", STR),
        ("aid_type", STR),
        ("tied_status", STR),
        ("provider_org_ref", STR),
        ("provider_org_type", STR),
        ("provider_org_name", STR),
        ("provider_activity_id", STR),
        ("receiver_org_ref", STR),
        ("receiver_org_type", STR),
        ("receiver_org_name", STR),
        ("receiver_activity_id", STR),
        ("tx_recipient_countries", STR),
    ]
)

RELATED_SCHEMA = pa.schema(
    [
        ("copy_id", INT),
        ("iati_identifier", STR),
        ("related_activity_ref", STR),
        ("related_activity_type", STR),
    ]
)

SCHEMAS = {"act": ACTIVITY_SCHEMA, "tx": TRANSACTION_SCHEMA, "rel": RELATED_SCHEMA}
FINAL_NAMES = {
    "act": "activities.parquet",
    "tx": "transactions.parquet",
    "rel": "related_activities.parquet",
}


# =============================================================================
# Small rules, applied to one element or value
# =============================================================================
def strip_or_none(x: str | None) -> str | None:
    """Trim a string; blank becomes None."""
    if x is None:
        return None
    x = x.strip()
    return x or None


def pick_name(el: etree._Element | None, default_lang: str | None) -> str | None:
    """Name held by an element: the English <narrative> if there is one, otherwise
    the first non-blank narrative, otherwise the element's own text (version 1).
    A narrative without xml:lang takes the activity's language."""
    if el is None:
        return None
    first = None
    for n in el.iterchildren("narrative"):
        t = strip_or_none(n.text)
        if not t:
            continue
        lang = (n.get(XML_LANG) or default_lang or "").lower()
        if lang.startswith("en"):
            return t
        if first is None:
            first = t
    return first or strip_or_none(el.text)


def recode_transaction_type(raw: str | None) -> tuple[str | None, str]:
    """Version 2 transaction-type code and how it was obtained.

    Returns (code, how) with how one of:
      "as published"  already a valid code (1-13)
      "v1 code"       version 1 text code (C, D, E, IF, ...) recoded
      "name"          a type name (e.g. "Disbursement") recoded
      "missing"       no code given; code is None
      "invalid"       anything else (e.g. a date); code is None
    """
    if raw is None:
        return None, "missing"
    r = raw.strip().upper()
    if not r:
        return None, "missing"
    if r in config.VALID_TRANSACTION_TYPES:
        return r, "as published"
    if r in config.V1_TRANSACTION_TYPES:
        return config.V1_TRANSACTION_TYPES[r], "v1 code"
    if r in config.NAMED_TRANSACTION_TYPES:
        return config.NAMED_TRANSACTION_TYPES[r], "name"
    return None, "invalid"


def apply_default(own: str | None, default: str | None) -> str | None:
    """The transaction's own value if given, otherwise the activity default."""
    return own if own else default


def parse_value(text: str | None) -> tuple[float | None, bool]:
    """Numeric value and whether it was readable. Blank counts as not readable."""
    t = strip_or_none(text)
    if t is None:
        return None, False
    try:
        return float(t.replace(",", "")), True
    except ValueError:
        return None, False


def is_secondary(flag: str | None) -> bool:
    """True if secondary-reporter means 'not the primary reporter'."""
    return (flag or "").strip().lower() in config.SECONDARY_REPORTER_TRUE


# =============================================================================
# One file
# =============================================================================
def empty_columns(kind: str) -> dict[str, list]:
    return {name: [] for name in SCHEMAS[kind].names}


def parse_activity(
    act: etree._Element,
    copy_id: int,
    folder: str,
    file: str,
    position: int,
    version: str | None,
    cols: dict[str, dict[str, list]],
) -> int:
    """Append one activity, its transactions and related-activity entries to cols.
    Returns the number of transactions."""
    lang = act.get(XML_LANG)
    a = {
        "iati_identifier": None,
        "reporting_org_ref": None,
        "reporting_org_type": None,
        "reporting_org_name": None,
        "secondary_reporter": False,
        "title": None,
        "activity_status": None,
        "default_flow_type": None,
        "default_finance_type": None,
        "default_aid_type": None,
        "default_tied_status": None,
    }
    countries, percentages, regions, txs, related = [], [], [], [], []
    dates: dict[str, str] = {}
    for ch in act:
        tag = ch.tag
        if not isinstance(tag, str):  # comments and processing instructions
            continue
        if tag == "iati-identifier":
            a["iati_identifier"] = strip_or_none(ch.text)
        elif tag == "transaction":
            txs.append(ch)
        elif tag == "reporting-org":
            a["reporting_org_ref"] = strip_or_none(ch.get("ref"))
            a["reporting_org_type"] = strip_or_none(ch.get("type"))
            a["reporting_org_name"] = pick_name(ch, lang)
            a["secondary_reporter"] = is_secondary(ch.get("secondary-reporter"))
        elif tag == "title":
            a["title"] = pick_name(ch, lang)
        elif tag == "recipient-country":
            code = strip_or_none(ch.get("code"))
            if code:
                countries.append(code.upper())
                percentages.append(strip_or_none(ch.get("percentage")) or "")
        elif tag == "recipient-region":
            code = strip_or_none(ch.get("code"))
            if code:
                regions.append(code)
        elif tag == "activity-date":
            t = (ch.get("type") or "").strip().lower()
            d = (ch.get("iso-date") or "").strip()[:10]
            if d and t not in dates:
                dates[t] = d
        elif tag == "activity-status":
            a["activity_status"] = strip_or_none(ch.get("code"))
        elif tag in ("default-flow-type", "default-finance-type", "default-tied-status"):
            a[tag.replace("-", "_")] = strip_or_none(ch.get("code"))
        elif tag == "default-aid-type" and a["default_aid_type"] is None:
            a["default_aid_type"] = strip_or_none(ch.get("code"))
        elif tag == "related-activity":
            related.append((strip_or_none(ch.get("ref")), strip_or_none(ch.get("type"))))

    def first_date(*types: set[str]) -> str | None:
        for ts in types:
            for t in ts:
                if t in dates:
                    return dates[t]
        return None

    rc = cols["rel"]
    for ref, rtype in related:
        rc["copy_id"].append(copy_id)
        rc["iati_identifier"].append(a["iati_identifier"])
        rc["related_activity_ref"].append(ref)
        rc["related_activity_type"].append(rtype)

    default_currency = strip_or_none(act.get("default-currency"))
    ac = cols["act"]
    for k, v in a.items():
        ac[k].append(v)
    ac["copy_id"].append(copy_id)
    ac["publisher_folder"].append(folder)
    ac["file"].append(file)
    ac["position_in_file"].append(position)
    ac["iati_version"].append(version)
    ac["last_updated_datetime"].append(strip_or_none(act.get("last-updated-datetime")))
    ac["hierarchy"].append(strip_or_none(act.get("hierarchy")))
    ac["default_currency"].append(default_currency)
    ac["recipient_countries"].append(";".join(countries) or None)
    ac["recipient_country_percentages"].append(";".join(percentages) if countries else None)
    ac["recipient_regions"].append(";".join(regions) or None)
    ac["start_date"].append(first_date(START_ACTUAL, START_PLANNED))
    ac["end_date"].append(first_date(END_ACTUAL, END_PLANNED))
    ac["n_transactions"].append(len(txs))

    tc = cols["tx"]
    for seq, t in enumerate(txs, 1):
        parse_transaction(t, a, default_currency, lang, tc)
        tc["copy_id"].append(copy_id)
        tc["tx_seq"].append(seq)
        tc["publisher_folder"].append(folder)
        tc["file"].append(file)
        tc["iati_identifier"].append(a["iati_identifier"])
        tc["reporting_org_ref"].append(a["reporting_org_ref"])
    return len(txs)


def parse_transaction(
    t: etree._Element,
    a: dict,
    default_currency: str | None,
    lang: str | None,
    tc: dict[str, list],
) -> None:
    """Append the fields of one <transaction> to the transaction columns."""
    f: dict[str, str | None] = {}
    value_text = None
    tx_countries = []
    for ch in t:
        tag = ch.tag
        if not isinstance(tag, str):
            continue
        if tag == "transaction-type":
            f["type_raw"] = ch.get("code")
        elif tag == "transaction-date":
            f["date"] = (ch.get("iso-date") or "").strip()[:10]
        elif tag == "value":
            value_text = ch.text
            f["currency"] = strip_or_none(ch.get("currency"))
            f["value_date"] = strip_or_none((ch.get("value-date") or "")[:10])
        elif tag in ("provider-org", "receiver-org"):
            side = tag.split("-")[0]
            f[f"{side}_org_ref"] = strip_or_none(ch.get("ref"))
            f[f"{side}_org_type"] = strip_or_none(ch.get("type"))
            f[f"{side}_org_name"] = pick_name(ch, lang)
            f[f"{side}_activity_id"] = strip_or_none(ch.get(f"{side}-activity-id"))
        elif tag in ("flow-type", "finance-type", "tied-status"):
            f[tag] = strip_or_none(ch.get("code"))
        elif tag == "aid-type" and "aid-type" not in f:
            f["aid-type"] = strip_or_none(ch.get("code"))
        elif tag == "recipient-country":
            code = strip_or_none(ch.get("code"))
            if code:
                tx_countries.append(code.upper())

    code, how = recode_transaction_type(f.get("type_raw"))
    date = f.get("date") or None
    date_ok = bool(date and DATE_RE.match(date))
    value, value_ok = parse_value(value_text)
    own_currency = f.get("currency")

    tc["transaction_ref"].append(strip_or_none(t.get("ref")))
    tc["transaction_type_raw"].append(strip_or_none(f.get("type_raw")))
    tc["transaction_type"].append(code)
    tc["type_recode"].append(how)
    tc["transaction_date"].append(date)
    tc["transaction_year"].append(int(date[:4]) if date_ok else None)
    tc["date_valid"].append(date_ok)
    tc["value"].append(value)
    tc["value_valid"].append(value_ok)
    tc["currency"].append(apply_default(own_currency, default_currency))
    tc["currency_source"].append(
        "transaction" if own_currency else "activity default" if default_currency else "missing"
    )
    tc["value_date"].append(f.get("value_date"))
    tc["flow_type"].append(apply_default(f.get("flow-type"), a["default_flow_type"]))
    tc["finance_type"].append(apply_default(f.get("finance-type"), a["default_finance_type"]))
    tc["aid_type"].append(apply_default(f.get("aid-type"), a["default_aid_type"]))
    tc["tied_status"].append(apply_default(f.get("tied-status"), a["default_tied_status"]))
    for side in ("provider", "receiver"):
        for part in ("org_ref", "org_type", "org_name", "activity_id"):
            tc[f"{side}_{part}"].append(f.get(f"{side}_{part}"))
    tc["tx_recipient_countries"].append(";".join(tx_countries) or None)


def parse_file(path: Path, file_idx: int, parts_dir: Path) -> dict:
    """Parse one XML file and write its rows to three part files in parts_dir.

    Returns a dict describing the file (kind, version, counts, status). A file that
    raises a parse error keeps the counts read before the error; its rows are
    written only if config.KEEP_PARTIAL_FILES is True."""
    folder, file = path.parent.name, path.stem
    info = {
        "file_idx": file_idx,
        "publisher_folder": folder,
        "file": file,
        "bytes": path.stat().st_size,
        "kind": "unknown",
        "iati_version": None,
        "activities": 0,
        "transactions": 0,
        "status": "ok",
        "error": None,
    }
    cols = {k: empty_columns(k) for k in SCHEMAS}
    version = None
    try:
        ctx = etree.iterparse(
            str(path),
            events=("start", "end"),
            tag=("iati-activities", "iati-organisations", "iati-activity"),
            huge_tree=True,
            resolve_entities=False,
            no_network=True,
        )
        for event, el in ctx:
            if event == "start":
                if el.tag == "iati-activities":
                    info["kind"] = "activity"
                    version = info["iati_version"] = strip_or_none(el.get("version"))
                elif el.tag == "iati-organisations":
                    info["kind"] = "organisation"
                    break  # organisation files hold no transactions
                continue
            if el.tag != "iati-activity":
                continue
            copy_id = file_idx * 1_000_000 + info["activities"]
            info["transactions"] += parse_activity(
                el, copy_id, folder, file, info["activities"], version, cols
            )
            info["activities"] += 1
            el.clear(keep_tail=True)  # free memory as we go (~ rm() in R)
            while el.getprevious() is not None:
                del el.getparent()[0]
    except (etree.XMLSyntaxError, OSError, UnicodeDecodeError, ValueError) as e:
        info["status"] = "failed"
        info["error"] = f"{type(e).__name__}: {e}"

    # A file that parses but has neither root element is not read. The usual cause is
    # a root element in an XML namespace (xmlns="..."), which the tag filter above does
    # not match. It is reported as failed, so it is logged and counted, not lost.
    if info["status"] == "ok" and info["kind"] == "unknown":
        root = getattr(ctx, "root", None)
        info["status"] = "failed"
        info["error"] = f"Root element {None if root is None else root.tag!r} is not iati-activities"

    if info["status"] == "ok" or config.KEEP_PARTIAL_FILES:
        for kind, c in cols.items():
            if c["copy_id"]:
                table = pa.table(c, schema=SCHEMAS[kind])
                pq.write_table(table, parts_dir / f"{kind}_{file_idx:06d}.parquet")
    return info


# =============================================================================
# Whole copy
# =============================================================================
def list_files(bulk_dir: Path) -> list[Path]:
    """All XML files in the copy, in a fixed order (folder, then file name)."""
    return sorted((bulk_dir / "datasets").glob("*/*.xml"))


def combine_parts(parts_dir: Path, out_dir: Path, kind: str) -> int:
    """Concatenate the part files of one kind, in file order, into one Parquet file.
    Streams one part at a time, so memory use stays small. Returns the row count."""
    parts = sorted(parts_dir.glob(f"{kind}_*.parquet"))
    out = out_dir / FINAL_NAMES[kind]
    n = 0
    with pq.ParquetWriter(out, SCHEMAS[kind], compression="zstd") as w:
        for p in parts:
            t = pq.read_table(p, schema=SCHEMAS[kind])
            w.write_table(t, row_group_size=500_000)
            n += t.num_rows
    return n


def read_publishers(bulk_dir: Path) -> pd.DataFrame:
    """Registry publisher list: folder name (short_name), identifiers, name, type."""
    with open(bulk_dir / "reporting-orgs.json") as fh:
        orgs = json.load(fh)["reporting_orgs"]
    keep = [
        "short_name",
        "organisation_identifier",
        "iati_identifier",
        "human_readable_name",
        "organisation_type",
        "hq_country",
        "reporting_source_type",
    ]
    df = pd.DataFrame([{k: o.get(k) for k in keep} for o in orgs])
    return df.rename(columns={"short_name": "publisher_folder", "human_readable_name": "publisher_name"})


def missing_datasets(bulk_dir: Path, present: set[tuple[str, str]]) -> pd.DataFrame:
    """Datasets listed in the index that have no XML file in the copy, with the
    reason from the last download attempt where datasets-full.json gives one."""
    full = bulk_dir / "datasets-full.json"
    src = full if full.exists() else bulk_dir / "datasets-minimal.json"
    with open(src) as fh:
        datasets = json.load(fh)["datasets"]
    rows = []
    for d in datasets:
        key = (d.get("reporting_org_short_name"), d.get("short_name"))
        if key in present:
            continue
        attempt = d.get("most_recent_get_attempt") or {}
        rows.append(
            {
                "publisher_folder": key[0],
                "dataset": key[1],
                "source_url": d.get("source_url"),
                "last_attempt": attempt.get("timestamp") if isinstance(attempt, dict) else None,
                "last_attempt_result": json.dumps(attempt)[:300] if attempt else None,
            }
        )
    return pd.DataFrame(rows)


def run(bulk_dir: Path, out_dir: Path, n_workers: int) -> dict:
    """Parse the whole copy. Returns the stage summary (also written to disk)."""
    log = common.setup_logging(out_dir, STAGE)
    t0 = time.time()
    files = list_files(bulk_dir)
    assert files, f"No XML files under {bulk_dir / 'datasets'}"
    log.info("Bulk copy %s: %d XML files, %d workers", bulk_dir, len(files), n_workers)

    parts_dir = out_dir / "_parts"
    shutil.rmtree(parts_dir, ignore_errors=True)
    parts_dir.mkdir(parents=True)

    # Largest files first, so no worker is left with a big file at the end.
    order = sorted(range(len(files)), key=lambda i: -files[i].stat().st_size)
    infos = []
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = [pool.submit(parse_file, files[i], i, parts_dir) for i in order]
        for k, fut in enumerate(as_completed(futures), 1):
            info = fut.result()
            infos.append(info)
            if info["status"] == "failed":
                log.warning("Failed: %s/%s.xml (%s)", info["publisher_folder"], info["file"], info["error"])
            if k % 1000 == 0 or k == len(files):
                n_tx = sum(i["transactions"] for i in infos)
                log.info("%d / %d files, %d transactions, %.0fs", k, len(files), n_tx, time.time() - t0)

    files_df = pd.DataFrame(infos).sort_values("file_idx")
    files_df.to_csv(out_dir / "files.csv", index=False)

    log.info("Combining part files")
    rows = {kind: combine_parts(parts_dir, out_dir, kind) for kind in SCHEMAS}
    shutil.rmtree(parts_dir)

    publishers = read_publishers(bulk_dir)
    publishers.to_parquet(out_dir / "publishers.parquet", index=False)
    present = set(zip(files_df["publisher_folder"], files_df["file"], strict=True))
    missing = missing_datasets(bulk_dir, present)
    missing.to_csv(out_dir / "datasets_missing.csv", index=False)

    ok = files_df["status"] == "ok"
    failed = files_df[~ok]
    kept = ok | config.KEEP_PARTIAL_FILES
    summary = {
        "bulk_dir": str(bulk_dir),
        "bulk_index_created": json.load(open(bulk_dir / "datasets-minimal.json"))["index_created"],
        "datasets_in_index": int(len(present) + len(missing)),
        "datasets_missing_from_copy": int(len(missing)),
        "xml_files_found": int(len(files_df)),
        "activity_files": int((files_df["kind"] == "activity").sum()),
        "organisation_files": int((files_df["kind"] == "organisation").sum()),
        "version_1_activity_files": int(files_df["iati_version"].fillna("").str.startswith("1").sum()),
        "files_failed": int(len(failed)),
        "failed_files": failed[["publisher_folder", "file", "error"]].to_dict("records"),
        "activities_read_all_files": int(files_df["activities"].sum()),
        "transactions_read_all_files": int(files_df["transactions"].sum()),
        "activities_in_failed_files": int(failed["activities"].sum()),
        "transactions_in_failed_files": int(failed["transactions"].sum()),
        "keep_partial_files": config.KEEP_PARTIAL_FILES,
        "rows_activities": rows["act"],
        "rows_transactions": rows["tx"],
        "rows_related_activities": rows["rel"],
        "rows_publishers": int(len(publishers)),
        "seconds": round(time.time() - t0),
    }
    # Hand-off checks: the combined tables hold exactly the rows of the kept files.
    assert rows["act"] == int(files_df.loc[kept, "activities"].sum())
    # copy_id = file index x 1,000,000 + position in the file, so a file must hold
    # fewer than 1,000,000 activities (the largest in the copy of 30 October 2026: 9,165)
    assert files_df["activities"].max() < 1_000_000, "a file has 1,000,000+ activities: copy_id repeats"
    assert rows["tx"] == int(files_df.loc[kept, "transactions"].sum())
    common.write_summary(out_dir, STAGE, summary)
    log.info(
        "Done: %d activities, %d transactions kept; %d read in all files; %d files failed (%.0fs)",
        rows["act"],
        rows["tx"],
        summary["transactions_read_all_files"],
        len(failed),
        time.time() - t0,
    )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bulk-dir", type=Path, default=config.BULK_DIR)
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--workers", type=int, default=config.N_WORKERS)
    a = ap.parse_args()
    out = a.out_dir or config.output_dir(a.bulk_dir)
    out.mkdir(parents=True, exist_ok=True)
    run(a.bulk_dir, out, a.workers)


if __name__ == "__main__":
    main()
