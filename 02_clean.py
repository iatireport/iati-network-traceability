"""Stage 2: one copy per activity, then normalise and validate identifiers.

Input   <output dir>/activities.parquet, transactions.parquet,
        related_activities.parquet, publishers.parquet   (stage 1)
Output  <output dir>/
          activities_clean.parquet          one row per activity (iati_identifier unique)
          transactions_clean.parquet        all transactions of the kept copies, with checks
          related_activities_clean.parquet  related-activity entries of the kept copies
          organisations.parquet             one row per organisation identifier seen
          duplicates_removed.csv            one row per removed activity copy, with reason

Rules (each a named function below):
  pick_one_copy_per_activity()  primary reporter first, then latest
                                last-updated-datetime, then the copy in the
                                reporting organisation's own folder, then the copy
                                with more transactions, then file order
  normalise_identifiers()       organisation refs trimmed and upper-cased; activity
                                IDs trimmed (case kept); blanks become missing
  classify_transactions()       direction, counterpart, internal flows, validity,
                                exact duplicates within an activity

No transaction of a kept copy is dropped: exact duplicates are flagged, not removed.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

import common
import config

STAGE = "02_clean"

# Columns that describe a transaction's content (used to find exact duplicates).
CONTENT_COLS = [
    "transaction_ref",
    "transaction_type_raw",
    "transaction_date",
    "value",
    "currency",
    "value_date",
    "flow_type",
    "finance_type",
    "aid_type",
    "tied_status",
    "provider_org_ref",
    "provider_org_name",
    "provider_activity_id",
    "receiver_org_ref",
    "receiver_org_name",
    "receiver_activity_id",
    "tx_recipient_countries",
]


# =============================================================================
# Deduplication
# =============================================================================
def parse_last_updated(s: pd.Series) -> pd.Series:
    """last-updated-datetime as a UTC timestamp. Spaces are removed first
    ('2026 - 10-27T00:00:00' occurs). Times without a zone are read as UTC.
    Unreadable or missing values become NaT (treated as the oldest)."""
    s = s.astype("string").str.replace(" ", "", regex=False)
    return pd.to_datetime(s, format="ISO8601", utc=True, errors="coerce")


def in_own_folder(act: pd.DataFrame, publishers: pd.DataFrame) -> pd.Series:
    """True if the copy sits in the folder of the registry publisher whose
    identifier equals the activity's reporting-org ref."""
    ids = pd.concat(
        [
            publishers[["publisher_folder", "organisation_identifier"]].rename(
                columns={"organisation_identifier": "org"}
            ),
            publishers[["publisher_folder", "iati_identifier"]].rename(columns={"iati_identifier": "org"}),
        ]
    )
    ids["org"] = common.norm_col(ids["org"])
    ids = ids.dropna().drop_duplicates()
    key = act[["publisher_folder"]].assign(org=common.norm_col(act["reporting_org_ref"]))
    hit = key.merge(ids.assign(match=True), on=["publisher_folder", "org"], how="left")["match"]
    return hit.fillna(False).astype(bool).to_numpy()


def pick_one_copy_per_activity(act: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep one copy per iati_identifier. Expects columns secondary_reporter,
    last_updated (timestamp), own_folder, n_transactions, copy_id.

    Order of preference:
      1. primary reporter (secondary-reporter not 1/true)
      2. latest last-updated-datetime (missing = oldest)
      3. copy in the reporting organisation's own publisher folder
      4. more transactions
      5. file order (lowest copy_id), so the result is reproducible

    Returns (kept, removed). `removed` has one row per dropped copy with the
    first rule that decided against it (`reason`) and the kept copy's file."""
    oldest = pd.Timestamp("1900-01-01", tz="UTC")
    ranked = act.assign(_lu=act["last_updated"].fillna(oldest)).sort_values(
        ["iati_identifier", "secondary_reporter", "_lu", "own_folder", "n_transactions", "copy_id"],
        ascending=[True, True, False, False, False, True],
    )
    first = ~ranked["iati_identifier"].duplicated()
    kept = ranked[first].drop(columns="_lu")
    removed = ranked[~first]

    k = kept.set_index("iati_identifier")
    w = removed.join(
        k[
            [
                "copy_id",
                "publisher_folder",
                "file",
                "secondary_reporter",
                "last_updated",
                "own_folder",
                "n_transactions",
            ]
        ],
        on="iati_identifier",
        suffixes=("", "_kept"),
    )
    lu, lu_k = w["last_updated"].fillna(oldest), w["last_updated_kept"].fillna(oldest)
    w["reason"] = np.select(
        [
            w["secondary_reporter"] & ~w["secondary_reporter_kept"],
            lu < lu_k,
            ~w["own_folder"] & w["own_folder_kept"],
            w["n_transactions"] < w["n_transactions_kept"],
        ],
        [
            "secondary reporter",
            "older last-updated-datetime",
            "not in reporting organisation's own folder",
            "fewer transactions",
        ],
        default="same on all rules (file order)",
    )
    w["same_folder_as_kept"] = w["publisher_folder"] == w["publisher_folder_kept"]
    cols = [
        "iati_identifier",
        "reason",
        "copy_id",
        "publisher_folder",
        "file",
        "n_transactions",
        "last_updated",
        "copy_id_kept",
        "publisher_folder_kept",
        "file_kept",
        "n_transactions_kept",
        "last_updated_kept",
        "same_folder_as_kept",
    ]
    return kept.drop(columns="last_updated"), w[cols]


# =============================================================================
# Identifiers and transaction checks
# =============================================================================
def normalise_identifiers(tx: pd.DataFrame) -> pd.DataFrame:
    """Organisation refs: norm() (trim, upper case). Activity IDs: clean_id()
    (trim, case kept). Blanks become missing. The published value of each
    organisation ref is kept in <col>_raw only where normalising changed it."""
    for c in ["reporting_org_ref", "provider_org_ref", "receiver_org_ref"]:
        raw = tx[c]
        tx[c] = common.norm_col(raw)
        tx[f"{c}_changed"] = (raw.notna() & (raw != tx[c])).fillna(False).astype(bool)
    for c in ["iati_identifier", "provider_activity_id", "receiver_activity_id"]:
        tx[c] = common.clean_id_col(tx[c])
    return tx


def classify_transactions(
    tx: pd.DataFrame, prefixes: list[str], legacy: set[str], published: pd.Index
) -> pd.DataFrame:
    """Add direction, counterpart and validity columns (all column-wise).

    direction            "in" (types 1, 9, 11, 13), "out" (2, 3, 4, 5, 6, 7, 8, 10, 12), else "other"
    counterpart_*        provider for "in", receiver for "out", missing for "other"
    internal             counterpart ref equals the reporting org ref
    valid_*_ref          the ref passes well_formed() (registration agency or legacy list)
    counterpart_activity_published  counterpart activity ID is a published iati-identifier
    self_reference       counterpart activity ID is the transaction's own activity
    exact_duplicate      same content as an earlier transaction of the same activity
    """
    t = tx["transaction_type"]
    tx["direction"] = np.select(
        [t.isin(config.INCOMING), t.isin(config.OUTGOING)], ["in", "out"], default="other"
    )
    is_in, is_out = tx["direction"] == "in", tx["direction"] == "out"
    for part in ["org_ref", "org_type", "org_name"]:
        tx[f"counterpart_{part}"] = tx[f"provider_{part}"].where(is_in, tx[f"receiver_{part}"].where(is_out))
    tx["counterpart_activity_id"] = tx["provider_activity_id"].where(
        is_in, tx["receiver_activity_id"].where(is_out)
    )
    tx = tx.rename(columns={"counterpart_org_ref": "counterpart_ref"})

    for c in ["reporting_org_ref", "provider_org_ref", "receiver_org_ref", "counterpart_ref"]:
        name = "valid_" + c.removesuffix("_org_ref").removesuffix("_ref") + "_ref"
        tx[name] = common.well_formed_col(tx[c], prefixes, legacy)

    tx["internal"] = (tx["counterpart_ref"] == tx["reporting_org_ref"]).fillna(False).astype(bool)
    tx["counterpart_activity_published"] = tx["counterpart_activity_id"].isin(published)
    tx["self_reference"] = (tx["counterpart_activity_id"] == tx["iati_identifier"]).fillna(False).astype(bool)

    h = pd.util.hash_pandas_object(tx[CONTENT_COLS], index=False)
    tx["exact_duplicate"] = pd.DataFrame({"c": tx["copy_id"], "h": h}).duplicated()
    return tx


def publisher_groups(act: pd.DataFrame, publishers: pd.DataFrame) -> pd.Series:
    """Publisher group of each activity. From config.PUBLISHER_GROUPS if that file
    exists (org_ref -> publisher_group); otherwise the IATI organisation type label
    of the reporting org, falling back to the registry's type for the folder."""
    reg_type = act["publisher_folder"].map(publishers.set_index("publisher_folder")["organisation_type"])
    otype = act["reporting_org_type"].where(act["reporting_org_type"].isin(config.ORG_TYPE), reg_type)
    group = otype.map(config.ORG_TYPE).fillna("Unknown")
    if config.PUBLISHER_GROUPS.exists():
        g = common.read_csv(config.PUBLISHER_GROUPS, dtype=str)
        g["org_ref"] = common.norm_col(g["org_ref"])
        override = act["reporting_org_ref"].map(g.set_index("org_ref")["publisher_group"])
        group = override.fillna(group)
    return group


def build_organisations(
    act: pd.DataFrame, tx: pd.DataFrame, publishers: pd.DataFrame, prefixes: list[str], legacy: set[str]
) -> pd.DataFrame:
    """One row per organisation identifier seen as reporting org, provider or
    receiver. The name and type are the ones the organisation uses for itself
    when it publishes (most common), otherwise the most common one others use."""
    parts = [
        act[["reporting_org_ref", "reporting_org_name", "reporting_org_type"]]
        .set_axis(["org_ref", "name", "type"], axis=1)
        .assign(own=True)
    ]
    for side in ["provider", "receiver"]:
        parts.append(
            tx[[f"{side}_org_ref", f"{side}_org_name", f"{side}_org_type"]]
            .set_axis(["org_ref", "name", "type"], axis=1)
            .dropna(subset=["org_ref"])
            .assign(own=False)
        )
    allr = pd.concat(parts, ignore_index=True).dropna(subset=["org_ref"])

    def most_common(col: str) -> pd.Series:
        x = allr.dropna(subset=[col]).groupby(["org_ref", "own", col]).size().rename("n").reset_index()
        x = x.sort_values(["org_ref", "own", "n"], ascending=[True, False, False])
        return x.drop_duplicates("org_ref").set_index("org_ref")[col]

    org = pd.DataFrame(index=pd.Index(allr["org_ref"].unique(), name="org_ref"))
    org["org_name"] = most_common("name")
    org["org_type"] = most_common("type")
    org["org_type_label"] = org["org_type"].map(config.ORG_TYPE)
    org["n_activities_published"] = act.groupby("reporting_org_ref").size()
    org["n_activities_published"] = org["n_activities_published"].fillna(0).astype(int)
    reg_ids = set(common.norm_col(publishers["organisation_identifier"]).dropna()) | set(
        common.norm_col(publishers["iati_identifier"]).dropna()
    )
    org["is_publisher"] = (org["n_activities_published"] > 0) | org.index.isin(reg_ids)
    org["in_registry"] = org.index.isin(reg_ids)
    org = org.reset_index()
    org["well_formed"] = common.well_formed_col(org["org_ref"], prefixes, legacy)
    org["partner_code"] = common.is_partner_code(org["org_ref"])  # left out of the org network
    return org


# =============================================================================
# Stage
# =============================================================================
def run(out_dir: Path) -> dict:
    """Run stage 2 on the stage 1 outputs in out_dir. Returns the stage summary."""
    log = common.setup_logging(out_dir, STAGE)
    t0 = time.time()
    s = {}

    act = pd.read_parquet(common.require_file(out_dir / "activities.parquet", "01_parse_bulk.py"))
    publishers = pd.read_parquet(out_dir / "publishers.parquet")
    s["activity_copies_in"] = len(act)

    # 1. Activities without an identifier cannot be linked or deduplicated.
    no_id = act["iati_identifier"].isna()
    s["activity_copies_without_identifier"] = int(no_id.sum())
    s["transactions_in_copies_without_identifier"] = int(act.loc[no_id, "n_transactions"].sum())
    act = act[~no_id].copy()

    # 2. One copy per activity.
    act["reporting_org_ref"] = common.norm_col(act["reporting_org_ref"])
    act["last_updated"] = parse_last_updated(act["last_updated_datetime"])
    act["own_folder"] = in_own_folder(act, publishers)
    kept, removed = pick_one_copy_per_activity(act)
    s["distinct_activity_ids"] = len(kept)
    s["activity_ids_with_more_than_one_copy"] = int(removed["iati_identifier"].nunique())
    s["activity_copies_removed"] = len(removed)
    s["transactions_in_removed_copies"] = int(removed["n_transactions"].sum())
    s["removed_by_reason"] = {
        r: {"copies": int(len(g)), "transactions": int(g["n_transactions"].sum())}
        for r, g in removed.groupby("reason")
    }
    s["removed_copies_in_same_folder_as_kept"] = int(removed["same_folder_as_kept"].sum())
    removed.to_csv(out_dir / "duplicates_removed.csv", index=False)
    log.info(
        "%d activity copies -> %d activities (%d copies, %d transactions removed as duplicates)",
        s["activity_copies_in"],
        len(kept),
        len(removed),
        s["transactions_in_removed_copies"],
    )

    kept = kept.copy()
    kept["publisher_group"] = publisher_groups(kept, publishers)
    reg = publishers.drop_duplicates("publisher_folder").set_index("publisher_folder")
    kept["registry_org_ref"] = common.norm_col(kept["publisher_folder"].map(reg["organisation_identifier"]))
    kept["reporting_ref_matches_registry"] = (kept["registry_org_ref"] == kept["reporting_org_ref"]).fillna(
        False
    )
    prefixes, legacy = common.load_codelists()
    kept["valid_reporting_ref"] = common.well_formed_col(kept["reporting_org_ref"], prefixes, legacy)
    assert kept["iati_identifier"].is_unique
    kept.to_parquet(out_dir / "activities_clean.parquet", index=False)

    # 3. Transactions of the kept copies.
    tx = pd.read_parquet(out_dir / "transactions.parquet")
    s["transactions_in"] = len(tx)
    tx = tx[tx["copy_id"].isin(kept["copy_id"])].reset_index(drop=True)
    s["transactions_kept"] = len(tx)
    assert s["transactions_kept"] == int(kept["n_transactions"].sum())
    log.info("%d transactions -> %d", s["transactions_in"], len(tx))

    tx = normalise_identifiers(tx)
    tx = classify_transactions(tx, prefixes, legacy, pd.Index(kept["iati_identifier"]))
    tx["publisher_group"] = tx["copy_id"].map(kept.set_index("copy_id")["publisher_group"])
    assert tx["iati_identifier"].notna().all()
    assert tx["publisher_group"].notna().all()

    s["transactions_by_direction"] = tx["direction"].value_counts().to_dict()
    s["transactions_by_type_recode"] = tx["type_recode"].value_counts().to_dict()
    s["exact_duplicate_transactions"] = int(tx["exact_duplicate"].sum())
    s["org_refs_changed_by_normalising"] = {
        c: int(tx[f"{c}_changed"].sum())
        for c in ["reporting_org_ref", "provider_org_ref", "receiver_org_ref"]
    }
    yr = tx["transaction_year"]
    s["transactions_dated_before_1960_or_after_2040"] = int(((yr < 1960) | (yr > 2040)).sum())
    s["transactions_without_valid_date"] = int((~tx["date_valid"]).sum())
    has_cp = tx["direction"] != "other"
    s["counterpart"] = {
        "transactions_in_or_out": int(has_cp.sum()),
        "ref_given": int(tx.loc[has_cp, "counterpart_ref"].notna().sum()),
        "ref_well_formed": int(tx.loc[has_cp, "valid_counterpart_ref"].sum()),
        "name_only": int((has_cp & tx["counterpart_ref"].isna() & tx["counterpart_org_name"].notna()).sum()),
        "activity_id_given": int(tx.loc[has_cp, "counterpart_activity_id"].notna().sum()),
        "activity_id_published": int(tx.loc[has_cp, "counterpart_activity_published"].sum()),
        "self_reference": int(tx["self_reference"].sum()),
        "internal": int(tx["internal"].sum()),
    }
    tx.to_parquet(out_dir / "transactions_clean.parquet", index=False)

    # 4. Related activities of the kept copies.
    rel = pd.read_parquet(out_dir / "related_activities.parquet")
    rel = rel[rel["copy_id"].isin(kept["copy_id"])].copy()
    rel["related_activity_ref"] = common.clean_id_col(rel["related_activity_ref"])
    rel = rel.dropna(subset=["related_activity_ref"])
    rel = rel[rel["related_activity_ref"] != rel["iati_identifier"]]
    rel["related_published"] = rel["related_activity_ref"].isin(kept["iati_identifier"])
    pub = kept.set_index("iati_identifier")["reporting_org_ref"]
    rel["related_publisher"] = rel["related_activity_ref"].map(pub)
    rel["same_publisher"] = (
        rel["related_publisher"] == rel["copy_id"].map(kept.set_index("copy_id")["reporting_org_ref"])
    ).fillna(False)
    rel = rel.drop_duplicates(["iati_identifier", "related_activity_ref", "related_activity_type"])
    rel.to_parquet(out_dir / "related_activities_clean.parquet", index=False)
    s["related_activity_links"] = len(rel)
    s["related_activity_links_published"] = int(rel["related_published"].sum())

    # 5. Organisations.
    org = build_organisations(kept, tx, publishers, prefixes, legacy)
    assert org["org_ref"].is_unique
    org.to_parquet(out_dir / "organisations.parquet", index=False)
    s["organisations"] = len(org)
    s["organisations_publishing"] = int((org["n_activities_published"] > 0).sum())

    s["seconds"] = round(time.time() - t0)
    common.write_summary(out_dir, STAGE, s)
    log.info("Done (%.0fs)", time.time() - t0)
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", type=Path, default=None)
    a = ap.parse_args()
    run(a.out_dir or config.output_dir())


if __name__ == "__main__":
    main()
