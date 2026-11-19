"""Helpers shared by the stages: logging, identifier rules, small file utilities.

The identifier rules (norm, clean_id, well_formed, load_codelists) are copied from
the Uganda 2025 pilot (an earlier script, not in this repository) unchanged in meaning. Each has a scalar
version (one value, as in the pilot) and a column version (a whole pandas column
at once, which is what the stages use on millions of rows).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pandas as pd

import config

PACKAGES = ["pandas", "pyarrow", "lxml", "networkx", "numpy", "shapely", "pytest", "ruff"]


# =============================================================================
# Logging and summaries
# =============================================================================
def setup_logging(out_dir: Path, stage: str) -> logging.Logger:
    """Log to the screen and to <out_dir>/logs/<stage>.log (overwritten each run)."""
    log_dir = out_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ch6")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.FileHandler(log_dir / f"{stage}.log", mode="w"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        logger.addHandler(h)
    return logger


def git_version() -> str | None:
    """Current git commit of the code, with "+changes" if files are modified
    (None if git or the repository is not available)."""
    here = Path(__file__).resolve().parent

    def git(*args: str) -> str:
        done = subprocess.run(["git", *args], cwd=here, capture_output=True, text=True, check=True)
        return done.stdout.strip()

    try:
        sha = git("rev-parse", "--short", "HEAD")
        modified = git("status", "--porcelain", "--untracked-files=no")
        return sha + ("+changes" if modified else "")
    except subprocess.CalledProcessError, FileNotFoundError:
        return None


def write_summary(out_dir: Path, stage: str, summary: dict) -> None:
    """Write the stage's counts to <out_dir>/summaries/<stage>.json (read by run_all.py),
    with the time and the code version, so that a stage run on its own is traceable."""
    d = out_dir / "summaries"
    d.mkdir(parents=True, exist_ok=True)
    summary = {
        **summary,
        "finished_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_version": git_version(),
    }
    (d / f"{stage}.json").write_text(json.dumps(summary, indent=1, default=str))


def read_summary(out_dir: Path, stage: str) -> dict:
    """Read a summary written by write_summary()."""
    return json.loads((out_dir / "summaries" / f"{stage}.json").read_text())


def package_versions() -> dict[str, str]:
    """Installed versions of the packages the pipeline uses."""
    out = {"python": sys.version.split()[0]}
    for p in PACKAGES:
        try:
            out[p] = version(p)
        except PackageNotFoundError:
            out[p] = "not installed"
    return out


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    """pd.read_csv() where only empty cells are missing. By default pandas, like R's
    read_csv(), also reads the text "NA" as missing, and "NA" is Namibia's country
    code. The pipeline writes missing values as empty cells, so nothing is lost."""
    return pd.read_csv(path, keep_default_na=False, na_values=[""], **kwargs)


def require_file(path: Path, made_by: str) -> Path:
    """Stop with a clear message if an input file from an earlier stage is missing."""
    if not path.exists():
        raise SystemExit(f"{path} not found. Run {made_by} first.")
    return path


# =============================================================================
# Identifiers (from the pilot)
# =============================================================================
def norm(x: object) -> str | None:
    """Upper-case and trim an organisation identifier; treat blanks as missing."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    x = str(x).strip().upper()
    return x or None


def clean_id(x: object) -> str | None:
    """Trim an activity identifier; missing values (None, NaN, blank) become None.
    Activity IDs keep their case, because publishers' IDs are case-sensitive."""
    if not isinstance(x, str) or not x.strip():
        return None
    return x.strip()


def norm_col(s: pd.Series) -> pd.Series:
    """Column version of norm(): trim, upper-case, blanks to missing."""
    s = s.astype("string").str.strip().str.upper()
    return s.mask(s == "")


def clean_id_col(s: pd.Series) -> pd.Series:
    """Column version of clean_id(): trim, keep case, blanks to missing."""
    s = s.astype("string").str.strip()
    return s.mask(s == "")


def load_codelists() -> tuple[list[str], set[str]]:
    """Registration-agency prefixes (longest first) and legacy identifiers, upper case."""
    ora = read_csv(config.ORG_REGISTRATION_AGENCIES)
    legacy = read_csv(config.LEGACY_ORG_IDENTIFIERS)
    prefixes = sorted(ora["prefix"].str.upper(), key=len, reverse=True)  # longest first
    return prefixes, set(legacy["identifier"].str.upper())


def well_formed(ref: object, prefixes: list[str], legacy: set[str]) -> bool:
    """True if the identifier is a known legacy code, or starts with a valid
    registration-agency prefix followed by '-' and something more."""
    if not isinstance(ref, str):  # missing values arrive as None or NaN
        return False
    if ref in legacy:
        return True
    return any(ref.startswith(p + "-") and len(ref) > len(p) + 1 for p in prefixes)


def well_formed_col(s: pd.Series, prefixes: list[str], legacy: set[str]) -> pd.Series:
    """Column version of well_formed(): same rule, as one regular expression.
    Expects identifiers already normalised with norm_col()."""
    pattern = "^(?:" + "|".join(re.escape(p) for p in prefixes) + r")-."
    s = s.astype("string")
    ok = s.str.match(pattern, na=False) | s.isin(legacy)
    return ok.fillna(False).astype(bool)


# =============================================================================
# Countries (one rule for stages 5 and 6; docs/methods_decisions.md, section 7)
# =============================================================================
def valid_countries(s: pd.Series) -> pd.Series:
    """Keep only the codes that are countries (config.COUNTRY_CODE_PATTERN) in a
    ";"-separated list, without repeats. Missing where no country is left.
    Example: "UG;GLOBAL;UG" -> "UG"; "998" -> missing."""
    long = s.dropna().str.split(";").explode().str.strip().rename_axis("idx").rename("c").reset_index()
    long = long[long["c"].str.fullmatch(config.COUNTRY_CODE_PATTERN, na=False)].drop_duplicates()
    return long.groupby("idx")["c"].agg(";".join).reindex(s.index)


def transaction_countries(copy_id: pd.Series, own: pd.Series, activity_level: pd.Series) -> pd.Series:
    """Countries of each transaction: its activity's activity-level countries; only if
    the activity names none, the transaction's own countries.

    copy_id         the transaction's activity copy
    own             the transaction's own countries, already passed through valid_countries()
    activity_level  activity-level countries indexed by copy_id, already passed through
                    valid_countries() (missing where the activity names none)

    Example: an activity naming UG with a transaction naming KE -> "UG", not "KE".
    An activity naming only "GLOBAL", with a transaction naming KE -> "KE"."""
    return copy_id.map(activity_level).fillna(own)


def is_partner_code(refs: pd.Series) -> pd.Series:
    """True for a publisher's own code for a partner (config.PARTNER_CODE_PATTERN, e.g.
    UNICEF's XM-DAC-41122-VN-...), which the organisation network leaves out."""
    return (
        refs.astype("string").str.contains(config.PARTNER_CODE_PATTERN, regex=True).fillna(False).astype(bool)
    )


def org_id_as_activity_id(ids: pd.Series, published: pd.Series, org_refs: set[str]) -> pd.Series:
    """True where an activity-ID field holds an organisation identifier instead of an
    activity ID: the value is not a published activity, and after norm() it equals
    a known organisation ref (e.g. USAID's receiver-activity-id="US-GOV-1")."""
    hit = ids.notna() & ~published.fillna(False).astype(bool) & norm_col(ids).isin(org_refs)
    return hit.fillna(False).astype(bool)
