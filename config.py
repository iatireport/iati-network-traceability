"""All paths and methodological rules for the traceability pipeline.

Every stage imports this file. Change a setting here, not in the stage scripts.
Paths can also be overridden with environment variables (useful for tests and for
a new bulk download):

    CH6_BULK_DIR    folder of the Bulk Data Service copy (holds datasets-minimal.json)
    CH6_OUTPUT_DIR  folder where the outputs of this run are written
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent

# =============================================================================
# 1. Paths
# =============================================================================
BULK_DIR = Path(os.environ.get("CH6_BULK_DIR", HERE / "iati-data"))
REFERENCE_DIR = HERE / "reference"
ORG_REGISTRATION_AGENCIES = REFERENCE_DIR / "org_registration_agencies.csv"
LEGACY_ORG_IDENTIFIERS = REFERENCE_DIR / "legacy_org_identifiers.csv"

# Optional table mapping publisher identifiers to the report's publisher groups
# (columns: org_ref, publisher_group). If the file is absent, the IATI
# organisation type of the reporting organisation is used as the group.
PUBLISHER_GROUPS = REFERENCE_DIR / "publisher_groups.csv"


def bulk_index_date(bulk_dir: Path = BULK_DIR) -> str:
    """Date (YYYY-MM-DD) on which the bulk copy's index was created."""
    index = bulk_dir / "datasets-minimal.json"
    if not index.exists():
        raise SystemExit(
            f"No Bulk Data Service copy found at {bulk_dir} (no datasets-minimal.json). "
            "Download one (see 'Data source' in README.md) or set CH6_BULK_DIR to its folder."
        )
    with open(index) as fh:
        return json.load(fh)["index_created"][:10]


def output_dir(bulk_dir: Path = BULK_DIR) -> Path:
    """Output folder for this bulk copy: outputs/bulk_<index date>/ unless
    CH6_OUTPUT_DIR is set. Outputs of different downloads never overwrite each other."""
    if os.environ.get("CH6_OUTPUT_DIR"):
        return Path(os.environ["CH6_OUTPUT_DIR"])
    return HERE / "outputs" / f"bulk_{bulk_index_date(bulk_dir)}"


# =============================================================================
# 2. Parsing (stage 1)
# =============================================================================
N_WORKERS = max(1, (os.cpu_count() or 2) - 2)  # parallel processes for the XML parse

# Files that cannot be parsed completely are excluded. The activities read before
# the error are counted (so totals match a direct count of the bulk copy) but not kept.
KEEP_PARTIAL_FILES = False

# IATI version 1 text codes for transaction types, recoded to version 2 numbers.
V1_TRANSACTION_TYPES = {
    "IF": "1",  # incoming funds
    "C": "2",  # commitment
    "D": "3",  # disbursement
    "E": "4",  # expenditure
    "IR": "5",  # interest repayment
    "LR": "6",  # loan repayment
    "R": "7",  # reimbursement
    "QP": "8",  # purchase of equity
    "QS": "9",  # sale of equity
    "CG": "10",  # credit guarantee
}

# Transaction types written as names instead of codes (seen in the bulk copy).
NAMED_TRANSACTION_TYPES = {
    "INCOMING FUNDS": "1",
    "COMMITMENT": "2",
    "OUTGOING COMMITMENT": "2",
    "DISBURSEMENT": "3",
    "EXPENDITURE": "4",
    "INTEREST PAYMENT": "5",
    "INTEREST REPAYMENT": "5",
    "LOAN REPAYMENT": "6",
    "REIMBURSEMENT": "7",
    "PURCHASE OF EQUITY": "8",
    "SALE OF EQUITY": "9",
    "CREDIT GUARANTEE": "10",
    "INCOMING COMMITMENT": "11",
    "OUTGOING PLEDGE": "12",
    "INCOMING PLEDGE": "13",
}

VALID_TRANSACTION_TYPES = {str(i) for i in range(1, 14)}

# Values of secondary-reporter that mean "this publisher is not the primary reporter".
SECONDARY_REPORTER_TRUE = {"1", "true"}

# =============================================================================
# 3. Transaction groups (IATI TransactionType codelist)
# =============================================================================
# Sale of equity (9) is incoming, as in the IATI guidance: the buyer is the provider.
# Added after the code review (November 2026); before, type 9 had no direction.
INCOMING = {"1", "9", "11", "13"}  # incoming funds / sale of equity / commitment / pledge
OUTGOING = {"2", "3", "4", "5", "6", "7", "8", "10", "12"}
FLOW_IN = {"1", "11"}  # incoming funds, incoming commitment
FLOW_OUT = {"2", "3"}  # outgoing commitment, disbursement
EXPENDITURE = {"4"}

# Which transactions can create an activity-to-activity link (stage 3).
# Provider side: the funder names the receiver's activity.
# Receiver side: the receiver names the funder's activity.
LINK_PROVIDER_SIDE_TYPES = FLOW_OUT | EXPENDITURE  # expenditure links are kept and labelled
LINK_RECEIVER_SIDE_TYPES = FLOW_IN

# Disbursements only (sensitivity check; the McCarthy paper uses disbursements only):
# outgoing flows are disbursements (type 3) and incoming flows are incoming funds
# (type 1), in the links (stage 3), the network (stage 4) and the indicators
# (stage 5). Commitments and expenditure links are left out. Default: False
# (commitments and disbursements pooled).
DISBURSEMENTS_ONLY = False


# The stages ask these functions, not the sets above, so that DISBURSEMENTS_ONLY
# changes every stage at once.
def flow_in_types() -> set[str]:
    return {"1"} if DISBURSEMENTS_ONLY else FLOW_IN


def flow_out_types() -> set[str]:
    return {"3"} if DISBURSEMENTS_ONLY else FLOW_OUT


def link_provider_types() -> set[str]:
    return {"3"} if DISBURSEMENTS_ONLY else LINK_PROVIDER_SIDE_TYPES


def link_receiver_types() -> set[str]:
    return {"1"} if DISBURSEMENTS_ONLY else LINK_RECEIVER_SIDE_TYPES


# =============================================================================
# 4. Network rules (stages 3 and 4)
# =============================================================================
# Links between two activities of the same publishing organisation (for example a
# programme and its own component) are kept in the edge table but excluded from
# the network, as in the pilot.
INCLUDE_SAME_ORG_LINKS_IN_NETWORK = False

# Expenditure-only links (type 4) are kept in the edge table, labelled, and used
# in the network. Set to False to build the network from commitments and
# disbursements only.
INCLUDE_EXPENDITURE_LINKS_IN_NETWORK = True

# When is an activity link implausible? (stage 3, is_plausible_link())
#   "known_publishers"  the named org ref belongs to a different publisher, or the
#                       link reverses the publisher's own funding (GALVmed pattern).
#                       Refs that are no publisher's identifier (names, badly written
#                       codes such as "FCDO" or "GB-GOV-01") cannot be checked and
#                       are not counted against the link.
#   "strict"            as above, and also any named ref that does not match the
#                       linked activity's publisher.
PLAUSIBILITY_RULE = "known_publishers"

# Chain enumeration stops after this many chains (a safeguard: the number of
# paths in a network can grow very fast). The total number of chains and the depth
# per origin (indicator 6, chain_origins.csv longest_steps) are always exact. The
# measures counted over the listed chains are not, if the limit is reached: depth per
# chain and per organisation path, indicators 6c to 6e, and n_chains per origin.
# Stage 4 logs a warning when that happens (12,751 chains in the copy of 30 October 2026).
MAX_CHAINS = 5_000_000

# Partner codes: identifiers that a publisher gives its own partners, not organisation
# identifiers in the usual sense. UNICEF writes them as XM-DAC-41122-VN-<code>
# (11,240 in the copy of 30 October 2026; many named "IP not published"). They are
# well formed and kept in org_edges, but left out of the organisation network and its
# counts (stage 4, org_nodes, network_summary.json), as in McCarthy et al., who merge
# them into UNICEF. They publish no activities, so links, chains and indicators do not
# change. Decided November 2026 (code review, M9). A regular expression.
PARTNER_CODE_PATTERN = r"^XM-DAC-41122-VN-"

# =============================================================================
# 5. Indicator settings (stage 5): the open questions for the IATI Secretariat
# =============================================================================
# Indicator 3: count expenditure (type 4) as an outgoing flow? The same setting
# applies to indicators 4 and x1, so all three use one definition.
INDICATOR3_INCLUDE_EXPENDITURE = False

# Indicator 1: count related-activity links as connections?
#   "none"             transaction links only (pilot definition)
#   "other_publishers" add related-activity links to another publisher's activity
#   "all"              add all related-activity links
INDICATOR1_RELATED_ACTIVITIES = "none"

# How a subset year is defined.
#   True:  by transaction date. A transaction belongs to year Y if it is dated in Y;
#          an activity belongs to Y if it has a transaction dated in Y (pilot rule).
#   False: by activity period. An activity belongs to Y if its start-end period
#          overlaps Y; all its transactions are counted, whatever their date.
YEAR_BY_TRANSACTION_DATE = True

# Subsets for reporting, computed on the global network: (recipient country, year).
# None means "all" for that part, e.g. (None, 2025) is all countries in 2025.
# The global scope (all countries, all years) is always computed.
SUBSETS = [(None, 2023), (None, 2024), (None, 2025), ("UG", 2025)]

# Indicators with a denominator below this are flagged small_base = TRUE
# (percentages on small bases should not be reported).
SMALL_BASE = 30

# =============================================================================
# 6. Spatial (stage 6, 06_spatial.py)
# =============================================================================
# Years for the country maps. None = all years (the global scope). A year is
# assigned by transaction date, as in stage 5's default.
SPATIAL_YEARS = [None, 2025]

# How an activity or transaction that names several recipient countries is counted.
# All three are computed; MULTI_COUNTRY_RULE is the one drawn on the maps.
#   "every"   counted in full in every listed country (as stage 5's country subsets)
#   "single"  only activities/transactions naming exactly one country
#   "split"   split by the published percentages (equal shares if they are missing
#             or unusable); numerators and denominators can then be fractions
MULTI_COUNTRY_RULES = ("every", "single", "split")
MULTI_COUNTRY_RULE = "every"

# A recipient-country code is placed on the map only if it is two capital letters.
# Others ("GLOBAL", region numbers such as "998", three-letter codes such as "HTI")
# are left out of the country lists and reported in spatial_unplaced.csv.
COUNTRY_CODE_PATTERN = r"[A-Z]{2}"

# Part D: activity locations (<location> elements) and example chains that reach
# geocoded work. The locations are read from the bulk copy once and kept in
# locations.parquet; they are read again only if stage 2 has been rerun since.
# A point is valid if latitude is within -90..90, longitude within -180..180, and
# it is not 0,0 (a common placeholder).
#
# Example chains: chains of at least SHOWCASE_MIN_STEPS steps whose end activity has
# a usable point (see below). They are ranked: end publisher a local organisation
# type first, then more steps confirmed by both sides, then more steps. To show
# different routes, one chain per organisation path, per end publisher, and per
# route before the end publisher (funder and intermediaries). Chains that end at an
# organisation they already passed through are not ranked. The first SHOWCASE_N
# are drawn.
SHOWCASE_MIN_STEPS = 2
SHOWCASE_N = 5
# One counter-example: the best-ranked chain whose end activity has no usable point.
COUNTEREXAMPLE_N = 1

# A point is "usable" for the examples if it is valid, lies inside one of its
# activity's recipient countries (Natural Earth 1:50m borders widened by
# POINT_COUNTRY_TOLERANCE_DEG degrees, about 25 km, for coasts and borders), and is
# not coded as a whole country (GeoNames feature designation PCL...).
COUNTRY_BORDERS = REFERENCE_DIR / "country_borders.geojson"  # from reference/make_country_borders.R
POINT_COUNTRY_TOLERANCE_DEG = 0.25

# Country of an organisation, for the maps (parts B and D): the country prefix of
# its identifier (GB-CHC-..., US-EIN-...); for identifiers without a country prefix
# (XM-, XI-, XR-, legacy codes), the headquarters country in the IATI Registry
# (publishers.parquet, hq_country) if it is a publisher. This is where an
# organisation is registered, not where it works.
# International bodies: XI- and XR- identifiers, and XM- identifiers except
# XM-DAC-<n> with n below DAC_INTERNATIONAL_FROM. Below that number, XM-DAC codes are
# OECD-DAC donor codes of national governments (XM-DAC-3 Denmark, XM-DAC-7
# Netherlands, XM-DAC-302 United States, XM-DAC-236 Benin). From it they are not
# governments: multilateral donors (XM-DAC-903 IFC, XM-DAC-928 WHO), private donors
# (XM-DAC-1601 Gates Foundation), and DAC channel codes of organisations, which
# include multilaterals (XM-DAC-41122 UNICEF, XM-DAC-44000 World Bank), international
# NGOs (XM-DAC-21016 ICRC) and partnerships (XM-DAC-30010 Unitaid).
# Changed from 40000 to 900 after the code review (November 2026): 40000 treated
# 106 refs from 900 to 39999, such as Unitaid and WHO, as national governments.
INTERNATIONAL_PREFIXES = {"XM", "XI", "XR"}
DAC_INTERNATIONAL_FROM = 900
# Other organisation links of the chain's organisations drawn as context (grey lines)
CONTEXT_FLOWS_N = 15
LOCAL_ORG_TYPES = {"11", "22", "23", "24", "72"}  # local gov., national / regional /
# partner-country NGO, private sector (recipient country); see ORG_TYPE below

# =============================================================================
# 7. Labels
# =============================================================================
ORG_TYPE = {
    "10": "Government",
    "11": "Local government",
    "15": "Other public sector",
    "21": "International NGO",
    "22": "National NGO",
    "23": "Regional NGO",
    "24": "Partner country based NGO",
    "30": "Public private partnership",
    "40": "Multilateral",
    "60": "Foundation",
    "70": "Private sector",
    "71": "Private sector (provider country)",
    "72": "Private sector (recipient country)",
    "73": "Private sector (third country)",
    "80": "Academic, training and research",
    "90": "Other",
}
