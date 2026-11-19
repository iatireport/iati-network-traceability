# Data dictionary

Every column of every output file in `outputs/bulk_<index date>/`. The test `test_data_dictionary_complete` checks that this file lists every column. The terms activity, transaction, link, chain, origin and organization path are defined in `docs/methods_decisions.md`, section 0.

**Conventions.**
- Identifiers of organizations (`*_org_ref`, `org_ref`) are trimmed and upper-cased from stage 2 onwards. Activity identifiers keep their published case.
- TRUE/FALSE columns are logical in R. Missing values are `NA` in R.
- Lists inside one cell are separated by `;` (countries, types) or ` > ` (paths).
- Years are numbers. Dates are text `YYYY-MM-DD`; convert with `as.Date()`.
- Values are in the currency in `currency` and are never converted.
- `NA` is Namibia's country code. Missing values in CSV files are empty cells. Read files with country columns using `readr::read_csv("file.csv", na = "")`, or Namibia becomes a missing value (as in `report/full_run.qmd`, section 9).

Read Parquet files in R with `arrow::read_parquet("file.parquet")`, and CSV files with `readr::read_csv("file.csv")`.

---

## Stage 1: `01_parse_bulk.py`

### files.csv
One row per XML file in the bulk copy.

| Column | Description |
|---|---|
| `file_idx` | Position of the file in the sorted file list (used to build `copy_id`) |
| `publisher_folder` | Folder name in `datasets/`: the publisher's registry short name |
| `file` | File name without `.xml` |
| `bytes` | File size |
| `kind` | `activity`, `organisation` or `unknown` (unreadable before the root element) |
| `iati_version` | `version` attribute of `<iati-activities>` |
| `activities` | Activities read (before an error, for failed files) |
| `transactions` | Transactions read (before an error, for failed files) |
| `status` | `ok` or `failed` |
| `error` | Parser error message for failed files |

### datasets_missing.csv
Datasets listed in the bulk index with no XML file in the copy.

| Column | Description |
|---|---|
| `publisher_folder` | Publisher short name |
| `dataset` | Dataset short name |
| `source_url` | The publisher's URL for the dataset |
| `last_attempt` | Time of the last download attempt (from `datasets-full.json`) |
| `last_attempt_result` | The record of that attempt (first 300 characters) |

### publishers.parquet
The registry's publisher list (`reporting-orgs.json`).

| Column | Description |
|---|---|
| `publisher_folder` | Registry short name (= folder name) |
| `organisation_identifier` | Registry organization identifier |
| `iati_identifier` | Registry IATI identifier (usually the same) |
| `publisher_name` | Registry name |
| `organisation_type` | IATI OrganisationType code |
| `hq_country` | Headquarters country |
| `reporting_source_type` | `primary_source` or `secondary_source` |

### activities.parquet
One row per activity **copy**: an activity in one file, before deduplication.

| Column | Description |
|---|---|
| `copy_id` | Unique number of the copy: `file_idx` × 1,000,000 + `position_in_file` |
| `publisher_folder` | Folder of the file |
| `file` | File name without `.xml` |
| `position_in_file` | Order of the activity in the file, from 0 |
| `iati_version` | Version of the file |
| `iati_identifier` | `<iati-identifier>`, trimmed |
| `reporting_org_ref` | `<reporting-org ref>`, as published (trimmed) |
| `reporting_org_type` | `<reporting-org type>` |
| `reporting_org_name` | Reporting organization name: English narrative, else the first, else element text (version 1) |
| `secondary_reporter` | TRUE if `secondary-reporter` is `1` or `true` |
| `last_updated_datetime` | `last-updated-datetime` attribute, as published |
| `hierarchy` | `hierarchy` attribute |
| `activity_status` | `<activity-status code>` |
| `title` | Title (same name rule) |
| `default_currency` | `default-currency` attribute |
| `default_flow_type` | `<default-flow-type code>` |
| `default_finance_type` | `<default-finance-type code>` |
| `default_aid_type` | First `<default-aid-type code>` |
| `default_tied_status` | `<default-tied-status code>` |
| `recipient_countries` | Activity-level recipient country codes, `;`-separated, upper case |
| `recipient_country_percentages` | Their percentages, in the same order |
| `recipient_regions` | Activity-level recipient region codes |
| `start_date` | Actual start date, else planned start date |
| `end_date` | Actual end date, else planned end date |
| `n_transactions` | Number of transactions in this copy |

### transactions.parquet
One row per transaction, with activity defaults applied. No transactions removed apart from failed files.

| Column | Description |
|---|---|
| `copy_id` | Activity copy the transaction belongs to |
| `tx_seq` | Position of the transaction in its activity, from 1. A transaction is identified by `copy_id` + `tx_seq`, because `transaction_ref` is optional and not unique |
| `publisher_folder`, `file` | Where it was published |
| `iati_identifier` | Its activity's identifier |
| `reporting_org_ref` | Its activity's reporting org ref, as published |
| `transaction_ref` | `<transaction ref>` |
| `transaction_type_raw` | Type code as published |
| `transaction_type` | Version 2 type code (1–13), after recoding; missing if invalid |
| `type_recode` | `as published`, `v1 code`, `name`, `missing` or `invalid` |
| `transaction_date` | `<transaction-date iso-date>`, first 10 characters |
| `transaction_year` | Year of `transaction_date` (missing if the date is not `YYYY-MM-DD`) |
| `date_valid` | TRUE if `transaction_date` has the form `YYYY-MM-DD` |
| `value` | Value, as a number |
| `value_valid` | TRUE if the value could be read as a number |
| `currency` | Transaction currency, else the activity's `default-currency` |
| `currency_source` | `transaction`, `activity default` or `missing` |
| `value_date` | `<value value-date>` |
| `flow_type`, `finance_type`, `aid_type`, `tied_status` | Transaction code, else the activity default |
| `provider_org_ref`, `provider_org_type`, `provider_org_name` | `<provider-org>` ref, type and name |
| `provider_activity_id` | `<provider-org provider-activity-id>` |
| `receiver_org_ref`, `receiver_org_type`, `receiver_org_name` | `<receiver-org>` ref, type and name |
| `receiver_activity_id` | `<receiver-org receiver-activity-id>` |
| `tx_recipient_countries` | Transaction-level recipient country codes, `;`-separated |

### related_activities.parquet
One row per `<related-activity>` element.

| Column | Description |
|---|---|
| `copy_id` | Activity copy |
| `iati_identifier` | Activity holding the element |
| `related_activity_ref` | `ref` attribute |
| `related_activity_type` | `type` attribute (1 parent, 2 child, 3 sibling, 4 co-funded, 5 third party) |

---

## Stage 2: `02_clean.py`

### activities_clean.parquet
One row per activity: one copy per `iati_identifier`. The columns are those of `activities.parquet` (with `reporting_org_ref` normalized), plus:

| Column | Description |
|---|---|
| `copy_id`, `publisher_folder`, `file`, `position_in_file`, `iati_version`, `iati_identifier`, `reporting_org_ref`, `reporting_org_type`, `reporting_org_name`, `secondary_reporter`, `last_updated_datetime`, `hierarchy`, `activity_status`, `title`, `default_currency`, `default_flow_type`, `default_finance_type`, `default_aid_type`, `default_tied_status`, `recipient_countries`, `recipient_country_percentages`, `recipient_regions`, `start_date`, `end_date`, `n_transactions` | As in `activities.parquet` |
| `own_folder` | TRUE if the copy is in the folder of the registry publisher whose identifier equals `reporting_org_ref` |
| `publisher_group` | Publisher group: from `reference/publisher_groups.csv` if present, otherwise the IATI organization type label |
| `registry_org_ref` | Registry identifier of the folder's publisher |
| `reporting_ref_matches_registry` | TRUE if `reporting_org_ref` equals `registry_org_ref` |
| `valid_reporting_ref` | TRUE if `reporting_org_ref` is well formed |

### duplicates_removed.csv
One row per activity copy removed by `pick_one_copy_per_activity()`.

| Column | Description |
|---|---|
| `iati_identifier` | The activity |
| `reason` | First rule that decided against this copy: `secondary reporter`, `older last-updated-datetime`, `not in reporting organisation's own folder`, `fewer transactions`, or `same on all rules (file order)` |
| `copy_id`, `publisher_folder`, `file`, `n_transactions`, `last_updated` | The removed copy (`last_updated` is the parsed time, UTC) |
| `copy_id_kept`, `publisher_folder_kept`, `file_kept`, `n_transactions_kept`, `last_updated_kept` | The copy kept |
| `same_folder_as_kept` | TRUE if both copies are in the same publisher folder |

### transactions_clean.parquet
All transactions of the kept activity copies. The columns are those of `transactions.parquet`, with organization refs normalized and activity IDs trimmed, plus:

| Column | Description |
|---|---|
| `copy_id`, `tx_seq`, `publisher_folder`, `file`, `iati_identifier`, `reporting_org_ref`, `transaction_ref`, `transaction_type_raw`, `transaction_type`, `type_recode`, `transaction_date`, `transaction_year`, `date_valid`, `value`, `value_valid`, `currency`, `currency_source`, `value_date`, `flow_type`, `finance_type`, `aid_type`, `tied_status`, `provider_org_ref`, `provider_org_type`, `provider_org_name`, `provider_activity_id`, `receiver_org_ref`, `receiver_org_type`, `receiver_org_name`, `receiver_activity_id`, `tx_recipient_countries` | As in `transactions.parquet` |
| `reporting_org_ref_changed`, `provider_org_ref_changed`, `receiver_org_ref_changed` | TRUE if normalizing (trim, upper case) changed the published ref |
| `direction` | `in` (types 1, 9, 11, 13), `out` (2–8, 10, 12) or `other` (no type, an invalid type, or before the code review type 9) |
| `counterpart_ref`, `counterpart_org_type`, `counterpart_org_name` | The other side: the provider for `in`, the receiver for `out` |
| `counterpart_activity_id` | The other side's activity ID |
| `valid_reporting_ref`, `valid_provider_ref`, `valid_receiver_ref`, `valid_counterpart_ref` | TRUE if that ref is well formed (registration-agency prefix + "-" + more, or a legacy identifier) |
| `internal` | TRUE if the counterpart ref equals the reporting org ref |
| `counterpart_activity_published` | TRUE if `counterpart_activity_id` is a published `iati_identifier` |
| `self_reference` | TRUE if `counterpart_activity_id` is the transaction's own activity |
| `exact_duplicate` | TRUE if identical in content to an earlier transaction of the same activity |
| `publisher_group` | The activity's publisher group |

### related_activities_clean.parquet
Related-activity entries of the kept copies. Blank refs and self-references are removed, and duplicates dropped.

| Column | Description |
|---|---|
| `copy_id`, `iati_identifier`, `related_activity_ref`, `related_activity_type` | As in `related_activities.parquet` |
| `related_published` | TRUE if the related activity is published |
| `related_publisher` | Reporting org of the related activity |
| `same_publisher` | TRUE if both activities have the same reporting org |

### organisations.parquet
One row per organization identifier seen as reporting org, provider or receiver.

| Column | Description |
|---|---|
| `org_ref` | Normalized identifier |
| `org_name` | Name the organization uses for itself when it publishes (most common), else the name others use most often |
| `org_type` | OrganisationType code, chosen by the same rule |
| `org_type_label` | Its label |
| `n_activities_published` | Activities it reports |
| `is_publisher` | TRUE if it reports activities or is in the registry |
| `in_registry` | TRUE if it is a registry identifier |
| `well_formed` | TRUE if the identifier is well formed |
| `partner_code` | TRUE for a publisher's own code for a partner (`config.PARTNER_CODE_PATTERN`: UNICEF's `XM-DAC-41122-VN-…`). Kept in `org_edges`, left out of the organization network (`org_nodes`, `network_summary.json`) |

---

## Stage 3: `03_links.py`

### link_transactions.parquet
One row per transaction that names a published counterpart activity (other than its own): the evidence behind the links. Exact duplicates are left out.

| Column | Description |
|---|---|
| `copy_id`, `tx_seq` | The transaction (see `transactions.parquet`) |
| `iati_identifier` | The reporting activity |
| `side` | `provider` (the funder's outgoing transaction names the receiver) or `receiver` (the receiver's incoming transaction names the funder) |
| `from_activity`, `to_activity` | The link it supports (funder → receiver) |
| `linked_activity` | The other activity (the one named) |
| `named_org_ref`, `named_org_name` | Organization named on the linked side |
| `transaction_type`, `transaction_year`, `value`, `currency` | As in `transactions_clean.parquet` |
| `org_check` | `match`, `other publisher`, `unrecognised ref` or `no org ref` (`check_named_org()`) |

### activity_edges.parquet / activity_edges.csv
One row per link (from_activity, to_activity).

| Column | Description |
|---|---|
| `from_activity`, `to_activity` | Funder's activity → receiver's activity |
| `from_org`, `from_org_name`, `from_org_type`, `from_publisher_group` | Publisher of the from activity |
| `to_org`, `to_org_name`, `to_org_type`, `to_publisher_group` | Publisher of the to activity |
| `reported_by_provider` | TRUE if the funder's transactions report the link |
| `reported_by_receiver` | TRUE if the receiver's transactions report the link |
| `evidence` | `confirmed` (both sides), `one-sided`, or `implausible` |
| `n_transactions`, `n_provider_transactions`, `n_receiver_transactions` | Transactions behind the link, in total and by side |
| `transaction_types` | Types behind the link, e.g. `1;2;3` |
| `expenditure_only` | TRUE if all transactions are expenditure (type 4) |
| `first_year`, `last_year` | Years of the transactions behind the link |
| `provider_value_by_currency`, `receiver_value_by_currency` | Value sums by type and currency, e.g. `type 2 DKK 245000000; type 3 DKK 245000000`. Never summed across types or currencies. `NA` where every value in the group is unreadable |
| `provider_org_check`, `receiver_org_check` | Best `org_check` on each side (missing if that side does not report) |
| `plausibility` | `org matches`, `names another publisher`, `reverses own funding link`, `unrecognised org ref` or `no org ref` (`is_plausible_link()`) |
| `plausible` | Plausible under `config.PLAUSIBILITY_RULE` |
| `plausible_strict` | Plausible under the strict rule |
| `same_org` | TRUE if both activities have the same reporting-org ref; if either ref is missing, TRUE if they are in the same publisher folder (`same_organisation()`). Two refs in one folder count as two organizations |
| `in_network` | TRUE if the link is used in the network: plausible and, by default, between different organizations |
| `from_publisher_folder`, `to_publisher_folder` | Folders of the two activities |

### activity_edge_values.parquet
Long form of the values behind each link.

| Column | Description |
|---|---|
| `from_activity`, `to_activity` | The link |
| `side` | `provider` or `receiver` |
| `transaction_type`, `currency` | Grouping |
| `value` | Sum of values |
| `n_transactions` | Number of transactions |

### org_edges.parquet / org_edges.csv
One row per organization link (from_org, to_org), from transactions naming an organization ref on the other side. Internal flows are left out. Pairs that appear only through an activity link are included.

| Column | Description |
|---|---|
| `from_org`, `to_org` | Funder → receiver organization |
| `n_transactions`, `n_provider_transactions`, `n_receiver_transactions` | Transactions naming the pair, in total and by side |
| `first_year`, `last_year`, `transaction_types` | As for activity links |
| `provider_value_by_currency`, `receiver_value_by_currency` | As for activity links |
| `n_activity_links` | Network activity links between the two organizations |
| `n_activity_links_confirmed` | ...of which confirmed |
| `reported_by_provider`, `reported_by_receiver` | TRUE if that side names the other |
| `has_activity_link` | TRUE if at least one network activity link joins them |
| `from_org_name`, `from_org_type`, `to_org_name`, `to_org_type` | From `organisations.parquet` |
| `from_is_publisher`, `to_is_publisher` | TRUE if the organization is an IATI publisher |
| `from_well_formed`, `to_well_formed` | TRUE if the identifier is well formed |

### org_edge_values.parquet
Long form of the values behind each organization link. Columns as in `activity_edge_values.parquet`, with `from_org` and `to_org`.

| Column | Description |
|---|---|
| `from_org`, `to_org` | The organization link |
| `side`, `transaction_type`, `currency`, `value`, `n_transactions` | As in `activity_edge_values.parquet` |

---

## Stage 4: `04_network.py`

### cycles.csv
One row per cycle (group of 2+ activities that can reach each other) left after excluding implausible links.

| Column | Description |
|---|---|
| `cycle_id` | Number |
| `n_activities`, `n_edges` | Size |
| `activities` | Member activities, ` \| `-separated |
| `orgs` | Their publishers |
| `edges` | The links inside the cycle, `from > to` |
| `evidence` | Evidence of each of those links, in the same order |

### chains.csv
One row per chain: a path from an origin to an end.

| Column | Description |
|---|---|
| `chain_id` | Number (joins to `chain_steps.csv`) |
| `steps` | Number of links |
| `origin_activity`, `end_activity` | First and last activity |
| `path_activities` | All activities, ` > `-separated |
| `path_orgs`, `path_org_refs`, `path_org_types` | Their publishers' names, identifiers and types |
| `weakest_evidence` | `confirmed` if every step is confirmed, else `one-sided` |
| `n_confirmed_steps` | Steps confirmed by both sides |
| `countries` | Recipient countries of all activities on the chain |
| `first_year`, `last_year`, `years` | Years of the transactions behind its links |
| `origin_publisher_group` | Group of the origin's publisher |
| `end_reason` | Why the chain stops at its end activity (see `chain_ends.parquet`). `onward link set aside (inside a cycle)` where the end activity's onward links were all set aside as part of a cycle (3 chains in the bulk copy of 30 October 2026) |
| `completeness` | Chain completeness score, 0 to 1: the points of all its steps (columns `receiver_ref_well_formed`, `funder_names_activity`, `receiver_confirms` in `chain_steps.csv`, one point each) divided by three times the number of steps. Added November 2026 (working paper) |

### chain_steps.csv
The chains in long form: one row per step.

| Column | Description |
|---|---|
| `chain_id`, `step` | Chain and position (1 = first link) |
| `from_activity`, `to_activity` | The link |
| `from_org`, `from_org_name`, `to_org`, `to_org_name` | Publishers |
| `evidence`, `transaction_types`, `first_year`, `last_year` | From `activity_edges.parquet` |
| `receiver_ref_well_formed` | TRUE if a transaction reported by the funder names a well-formed receiver organization identifier (`link_transactions.parquet`, side `provider`) |
| `funder_names_activity` | TRUE if the funder reports the link, naming the receiver's published activity (`reported_by_provider`) |
| `receiver_confirms` | TRUE if the receiver reports the link with an incoming transaction naming the funder's activity (`reported_by_receiver`) |
| `step_points` | The number of the three above that are TRUE (0 to 3); used for `completeness` in `chains.csv` |

### chain_origins.csv
One row per origin.

| Column | Description |
|---|---|
| `origin_activity` | The origin |
| `longest_steps` | Steps of its longest chain (indicator 6 averages this) |
| `n_chains` | Chains starting there |
| `origin_publisher_group` | Its publisher group |

### org_paths.csv
One row per organization path: a distinct sequence of organizations along chains.

| Column | Description |
|---|---|
| `org_path_id` | Number |
| `path_org_refs`, `path_orgs`, `path_org_types` | The organizations |
| `steps` | Number of links |
| `n_chains` | Chains following this path |
| `n_origin_activities` | Distinct origin activities among them |
| `first_year`, `last_year` | Years |
| `origin_publisher_group` | Group of the first organization |
| `n_chains_confirmed` | Chains on this path confirmed at every step |
| `best_evidence` | `confirmed` if `n_chains_confirmed` > 0, else `one-sided` |
| `countries` | Recipient countries of all chains on the path |

### chain_end_reasons_by_year.csv
As `chain_end_reasons.csv`, for the activities with at least one transaction dated in each year of `config.SUBSETS` (2023, 2024, 2025; exact duplicates left out), the rule stage 5 uses to select activities for a year. Added November 2026 for the working paper.

| Column | Description |
|---|---|
| `scope` | `all_2023`, `all_2024`, `all_2025` (as in `indicators.csv`) |
| `group` | `chain ends (in network)` or `all receiving activities` |
| `end_reason` | The eight reasons, strongest evidence first |
| `activities` | Activities (unit: activities) |
| `percent` | Share of the group in that year |

### multipliers.csv
One row per organization identifier that is an intermediary (neither first nor last) on at least one organization path: the network multipliers. All counts use only the paths where the organization is an intermediary, each path once, and never count the organization as its own partner. Added November 2026 (working paper).

| Column | Description |
|---|---|
| `org_ref`, `org_name`, `org_type` | The organization (name and IATI organization type as on the first path listed) |
| `paths_through` | Organization paths on which it is an intermediary |
| `direct_recipients` | Distinct organizations right after it on those paths |
| `downstream_orgs` | Distinct organizations after it on those paths |
| `upstream_orgs` | Distinct organizations before it on those paths |

### depth_distribution.csv
Chain depth for all chains in the global network.

| Column | Description |
|---|---|
| `unit` | `chains`, `origins (longest chain)` or `organisation paths` |
| `origin_group` | `All publishers` or a publisher group |
| `steps` | `1`, `2`, `3` or `4+` |
| `n`, `percent` | Count and share within unit and group |

### chain_ends.parquet
One row per activity that receives funds (incoming transactions, or an incoming network link) but has no onward link in the network.

| Column | Description |
|---|---|
| `iati_identifier` | The activity |
| `n_outgoing_transactions` | Its commitments, disbursements and expenditure |
| `n_outgoing_to_others` | ...not internal |
| `end_reason` | The best evidence of where the money went, in this order: `link only to own organisation or implausible`, `activity ID given but not published`, `organisation ID given as activity ID`, `receiver organisation ID only`, `receiver name only`, `outgoing only to itself`, `no receiver named`, `no outgoing transactions` |
| `in_network` | TRUE if the activity is in the network (an end of a chain) |
| `reporting_org_ref`, `publisher_group` | Its publisher |

### chain_end_reasons.csv
`chain_ends.parquet` summarized.

| Column | Description |
|---|---|
| `scope` | `chain ends (in network)` or `all receiving activities` |
| `end_reason` | Reason |
| `activities`, `percent` | Count and share |

### network_nodes.parquet
One row per activity in the network.

| Column | Description |
|---|---|
| `iati_identifier` | The activity |
| `in_degree`, `out_degree` | Incoming and outgoing network links |
| `component` | Number of its connected group (1 = largest) |
| `in_cycle` | TRUE if part of a cycle |
| `longest_steps_from_here` | Longest chain starting at this activity |
| `reporting_org_ref`, `publisher_group` | Publisher |
| `recipient_countries` | Activity-level countries |
| `countries` | Activity-level countries, else transaction-level |

### org_nodes.parquet / org_nodes.csv
One row per organization in the organization network. Partner codes (`partner_code` in `organisations.parquet`) and their links are left out, so degrees and components are those of the network without them.

| Column | Description |
|---|---|
| `org_ref`, `org_name`, `org_type`, `is_publisher` | From `organisations.parquet` |
| `funds_n_orgs`, `funded_by_n_orgs` | Outgoing and incoming organization links |
| `component` | Connected group in the organization network (1 = largest) |
| `in_activity_network` | TRUE if it publishes an activity in the network, or is linked by one |
| `activity_network_component` | Its group in the activity network seen as organizations |
| `activities_in_network` | Its activities in the network |

---

## Stage 5: `05_indicators.py`

### indicators.csv
One row per scope × group × indicator. Definitions are in `docs/indicators.md`.

| Column | Description |
|---|---|
| `scope` | `global`, or `<country or all>_<year or all>` |
| `country`, `year` | The subset (`all` if not restricted) |
| `group` | `All publishers` or a publisher group (origin group for 6–6e) |
| `indicator` | Code: 1, 2, 3, 4, 5, 6, 6b–6e, 6f (tentative), 7a, 7b, x1, x1b, D1, D1a, D1b, D2, D2g (D2g: one row per publisher group, denominator = all traced transactions in the scope) |
| `name` | Indicator name |
| `unit` | What is counted: activities, transactions, organizations, origins, chains or organization paths |
| `numerator`, `denominator` | Counts |
| `percent` | 100 × numerator / denominator |
| `value` | For 6 (mean steps) and 6c (maximum steps) |
| `small_base` | TRUE if the denominator is below `config.SMALL_BASE` (30): do not report the percentage |


### indicator3_by_publisher.csv
Indicator 3 for each reporting organization, all years (exact duplicates left out), under the default setting (commitments and disbursements pooled) and disbursements only (type 3). Same rule as `indicators.csv`; the default columns add up to its "All publishers" row (stage 5 checks this). Transactions of activities without a reporting-org identifier form one row with an empty `reporting_org_ref`; leave it out of publisher-level statistics. Added November 2026 for the working paper, to compare with the publisher-level figures of McCarthy et al. (2026).

| Column | Description |
|---|---|
| `reporting_org_ref`, `publisher_group` | The reporting organization and its publisher group |
| `outgoing`, `valid`, `rate` | Outgoing commitments and disbursements (not internal), those with a well-formed receiver identifier, and the rate (%) |
| `disb_outgoing`, `disb_valid`, `disb_rate` | The same for disbursements only (type 3); `disb_rate` empty if none |
| `has_disbursement` | TRUE if the organization reports at least one outgoing disbursement |

### indicators_by_publisher.csv
The transaction indicators (2, 2b, 3, 3b, 4, 5, x1, x1b) for each reporting organization and scope, computed from the same flags as `indicators.csv`, so the rows of all publishers add up to its "All publishers" row (stage 5 checks this). Transactions of activities without a reporting-org identifier form one row with an empty `reporting_org_ref`. Rows with a zero denominator are left out. Added November 2026 for the working paper.

| Column | Description |
|---|---|
| `scope` | As in `indicators.csv` (`global`, `all_2023`, ...) |
| `reporting_org_ref` | The reporting organization |
| `publisher_group` | Its publisher group (IATI organization type) |
| `indicator` | Indicator code |
| `numerator`, `denominator` | Counts (unit: transactions) |
| `percent` | 100 × numerator / denominator, one decimal |

### depth_by_scope.csv
Chain depth in three units for every scope.

| Column | Description |
|---|---|
| `scope`, `country`, `year` | As in `indicators.csv` |
| `origin_group` | `All publishers` or a group |
| `unit`, `steps`, `n`, `percent` | As in `depth_distribution.csv` |

---

## Stage 6: `06_spatial.py`

How records are placed in countries, and the three multi-country rules, are in `docs/methods_decisions.md`, section 7.

### country_indicators.csv
Indicators 1 and 3 by recipient country. One row per scope × country × multi-country rule × indicator.

| Column | Description |
|---|---|
| `scope`, `year` | `global` (all years) or `all_<year>` (transactions dated in the year), as in `indicators.csv` |
| `country` | Recipient country code (two capital letters) |
| `rule` | Multi-country rule: `every`, `single` or `split` |
| `default_rule` | TRUE for the rule in `config.MULTI_COUNTRY_RULE` (the one drawn on the maps) |
| `indicator`, `name`, `unit` | `1` (unit: activities) or `3` (unit: transactions), as in `indicators.csv` |
| `numerator`, `denominator` | Counts. Under `split` these are sums of shares, so they can be fractions |
| `percent` | 100 × numerator / denominator |
| `records` | Activities or transactions in the denominator, each counted once in this country whatever its share |
| `small_base` | TRUE if the denominator is below `config.SMALL_BASE` (30): show the count, not the percentage |

### spatial_unplaced.csv
Activities in a scope that cannot be placed on a country map. An activity naming several regions or codes is counted under each, so rows within one reason can overlap.

| Column | Description |
|---|---|
| `scope`, `year` | As in `country_indicators.csv` |
| `reason` | `region only`, `non-standard country code only` (for example `GLOBAL`, `998`, `HTI`) or `no country or region` |
| `code` | The region code or the non-standard code (empty for `no country or region`) |
| `activities` | Activities in the scope |
| `activities_connected` | ...connected to another IATI activity (indicator 1 numerator) |
| `outgoing_transactions` | Their transactions in the indicator 3 denominator |
| `outgoing_valid_receiver` | ...with a valid receiver ID (indicator 3 numerator) |

### spatial_links_unplaced.csv
Network links (all years) by where each end activity can be placed.

| Column | Description |
|---|---|
| `side` | `funder` (from activity) or `receiver` (to activity) |
| `placement` | `one country`, `several countries`, `region only` or `no country or region` |
| `links` | Network links |

### locations.parquet
One row per `<location>` element of a kept activity copy (part D). Read from the bulk copy that stage 1 used; reused on later runs unless stage 2 has been rerun since.

| Column | Description |
|---|---|
| `copy_id`, `iati_identifier` | The activity copy kept in stage 2 |
| `loc_seq` | Position of the location within the activity (0 = first) |
| `location_ref` | The publisher's own reference (`ref` attribute) |
| `name` | Location name (English narrative if there is one; version 1: element text) |
| `location_reach` | Code: 1 activity happens here, 2 beneficiaries live here |
| `location_id_vocabulary`, `location_id_code` | First `<location-id>` (for example GeoNames, vocabulary G1) |
| `n_administrative` | Number of `<administrative>` elements |
| `admin_vocabulary`, `admin_level`, `admin_code` | First `<administrative>` element; version 1 `adm2` (else `adm1`) with vocabulary `v1` |
| `pos` | Text of `<point><pos>`, latitude then longitude, as published |
| `v1_latitude`, `v1_longitude`, `v1_precision` | Version 1 `<coordinates>` attributes |
| `exactness` | Code: 1 exact, 2 approximate |
| `location_class` | Code: 1 administrative region, 2 populated place, 3 structure, 4 other topographical feature |
| `feature_designation` | GeoNames feature code, for example `PPLC` (capital) |
| `lat`, `lon` | Latitude and longitude read from `pos`, else from the version 1 attributes |
| `point_status` | `valid`, `no point`, `unreadable`, or `out of range or 0,0` (0,0 is a common placeholder) |
| `in_recipient_country` | TRUE if a valid point lies inside one of its activity's recipient countries (Natural Earth borders widened by `config.POINT_COUNTRY_TOLERANCE_DEG`, about 25 km); FALSE if outside all of them; empty if the point is not valid or the activity has no country |
| `country_level` | TRUE if the location is coded as a whole country (`feature_designation` starting `PCL`) |
| `point_usable` | TRUE if the point is valid, inside a recipient country and not country-level: usable for the example maps |

### geo_chains.csv
One row per chain of `config.SHOWCASE_MIN_STEPS` (2) or more steps, with its locations and its rank as an example (part D).

| Column | Description |
|---|---|
| `chain_id`, `steps`, `path_activities`, `path_orgs`, `path_org_refs`, `path_org_types`, `weakest_evidence`, `n_confirmed_steps`, `countries`, `first_year`, `last_year` | As in `chains.csv` |
| `end_activity` | Last activity of the chain |
| `end_org`, `end_org_ref`, `end_org_type` | Its publisher |
| `end_local` | TRUE if the end publisher's type is in `config.LOCAL_ORG_TYPES` (local government, national, regional or partner-country NGO, private sector in the recipient country) |
| `end_points` | Valid points of the end activity |
| `end_points_usable` | ...of which usable (`point_usable` in `locations.parquet`) |
| `end_admin_areas` | Locations of the end activity with an administrative area |
| `end_problem` | Why the end activity has no usable point: `no location`, `no valid point`, `point outside recipient country`, `country-level point only`; empty if it has one |
| `activities_with_points` | Activities on the chain with at least one usable point |
| `showcase_rank` | Rank as an example, among chains whose end activity has a usable point (empty otherwise, or if a better-ranked chain has the same organization path, the same end publisher, or the same route before the end publisher). Order: local end publisher, more confirmed steps, more steps, more activities with points |
| `showcase` | TRUE for the first `config.SHOWCASE_N` (5) ranks |
| `counterexample_rank` | The same ranking, among chains whose end activity has no usable point |
| `counterexample` | TRUE for the first `config.COUNTEREXAMPLE_N` (1) |

### geo_chain_points.csv
The valid points of every activity on the chains whose end activity has a valid point, and on the counter-example chains. One row per chain × activity × point.

| Column | Description |
|---|---|
| `chain_id`, `showcase_rank`, `counterexample_rank` | As in `geo_chains.csv` |
| `iati_identifier` | Activity on the chain |
| `position` | Its place in the chain: 0 = origin, then 1, 2, ... |
| `is_end` | TRUE for the end activity |
| `org_name` | Its publisher |
| `loc_seq`, `location_name`, `lat`, `lon`, `exactness`, `location_reach`, `feature_designation`, `in_recipient_country`, `country_level`, `point_usable` | As in `locations.parquet` |

### geo_chain_orgs.csv
The organizations of the showcase and counter-example chains, placed by country of registration (for the global maps). One row per chain × position.

| Column | Description |
|---|---|
| `chain_id`, `position`, `iati_identifier` | As in `geo_chain_points.csv` |
| `role` | `funder` (position 0), `intermediary` or `implementer` (end) |
| `org_ref`, `org_name`, `org_type` | The publisher of that activity |
| `org_country` | Country of registration: the identifier's country prefix; for `XM`/`XI`/`XR` or no prefix, the IATI Registry headquarters country. Where the organization is registered, not where it works |
| `org_country_source` | `identifier prefix`, `registry headquarters` or `none` |
| `international` | TRUE for international bodies: `XI` and `XR` identifiers, and `XM` identifiers except OECD-DAC donor codes of national governments (`XM-DAC-` below `config.DAC_INTERNATIONAL_FROM`, 900; for example `XM-DAC-7`, the Netherlands) |
| `country_name`, `lon`, `lat` | Name of `org_country` and its Natural Earth label point, where the organization is drawn |

### geo_chain_context.csv
Other organization links of each example chain's organizations, drawn as gray context lines: the `config.CONTEXT_FLOWS_N` (15) largest outgoing links per chain, by transactions, to organizations not on the chain with a known country.

| Column | Description |
|---|---|
| `chain_id` | The example chain |
| `from_org` | Its organization |
| `to_org`, `to_org_name` | The other organization |
| `to_country` | Its country of registration (as `org_country` in `geo_chain_orgs.csv`) |
| `n_transactions` | Transactions behind the organization link (`org_edges.parquet`) |

### country_flows.csv
Network links (unit: links; all 13,024 plausible links between organizations, all years) by where the funder is registered and where the receiving activity works (part B). One row per pair.

| Column | Description |
|---|---|
| `from_country` | Country of registration of the funding organization (as `org_country` in `geo_chain_orgs.csv`); `unknown` if it has none |
| `from_international` | TRUE if the funder is an international body (as `international` in `geo_chain_orgs.csv`); its country, if any, is its Registry headquarters. Not drawn on the map |
| `to_place` | The receiving activity's recipient country if it names exactly one (placed as in part A); otherwise `several countries`, `region only` or `no country or region` |
| `links` | Network links |
| `links_confirmed` | ...reported by both sides |
| `links_receiver_registered_here` | ...whose receiving organization is registered in `to_place` (a local implementer) |
| `to_is_country` | TRUE if `to_place` is a country code |
| `domestic` | TRUE if the funder is registered in the recipient country |

### country_flows_by_year.csv
As `country_flows.csv`, for the network links with at least one link transaction dated in each year of `config.SUBSETS` (2023, 2024, 2025), with a `year` column. The network is the global one; the year only selects links. Added November 2026 for the working paper.

| Column | Description |
|---|---|
| `year` | Year of at least one of the link's transactions |
| `from_country`, `from_international`, `to_place` | As in `country_flows.csv` |
| `links`, `links_confirmed`, `links_receiver_registered_here` | As in `country_flows.csv`, for that year's links |
| `to_is_country`, `domestic` | As in `country_flows.csv` |

### local_links_steps.csv
Every network link received by a local organization (the receiving organization is registered in the receiving activity's one recipient country, as `links_receiver_registered_here` in `country_flows.csv`), with the number of steps from a chain origin to it. Added November 2026 for the working paper.

| Column | Description |
|---|---|
| `from_activity`, `to_activity`, `from_org`, `to_org` | The link |
| `to_place` | The recipient country (where the receiving organization is registered) |
| `steps_from_origin` | Fewest steps from a chain origin to this link: 1 = funded directly by the chain's first funder; 2 = through one intermediary; empty if the link is in no chain |
| `origin_org` | First organization of the chain with the fewest steps (lowest chain_id if tied) |
| `origin_country`, `origin_international` | Its country of registration and whether it is an international body (as in `org_countries.csv`) |

### org_countries.csv
One row per organization identifier in `org_edges.parquet` (both ends; UNICEF partner codes included): its country of registration, as used for the maps (`06_spatial.org_countries()`; rule in `docs/methods_decisions.md`, section 7). Added November 2026 for the working paper, so tables and chain diagrams can show where each organization is registered.

| Column | Description |
|---|---|
| `org_ref` | Organization identifier |
| `org_country` | Two-letter country of registration; empty if none |
| `org_country_source` | `identifier prefix`, `registry headquarters` or `none` |
| `international` | TRUE for international bodies (XI-, XR-, XM- identifiers, except XM-DAC codes below 900) |

---

## Run records

| File | Content |
|---|---|
| `run_manifest.json` | Written by `run_all.py`: bulk index date, files read / skipped / failed, row counts after every step, settings, package versions, code version |
| `indicator_settings.json` | Settings used by stage 5 |
| `network_summary.json` | Stage 4 counts |
| `summaries/<stage>.json` | Each stage's counts |
| `logs/<stage>.log` | Each stage's log |
