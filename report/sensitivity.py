"""Rerun the indicators once per alternative setting for the open questions, and save
the main indicators to <output dir>/sensitivity.csv.

Most alternatives only change stage 5: it is rerun in the output folder, and the
default outputs are restored at the end (stage 5 is run once more with the settings
in config.py). Alternatives that change which links exist (disbursements only) also
rebuild the links and the network: stages 3-5 run in a subfolder of the output
folder that points to the stage 2 files, so the default outputs are not touched.

Run after the pipeline (from the project folder):
  .venv/bin/python report/sensitivity.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

# label, settings changed, rebuild links and network (stages 3-4)?
ALTERNATIVES = [
    ("None (default)", {}, False),
    ("Expenditure counted in 3, 4, x1", {"INDICATOR3_INCLUDE_EXPENDITURE": True}, False),
    (
        "Related-activity links, other publishers",
        {"INDICATOR1_RELATED_ACTIVITIES": "other_publishers"},
        False,
    ),
    ("Related-activity links, all", {"INDICATOR1_RELATED_ACTIVITIES": "all"}, False),
    ("Year by activity period", {"YEAR_BY_TRANSACTION_DATE": not config.YEAR_BY_TRANSACTION_DATE}, False),
    ("Disbursements only (network rebuilt)", {"DISBURSEMENTS_ONLY": True}, True),
]
SHOW = ["1", "2", "3", "4", "5", "x1", "x1b", "6"]

# Stage 2 files that stages 3-5 read (linked into the rebuild folder)
STAGE2_FILES = [
    "transactions_clean.parquet",
    "activities_clean.parquet",
    "organisations.parquet",
    "publishers.parquet",
    "related_activities_clean.parquet",
    "duplicates_removed.csv",
]
REBUILD_DIR = "sensitivity_disbursements_only"


def load(filename: str):
    spec = importlib.util.spec_from_file_location("s" + filename[:2], ROOT / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rebuild_dir(out: Path) -> Path:
    """A subfolder of out whose stage 2 files are links to those in out."""
    sub = out / REBUILD_DIR
    sub.mkdir(exist_ok=True)
    for f in STAGE2_FILES:
        link = sub / f
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(out / f)
    return sub


def main() -> None:
    s3, s4, s5 = (load(f) for f in ("03_links.py", "04_network.py", "05_indicators.py"))
    out = config.output_dir()
    rows = []
    try:
        for label, change, rebuild in ALTERNATIVES:
            old = {k: getattr(config, k) for k in change}
            for k, v in change.items():
                setattr(config, k, v)
            try:
                where = rebuild_dir(out) if rebuild else out
                if rebuild:
                    s3.run(where)
                    s4.run(where)
                s5.run(where)
                ind = pd.read_csv(where / "indicators.csv", dtype={"indicator": str})
                a = ind[(ind["group"] == "All publishers") & ind["indicator"].isin(SHOW)]
                a = a.assign(setting=label, result=a["value"].fillna(a["percent"]))
                rows.append(a[["setting", "scope", "indicator", "unit", "result"]])
            finally:
                for k, v in old.items():  # settings back to those in config.py
                    setattr(config, k, v)
    finally:
        # Restore the default outputs, also when an alternative failed. Otherwise
        # indicators.csv would keep the last alternative's results.
        s5.run(out)
    pd.concat(rows).to_csv(out / "sensitivity.csv", index=False)
    print(f"Written to {out / 'sensitivity.csv'}")


if __name__ == "__main__":
    main()
