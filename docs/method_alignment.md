This annex compares the pipeline with two sources: the IATI traceability guidance (read 3 November 2026) and McCarthy et al. (2026; arXiv 2512.17243, version 3). Counts are from the bulk copy of 30 October 2026. Status terms: **Aligned**, the same rule. **Extended**, the same rule with an added check. **Differs**, a different rule. **Not used**, the pipeline does not read this element.

### Comparison with the IATI traceability guidance

The guidance tells publishers what to publish. It does not define a measure of traceability. So chains, depth and the indicators are the pipeline's own, built on the fields the guidance names.

| Guidance | What the pipeline does | Status | Effect (unit) |
|:-------------|:----------------|:---|:-----------|
| Follow funds from donors through intermediaries to implementers, by linking activity identifiers | Links and chains are built from activity IDs in transactions | Aligned | 13,024 links; 12,751 chains |
| Organization identifier in `provider-org` and `receiver-org` `ref` | Read, trimmed and upper-cased; checked against the registration agency codelist (indicators 2, 3). A publisher's own identifier with a prefix outside the codelist, such as USAID's `US-GOV-1`, is reported separately (indicators 2b, 3b) | Extended | Variant IDs of one organization are not merged. UNICEF's 11,240 partner codes are left out of the organization network |
| Activity identifier: organization identifier plus a suffix | Named IDs must match a published `iati-identifier` exactly | Aligned | Ignoring case would rescue 557 of 586,138 unpublished IDs (IDs) |
| `provider-activity-id` and `receiver-activity-id` on transactions | Both are used. Either side can make a link; both sides make it "confirmed" | Extended | 68,460 transactions in the network |
| Incoming types 1, 11, 13: the external organization is the provider | Same. Types 1 and 11 can make links; type 13 cannot | Aligned | Type 13: 82 transactions |
| Outgoing types 2, 3, 5, 6, 7, 8, 10, 12: the external organization is the receiver | Same. Types 2 and 3 make links, and type 4 if it names an activity | Aligned | |
| Expenditure (type 4): no external organization expected | Treated as outgoing. Kept out of indicators 3, 4 and x1 by default. Expenditure-only links are kept and labeled | Differs, small | 30 links between organizations; 1,177,906 of 3,328,930 expenditure transactions name a receiver ID |
| Sale of equity (type 9): incoming | Same: incoming, with the provider as counterpart. Type 9 does not make links | Aligned | 150 transactions |
| If `provider-org` or `receiver-org` is absent, the reporting organization is presumed | Used for the publisher's own side of the flow. A missing counterpart stays missing and is not counted as internal | Aligned | 135,729 incoming transactions name no provider (transactions) |
| Provider and receiver usually differ; internal flows are an exception | Flagged as internal and left out of link indicators | Aligned | 2.02 million transactions |
| If the activity ID is unknown, give the organization ID and name; if no ID exists, give name and type | "Why a chain stops" reports this ladder directly | Aligned | Figure on why chains stop |
| Name the provider as it publishes itself | The plausibility rule accepts a publisher's known refs. Refs naming a different publisher count against a link | Extended | 180 implausible links |
| `participating-org`, with role, identifier and activity ID | **Not read** | Not used | About 217,000 elements carry an activity ID (elements, all copies). How many new links this would add is unknown |
| `related-activity` for parent and child activities | Read. Not counted as a funding link by default; tested as a setting | Aligned | Indicator 1 is 2.1% with other publishers' related activities, 33.0% with all |
| Planned disbursements should carry the same elements | Not read. They are plans, not flows | Not used | About 435,000 elements |
| Organization File: annual budget for each receiver | Organization files are skipped | Not used | No effect on links |
| Publish commitments and disaggregated transactions | Commitments and disbursements pooled by default; disbursements only as a sensitivity check. Disaggregation is not measured | Extended | 13,024 links fall to 9,408 under disbursements only |

**Additions beyond the guidance.** These are measurement choices, not conflicts: the plausibility rule, excluding links within one organization (28,839 of 42,043 links), setting cycles aside (32 links), removing duplicate activity copies and exact duplicate transactions, and chain depth in three units.

**Conclusion.** The pipeline follows the guidance on identifiers, direction and internal flows. There is one small difference (type 4, expenditure). The one gap that could change results is `participating-org` activity IDs.

### Comparison with McCarthy et al. (2026)

| Topic | McCarthy et al. | This pipeline | Status |
|:--------|:-------------|:-------------|:---|
| Question | Shape of the whole system: reach, hierarchy, resilience | How far a flow can be followed | Complementary |
| Source | IATI Datastore extract, received 8 September 2026 | Bulk Data Service copy, 30 October 2026 | Differs |
| Node | Organization | Activity for chains; organization as well (22,523, without UNICEF's partner codes) | Extended |
| Link | Organization x paid organization y at least once | A transaction names a published activity of another organization. An organization network is also built | Extended |
| Transaction types | Type 3 only | Types 1, 2, 3, 11, with type 4 labeled. Type 1 and 3 only is computed as a sensitivity check | Differs; check available |
| Who reports the link | The payer; incoming funds are named as future work | Payer and receiver; both sides mark a link "confirmed" | Extended |
| Missing provider | Reporting organization is used | Same | Aligned |
| Identifier cleaning | Upper-cased, suffixes collapsed, malformed prefixes repaired | Trimmed and upper-cased only | Differs; affects organization counts, not chains |
| Names matched to identifiers | Yes: 269,090 rows, at least 3 observations and 90% agreement | No; name-only receivers are reported as a chain-end reason | Differs |
| Duplicates | Exact duplicate rows removed | One copy per activity; exact duplicate transactions left out | Aligned |
| Secondary reporters | AidData removed: 17,715 rows | No publisher removed; the primary reporter's copy is kept. AidData has 22,739 activities here and no activity links | Differs, no effect on chains |
| Self-loops | Removed | Internal transactions left out; links within one organization are out of the network | Aligned |
| Missing receiver | Kept in per-funder sinks, to measure value without a receiver | Counted in indicators 3 and 4 and in chain ends; no sinks | Differs in form |
| Time | 1967 to 2025, pooled | All years pooled for the network; subsets for 2023 to 2025 | Aligned |
| Value | Fixed-rate USD; paths are unweighted | Not converted; never summed across currencies | Differs |
| Funder country | Identifier prefix or DAC code; multilaterals excluded | Same in substance; international bodies counted but not drawn | Aligned |
| Recipient country | Transaction, then activity | Activity, then transaction. The two rarely differ (130 of 8,692 transactions) | Differs, small |
| Measures | Harmonic centrality, distance, components, percolation, null models | Chain depth, confirmation, plausibility, indicators; organization components | Complementary |

**Reproduction.** The pipeline was limited to the paper's definition: disbursements reported by the funder, with suffixed identifiers merged by the paper's rule. It finds 15,018 organizations and 17,588 links, against 22,773 and 28,708. The share of ordered pairs connected is 0.77% against 1.11%, mean distance 5.34 against 6.00 steps, the longest path 13 steps in both, and the largest connected group 96.4% against 98.6%. So the shape agrees and the size does not. The paper's name matching (269,090 rows) is the most likely reason for the larger size. This has not been tested separately. Without merging identifiers the pipeline shows 28,917 links, but that closeness to 28,708 is largely a coincidence, because about 11,000 of those links go to UNICEF's partner codes.

**UNICEF partner codes.** 11,240 of the 33,763 identifiers in the organization links have the form `XM-DAC-41122-VN-…`. They are UNICEF's own codes for its partners, and 737 of them carry the name "IP not published". The paper's rule merges them into UNICEF. This chapter leaves them out of the organization network: it has 22,523 organizations, and 99.3% are in one connected group (99.6% with them). The chain and link counts are not affected, because these codes publish no activities.

**Conclusion.** The two are consistent where their definitions match, and they answer different questions. The paper is ahead on identifier cleaning, name matching and value. The pipeline is ahead on activity-level chains, receiver reports, link confirmation, plausibility checks and locations.
