# Indicators

Computed by `05_indicators.py` and written to `indicators.csv`. There is one row per scope × publisher group × indicator, with columns:
- `scope`, `country`, `year`;
- `group`;
- `indicator`, `name`, `unit`;
- `numerator`, `denominator`, `percent` (100 × numerator / denominator);
- `value` (for indicators that are not shares);
- `small_base` (TRUE when the denominator is below `config.SMALL_BASE` = 30; do not report percentages for these).

The terms activity, transaction, link, chain, origin and organization path are defined in `docs/methods_decisions.md`, section 0. The **unit** column says which one each count is in.

## Rules common to all indicators

**Exact duplicates.** Exact duplicate transactions are left out of everything except D1.

**Network.** The network is always the global one. It is made of plausible links between different organizations (`in_network` in `activity_edges.parquet`), in any year and any country. A subset only selects which activities or transactions are counted; a link from a subset activity to an activity outside the subset still counts.

**Scopes.** A scope is either "global" (all countries, all years) or a subset in `config.SUBSETS`, written as (country, year):
- **Transactions in a subset.** The transaction's countries include the country. A transaction's countries are its activity's activity-level countries; only if the activity names none (after dropping codes that are not two capital letters) are the transaction's own countries used. This is the rule of the country maps in stage 6 (`common.transaction_countries()`). Until the code review (November 2026) stage 5 counted a transaction if the activity *or* the transaction named the country; for Uganda 2025 both rules give the same numbers. For the year, there are two options:
  - transaction-date rule (`YEAR_BY_TRANSACTION_DATE = True`, the default): the transaction is dated in the year;
  - activity-period rule (`False`): the activity's start–end period overlaps the year. A missing start or end is replaced by the year of its first or last transaction.
- **Activities in a scope.** Activities with at least one transaction in the scope. About 99,700 activities publish no transactions, so the global scope has 794,616 activities, not 894,287.

**Groups.** "All publishers", then each publisher group: the IATI organization type of the reporting organization, or `reference/publisher_groups.csv` if that file exists. For indicator 6 and its variants, the group is that of the chain's origin.

**Transaction types in indicators 3, 4 and x1.** Indicator 3 counts outgoing commitments and disbursements (types 2, 3). Indicators 4, x1 and x1b count every transaction with a direction: incoming types 1, 9, 11 and 13, and outgoing types 2, 3, 5, 6, 7, 8, 10 and 12. In indicator 4, 198,220 of the 2,236,776 transactions in the denominator are types 5 to 8, 10, 12 or 13; most are interest and loan repayments (types 5 and 6). Expenditure (type 4) is added to all three if `INDICATOR3_INCLUDE_EXPENDITURE = True`; the one setting applies to all three. Under `DISBURSEMENTS_ONLY`, indicators 3, 4, x1 and x1b are limited to types 1 and 3.

**Internal transactions.** A transaction whose counterpart ref equals the reporting org's own ref is internal. Internal transactions are excluded from indicators 3, 4, 5 and D2.

## Definitions

| Code | Name | Unit | Numerator | Denominator |
|---|---|---|---|---|
| 1 | Activities connected to another IATI activity | activities | Activities in the scope that are an end of at least one network link. If `INDICATOR1_RELATED_ACTIVITIES` is "other_publishers" or "all", activities with a related-activity link to a published activity (of another publisher, or of any publisher) are added. | Activities in the scope |
| 2 | Incoming transactions with a valid provider ID | transactions | Those whose provider ref is well formed: it starts with a registration-agency prefix from the codelist (`reference/org_registration_agencies.csv`) followed by "-" and more, or it is a legacy identifier. "Valid" means conformant to the codelist, not "a real organization": `US-GOV` is not in the codelist, so USAID's own identifier `US-GOV-1` is not valid (see the note below the table). | Incoming funds and incoming commitments (types 1, 11) |
| 2b | Incoming: provider ID is a publisher's identifier not in the codelist | transactions | Those whose provider ref is not valid (as in 2) but is the identifier of an IATI publisher (it publishes activities in the bulk copy, or it is in the Registry), e.g. USAID's `US-GOV-1` | As in 2 |
| 3 | Outgoing transactions with a valid receiver ID | transactions | Those whose receiver ref is well formed, as in 2 | Commitments and disbursements (types 2, 3; and 4 if the expenditure setting is on), not internal |
| 3b | Outgoing: receiver ID is a publisher's identifier not in the codelist | transactions | Those whose receiver ref is not valid but is the identifier of an IATI publisher, as in 2b | As in 3 |
| 4 | Counterpart IDs that match an IATI publisher | transactions | Those whose counterpart ref belongs to an IATI publisher: it reports activities in the bulk copy, or it is in the registry. | Incoming and outgoing transactions naming a counterpart ref, not internal (type 4 only if the expenditure setting is on) |
| 5 | Outgoing links to published activities confirmed by the receiver | transactions | Those where the receiver activity also reports the link (the edge is `reported_by_receiver`) | Commitments and disbursements, not internal, naming a published receiver activity other than their own |
| 6 | Average observable chain depth | origins | — (`value` = mean, over origins, of the longest chain from each origin, in steps) | Origins of chains passing through at least one activity in the scope |
| 6b | Origins whose longest chain has 2+ steps | origins | Origins with a longest chain of 2+ steps | As in 6 |
| 6c | Longest observed chain | chains | — (`value` = maximum steps) | Chains passing through the scope |
| 6d | Chains with 2+ steps | chains | Chains with 2+ steps | Chains passing through the scope |
| 6e | Organization paths with 2+ steps | organization paths | Distinct organization paths with 2+ steps | Distinct organization paths of the chains passing through the scope |
| 6f | Chain completeness score (**tentative**) | chains | — (`value` = mean, over chains, of `completeness` in `chains.csv`) | Chains passing through the scope. Added November 2026 as a tentative indicator: it may be renamed or removed, because the IATI Data Quality Dashboard already reports a different "completeness" measure and the paper should not add indicators that do not follow existing methodologies |
| 7a | Isolated activities | activities | Activities in the scope not connected (the complement of 1) | Activities in the scope |
| 7b | Isolated publishing organizations | organizations | Reporting organizations none of whose activities in the scope are connected | Reporting organizations with activities in the scope. Activities without a reporting-org ref (440 in the whole copy) are not counted |
| x1 | Counterpart activity ID given but not published | transactions | Those whose counterpart activity ID is not a published `iati-identifier` | Incoming and outgoing transactions naming a counterpart activity ID other than their own (type 4 only if the expenditure setting is on), **excluding** those counted in x1b |
| x1b | Organization ID given in the activity-ID field | transactions | Those whose "activity ID" is not a published activity but equals a known organization ref (`common.org_id_as_activity_id()`), e.g. USAID's `receiver-activity-id="US-GOV-1"` | As x1, before the x1b transactions are excluded |
| D1 | Exact duplicate transactions | transactions | Transactions identical in every content field (`CONTENT_COLS` in `02_clean.py`) to an earlier one in the same activity | All transactions in the scope, duplicates included |
| D1a | Activities with a duplicate copy removed | activities | Activities in the scope that had another copy removed in stage 2 | Activities in the scope |
| D1b | Flows reported by both sides (double-count risk) | transactions | As 5. Summing values across publishers would count these flows twice. | As 5 |
| D2 | Concentration of traced links | transactions | Traced transactions of the 10 publishers with the most of them | Traced transactions: those naming a published counterpart activity, not internal, not their own activity |
| D2g | Traced transactions held by the publisher group | transactions | Traced transactions (as in D2) reported by publishers of the group (0 for a group with transactions in the scope but none traced) | All traced transactions in the scope, all publishers (so the group shares add up to 100%). Added November 2026 for the working paper |

**Chain completeness score (chain level, per chain; added November 2026).** In `chains.csv` (`completeness`); its mean over the chains in a scope is the tentative indicator 6f in `indicators.csv`. For each step of a chain, one point each for a well-formed receiver organization identifier on the funder's transaction, a receiver activity identifier that resolves to a published activity, and a confirming incoming transaction from the receiver naming the funder's activity; the score is the total divided by three times the number of steps (0 to 1). Proposed for the working paper with reference to the fields traced by EyeOpenerWorks and Open Works (2020); the formula is new. See `docs/methods_decisions.md`, section 9.

**By reporting organization.** The transaction indicators for each reporting organization and scope are in `indicators_by_publisher.csv` (added November 2026).

**Depth by scope.** Chain depth in all three units (chains, origins, organization paths), by steps 1 / 2 / 3 / 4+, for each scope and origin group, is in `depth_by_scope.csv`.

**Note on "valid" identifiers (indicators 2 and 3).** The rule accepts only identifiers whose prefix is in the registration-agency codelist, or that are on the legacy list. Some large publishers write their own identifier with a prefix that is in neither: 86 of 1,974 publishing organizations, which publish 380,887 of 893,847 activities, for example USAID and other US agencies (`US-GOV-…`), Sweden (`SE-0`), the Gates Foundation (`DAC-1601`) and the World Bank (`44000`). Where a transaction names one of these publishers' own identifiers, it counts as not valid. **Decision (November 2026, code review M3):** indicators 2 and 3 keep the codelist rule, and these refs are reported separately as 2b and 3b. Global, all publishers: 2b is 5.6% (78,415 of 1,401,645 transactions, of which 36,731 name `US-GOV-…`) and 3b is 0.5% (24,603 of 5,210,271). So 62.8% + 5.6% = 68.4% of incoming transactions name either a valid ID or a publisher's own identifier, and 19.8% + 0.5% = 20.3% of outgoing ones. By year, 2b is 7.1% (2023), 7.0% (2024) and 5.6% (2025); among publisher groups with a base of 30 or more, it is highest for "Other public sector" (9.2%) and "Multilateral" (6.4%).

## Country versions of indicators 1 and 3 (stage 6)

`06_spatial.py` computes indicator 1 (unit: activities) and indicator 3 (unit: transactions) for each recipient country, for all years and for 2025 (`config.SPATIAL_YEARS`), and writes them to `country_indicators.csv`. The definitions are those of stage 5 (`connected_activities()`, `indicator3_flags()`), on the global network. Exact duplicates are left out.

- **Which country.** A transaction is placed in its activity's activity-level countries; only if the activity names none, in its own countries (`common.transaction_countries()`). An activity is placed in its own countries; if it names none, in all countries of its transactions in the scope. Only two-letter codes are countries. This is also the rule of stage 5's country subsets, so a country's values under the rule "every" equal stage 5's for that country and year.
- **Multi-country records.** Computed under three rules (`config.MULTI_COUNTRY_RULES`): `every` (counted in full in each country; the default, `config.MULTI_COUNTRY_RULE`), `single` (only records naming one country) and `split` (divided by the published percentages, or equally). Under `split`, numerators and denominators can be fractions; `records` gives the whole number of records behind the denominator.
- **Small bases.** `small_base` is TRUE when the denominator is below 30, as in `indicators.csv`.
- What cannot be placed is in `spatial_unplaced.csv` (activities and transactions) and `spatial_links_unplaced.csv` (links). See `docs/methods_decisions.md`, section 7.

## Differences from the pilot

| Indicator | Pilot | Full run | Why |
|---|---|---|---|
| 1, 6, 7 | The network was built from links in the downloaded country-year, plus one step outward. | The network is global. | Chains cross countries and years (guide 2.1 A, B). |
| 1, 6, 7 | Every link to a published activity counted, if not internal. | Only plausible links between different organizations count. | Guide 2.1 E; `docs/methods_decisions.md`, section 3.1 |
| 3, 4, x1 | Expenditure was "other" and never counted. | Expenditure is counted only if the setting is on (default off). | Open question for the Secretariat |
| 5 | Confirmation was looked up through the API. | Confirmation comes from the edge table. | Same definition; the bulk copy holds all activities |
| 6 | One measure (mean per origin). | Also 6d (per chain) and 6e (per organization path). | The three units answer different questions |
| x1 | Self-references were counted. | Self-references are excluded. | A transaction naming its own activity names no counterpart. |
| D1 | Exact duplicates were removed. | Exact duplicates are flagged and left out. | Same effect on the indicators. Duplicate activity copies are a separate measure (D1a). |

## Sensitivity to the open questions (bulk copy of 30 October 2026)

Percent for all publishers; indicator 6 in steps. Each row changes one setting from the default.

| Setting | Scope | 1 | 3 | 4 | x1 | 6 |
|---|---|---:|---:|---:|---:|---:|
| Default | global | 1.8 | 19.8 | 35.1 | 61.1 | 1.09 |
| Default | 2025 | 2.1 | 23.7 | 31.8 | 57.0 | 1.12 |
| Expenditure counted in 3, 4, x1 | global | 1.8 | 14.1 | 33.9 | 62.1 | 1.09 |
| Expenditure counted in 3, 4, x1 | 2025 | 2.1 | 14.4 | 31.6 | 57.3 | 1.12 |
| Related-activity links, other publishers | global | 2.1 | – | – | – | – |
| Related-activity links, other publishers | 2025 | 3.0 | – | – | – | – |
| Related-activity links, all | global | 33.0 | – | – | – | – |
| Related-activity links, all | 2025 | 30.6 | – | – | – | – |
| Year by activity period | 2025 | 2.1 | 28.3 | 39.5 | 50.9 | 1.11 |
| Disbursements only (network rebuilt) | global | 1.4 | 23.4 | 36.5 | 58.7 | 1.13 |
| Disbursements only (network rebuilt) | 2025 | 1.7 | 27.9 | 33.3 | 56.0 | 1.13 |

Values are from `sensitivity.csv` of the run of 11 November 2026 (commit `5351929`), with x1 defined without the organization IDs counted in x1b. A dash means the setting does not affect that indicator, so the value is as in the default row. Related-activity links within one publisher multiply indicator 1 by about 15, as in the pilot. Expenditure lowers indicator 3 by about 6 to 9 points, because most expenditure names no receiver ID.

**Disbursements only** (`DISBURSEMENTS_ONLY`, 2 November 2026). Outgoing flows are disbursements (type 3) and incoming flows are incoming funds (type 1); the links, network and chains are rebuilt. This is the definition used in the McCarthy paper. The network shrinks from 13,024 to 9,408 links and from 14,716 to 11,131 activities; the longest chain falls from 5 to 4 steps. Indicator 3 rises by about 4 points (disbursements name a valid receiver ID more often than commitments). Two indicators not in the table above change most (global): indicator 2 from 62.8% to 61.7%, and indicator 5 from 82.7% (19,345 of 23,384 transactions) to 58.8% (5,114 of 8,694). Most outgoing transactions that name a published receiver activity are commitments, and a disbursement is less often matched by the receiver's incoming funds than the pooled flows are matched by incoming funds or incoming commitments. All values are in `sensitivity.csv`, which since this change also includes indicators 2 and 5 for every setting.

## Decided: organization IDs written as activity IDs (x1 and x1b)

343,092 of the 571,756 transactions that give an "activity ID" that is not published carry an *organization* identifier in the activity-ID field. USAID accounts for 321,090 of these: its outgoing transactions give `receiver-activity-id="US-GOV-1"`, its own organization ID. These transactions are left out of the numerator and denominator of x1 and reported separately as x1b. x1 (global) is 61.1% (228,664 of 373,990 transactions); counted with them, it would be 79.7%. x1b is 47.8% (343,092 of 717,082 transactions). Before sale of equity (type 9) became incoming (November 2026), these were 61.2% and 47.9%.
