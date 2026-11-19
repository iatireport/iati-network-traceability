"""Run all stages in order and write run_manifest.json.

Usage (from the project folder):
  .venv/bin/python run_all.py                 all stages (stage 1 parses the 12 GB copy)
  .venv/bin/python run_all.py --from-stage 2  reuse stage 1 outputs, rerun 2-6
  .venv/bin/python run_all.py --from-stage 5  only recompute the indicators and stage 6
  .venv/bin/python run_all.py --from-stage 6  only recompute the spatial tables

The manifest records the bulk index date, files read / skipped / failed, the row
counts after every step, the settings used and the package versions. Each stage
also keeps its own log in <output dir>/logs/ and its counts in <output dir>/summaries/.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import common
import config

HERE = Path(__file__).resolve().parent
STAGES = ["01_parse_bulk", "02_clean", "03_links", "04_network", "05_indicators", "06_spatial"]


def load_stage(name: str):
    """Import a stage script (its name starts with a digit, so `import` cannot)."""
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # lets stage 1's parallel workers find the module
    spec.loader.exec_module(mod)
    return mod


def row_counts(out_dir: Path) -> dict:
    """Row counts after every step, from the stage summaries."""
    s1, s2, s3, s4 = (common.read_summary(out_dir, s) for s in STAGES[:4])
    return {
        "1_parse": {
            "xml_files_found": s1["xml_files_found"],
            "activity_files": s1["activity_files"],
            "organisation_files_skipped": s1["organisation_files"],
            "files_failed": s1["files_failed"],
            "activities_read": s1["activities_read_all_files"],
            "transactions_read": s1["transactions_read_all_files"],
            "activities_after_excluding_failed_files": s1["rows_activities"],
            "transactions_after_excluding_failed_files": s1["rows_transactions"],
        },
        "2_clean": {
            "activities_without_identifier_removed": s2["activity_copies_without_identifier"],
            "duplicate_activity_copies_removed": s2["activity_copies_removed"],
            "transactions_in_removed_copies": s2["transactions_in_removed_copies"],
            "activities": s2["distinct_activity_ids"],
            "transactions": s2["transactions_kept"],
            "exact_duplicate_transactions_flagged": s2["exact_duplicate_transactions"],
            "organisations": s2["organisations"],
        },
        "3_links": {
            "link_transactions": s3["link_transactions"],
            "activity_edges": s3["activity_edges"],
            "activity_edges_same_org": s3["activity_edges_same_org"],
            "activity_edges_by_evidence": s3["activity_edges_by_evidence"],
            "activity_edges_in_network": s3["activity_edges_in_network"],
            "org_edges": s3["org_edges"],
        },
        "4_network": {
            "network_activities": s4["activity_network"]["activities"],
            "network_edges": s4["activity_network"]["edges"],
            "cycles_after_excluding_implausible_links": s4["cycles_after_excluding_implausible_links"],
            "chains": s4["chains_total"],
            "origins": s4["origins"],
            "organisation_paths": s4["organisation_paths"],
            "longest_chain": s4["longest_chain"],
        },
    }


def settings_used() -> dict:
    """Every upper-case setting in config.py (sets written as sorted lists)."""
    out = {}
    for k in sorted(dir(config)):
        v = getattr(config, k)
        if k.isupper() and not callable(v):
            out[k] = sorted(v) if isinstance(v, set) else v
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-stage", type=int, default=1, choices=range(1, 7))
    ap.add_argument("--bulk-dir", type=Path, default=config.BULK_DIR)
    a = ap.parse_args()
    if a.from_stage == 1:
        config.bulk_index_date(a.bulk_dir)  # stops with a clear message if there is no bulk copy
    out = config.output_dir(a.bulk_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    timings = {}
    for k, name in enumerate(STAGES, 1):
        if k < a.from_stage:
            continue
        if k > 1:
            common.require_file(out / "summaries" / f"{STAGES[k - 2]}.json", f"{STAGES[k - 2]}.py")
        t0 = time.time()
        mod = load_stage(name)
        if k == 1:
            mod.run(a.bulk_dir, out, config.N_WORKERS)
        else:
            mod.run(out)
        timings[name] = round(time.time() - t0)

    s1 = common.read_summary(out, "01_parse_bulk")
    s5 = common.read_summary(out, "05_indicators")
    manifest = {
        "run_started_utc": started.isoformat(timespec="seconds"),
        "run_finished_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "stages_run": [s for k, s in enumerate(STAGES, 1) if k >= a.from_stage],
        "seconds_per_stage": timings,
        "code_version": common.git_version(),
        # When each stage's current outputs were made, and by which code (stages run
        # earlier, or on their own, keep the values they were written with)
        "stage_outputs": {
            s: {k: common.read_summary(out, s).get(k) for k in ("finished_utc", "code_version")}
            for s in STAGES
            if (out / "summaries" / f"{s}.json").exists()
        },
        "bulk_copy": {
            "path": s1["bulk_dir"],
            "index_created": s1["bulk_index_created"],
            "datasets_in_index": s1["datasets_in_index"],
            "datasets_missing_from_copy": s1["datasets_missing_from_copy"],
            "xml_files_read": s1["xml_files_found"],
            "files_skipped_organisation_files": s1["organisation_files"],
            "files_failed": s1["files_failed"],
            "failed_files": s1["failed_files"],
        },
        "row_counts": row_counts(out),
        "indicator_scopes": s5["scopes"],
        "settings": settings_used(),
        "packages": common.package_versions(),
    }
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    print(f"Manifest written to {out / 'run_manifest.json'}")


if __name__ == "__main__":
    main()
