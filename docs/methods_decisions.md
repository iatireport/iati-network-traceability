# Method decisions for the full run

This file records each methodological choice in the pipeline: what was decided, why, and its effect on the counts in the bulk copy of 30 October 2026 (index created 2026-10-30 19:57 UTC). Settings that can be switched are named after their entry in `config.py`. Use this file when writing the methods section of the network and traceability chapter.

## 0. Terms used in this file and in the outputs

These terms are not interchangeable. Each count in the report should say which unit it is in.

| Term | What it is | In the XML | Example |
|---|---|---|---|
| **Activity** | A project or program as published, identified by its `iati-identifier`. One activity holds many transactions. | `<iati-activity>` | `XM-DAC-3-1-286697-35435` (a Danida grant) |
| **Transaction** | One financial flow recorded in an activity: a commitment, disbursement, expenditure or incoming funds, with a date and a value. | `<transaction>` inside an activity | Danida disburses DKK 25 million on 2 May 2023 |
| **Link** (edge) | A pair of activities, *from* (funder) and *to* (receiver), connected because at least one transaction in one of them names the other by its activity ID. One link usually rests on several transactions. | `provider-activity-id` / `receiver-activity-id` | Danida grant → DanChurchAid project (22 transactions) |
| **Organization link** | A pair of organizations connected because a transaction names the other by its organization ID. | `provider-org ref` / `receiver-org ref` | Danida → DanChurchAid |
| **Chain** | A sequence of links from an origin activity to an end activity, followed activity by activity. Its length is counted in steps (links). | — | Danida grant → DanChurchAid project → partner project (2 steps) |
| **Origin** | An activity at the start of at least one chain: it funds others in the network but has no traced funder. | — | — |
| **Organization path** | The sequence of organizations along a chain. Many chains can share one organization path, because they pass through different activities of the same organizations. | — | Danida → DanChurchAid → partner |

**How the units nest in this copy:**

| Level | Activities | Links | Transactions |
|---|---:|---:|---:|
| All data after cleaning (stage 2) | 894,287 | — | 11,446,196 |
| Transactions naming a published counterpart activity (stage 3) | — | 42,043 | 145,464 |
| Network: plausible links between different organizations (stage 4) | 14,716 | 13,024 | 68,460 |
| Used in chains (excludes the 32 links inside cycles) | 14,698 | 12,992 | 68,074 |

So the chains are built from **68,074 transactions**, which connect **14,698 activities** through **12,992 links**. Of these transactions, 58,717 were reported by the receiver (incoming funds) and 9,357 by the funder.

The 14,698 activities in the chains hold 350,547 transactions in total, excluding exact duplicates. Most of these name no counterpart activity, so they do not contribute to any link.

## 1. Source and files (stage 1)

**Source.** The Bulk Data Service copy is the source of record (a dated copy can be rerun and checked; the IATI Datastore index changes continuously and, on 29 October 2026, briefly held two versions of some publishers' data). Of 13,989 datasets in the index, 747 have no file in the copy. They are listed in `datasets_missing.csv`.

**Unreadable files.** 9 files cannot be parsed completely. They are excluded entirely (`KEEP_PARTIAL_FILES = False`). The 286 activities and 1,812 transactions read before the error are counted in the totals but not kept.

**Files in an XML namespace** (changed after the code review, November 2026). One file (`ipmc-humanitaire-activities.xml`, 14 activities, no transactions) puts its root element in a namespace (`xmlns="http://iatistandard.org/activity-schema"`). The parser does not read such files. Until the review it passed them as "ok" with no activities; it now reports them as failed, with the root element in the error. After the next run, `files_failed` is therefore 10 (9 parse errors and this file), not 9. No transaction and no indicator changes, because the file has no transactions.

**Encoding.** One file (`un-environment-xm-dac-41116-p-007.xml`, 10 activities, 17 transactions) declares its encoding as `utf8` rather than `UTF-8`. Python's standard XML reader rejects it, and the pipeline's reader (lxml) reads it correctly. This explains why the pipeline reads 11,496,865 transactions where the earlier count gave 11,496,848, and finds 9 files with parse errors instead of 10.

**Activity defaults.** A transaction without its own currency, flow type, finance type, aid type or tied status takes the activity's default. After this, 206 transactions have no currency. Before defaults are applied, about half (51%) of transactions have no currency of their own.

**Transaction types.** Version 1 codes (C, D, E, IF, IR, LR, R, QP, QS, CG) are recoded to version 2 numbers: 31,944 transactions. Type names written as codes ("Disbursement", "Expenditure") are recoded too: 45 transactions. Unusable codes, such as dates typed into the code, are flagged as invalid and kept, but they belong to no direction: 77 transactions.

**Names.** The English `<narrative>` is used if one exists, otherwise the first narrative. Version 1 files have no narratives, so the element's own text is used.

## 2. Activities and transactions (stage 2)

**One copy per activity.** When an `iati-identifier` appears more than once, one copy is kept, chosen by these rules in order:
1. the primary reporter;
2. the latest `last-updated-datetime`;
3. the copy in the reporting organization's own publisher folder;
4. the copy with more transactions;
5. file order.

This removes 5,553 copies (48,819 transactions), and 98% of them are in the same publisher folder as the copy kept. Only 2 copies are removed for being from a secondary reporter. 20 activities without an identifier (38 transactions) are dropped.

**Exact duplicate transactions.** A transaction identical in every content field to an earlier one in the same activity is flagged (`exact_duplicate`), not deleted: 225,206 transactions. UNIDO accounts for 103,132 and Danida for 41,643. These are left out of the link tables and the indicators, as in the pilot, and reported as indicator D1. The content fields are listed in `CONTENT_COLS` (`02_clean.py`): reference, type as published, date, value, currency, value date, flow, finance, aid and tied-status types, provider and receiver refs, names and activity IDs (refs after trimming and upper-casing), and transaction countries. Organization types are not compared, so two transactions that differ only in `receiver-org type` count as duplicates.

**Identifiers.** Organization refs are trimmed and upper-cased. Activity IDs are trimmed but keep their case. Matching activity IDs regardless of case would rescue only 557 of 586,138 unpublished IDs, so exact matching is kept.

**Direction.** "In" means types 1, 9, 11 and 13 (type 9, sale of equity, since the code review of November 2026, as in the IATI guidance; 146 transactions excluding exact duplicates). "Out" means types 2–8, 10 and 12. Unlike the pilot, expenditure (type 4) counts as outgoing, so that its receivers are checked. Whether expenditure enters indicators 3, 4 and x1 is one setting (`INDICATOR3_INCLUDE_EXPENDITURE`).

**Internal flows.** A transaction whose counterpart is the reporting organization itself is internal: 2.02 million transactions, of which WHO accounts for 1.06 million. These are excluded from the link indicators, as in the pilot.

## 3. Links (stage 3)

**What makes a link.** A link between two activities exists when one of them names the other in a transaction, and the named ID is a published `iati-identifier`. Two kinds of transaction can make a link:
- a funder's commitment, disbursement or expenditure that names the receiver's activity in `receiver-activity-id`;
- a receiver's incoming funds or incoming commitment that names the funder's activity in `provider-activity-id`.

Links are not filtered by year. The years of the transactions behind each link are recorded with it.

**Confirmed and one-sided.** A link is confirmed if both sides report it, and one-sided if only one does.

**Values.** Values are summed by side, transaction type and currency. They are never added across currencies. They are also never added across types, because a commitment and the disbursement that follows it are the same money.

**Links within one organization.** These are links between two activities of the same publisher, for example a program and its own components: 28,839 of 42,043 edges (69%). They are kept in the edge table and excluded from the network (`INCLUDE_SAME_ORG_LINKS_IN_NETWORK = False`).

**Expenditure links.** These are kept, labeled `expenditure_only`, and included in the network (`INCLUDE_EXPENDITURE_LINKS_IN_NETWORK = True`). There are 162 such edges, of which 30 are between organizations.

### 3.1 Plausibility rule (`PLAUSIBILITY_RULE = "known_publishers"`, agreed 30 October 2026)

**Original definition.** A link is plausible when the publisher of the linked activity matches the organization `ref` named on that side of the transaction. Applied strictly, this rejects many true links, because publishers often write a name or a badly formed code in the `ref` field. Examples are `GB-GOV-01`, `GB-01`, `FCDO` or `DFID` for FCDO (`GB-GOV-1`), and `SIDA` for Sweden. In the GALVmed case, the strict rule rejects both the false link (GALVmed → FCDO) and the true one (FCDO → GALVmed). Of 2,380 transactions whose named ref does not match, only 526 name the identifier of another real publisher (2,374 before the code review of November 2026; see section 3.1).

**Rule used.** Each transaction behind a link is first given an organization check:

| Check | Meaning |
|---|---|
| match | The named ref is an identifier of the linked activity's publisher (its reporting-org ref, its registry identifier, or any reporting-org ref used in its folder). Also counts as a match: the linked activity ID starts with the ref, or the ref is a bare DAC code. |
| other publisher | The named ref is the identifier of a different publisher. |
| unrecognized ref | A ref is given, but it is no publisher's identifier (a name or a malformed code). |
| no org ref | No ref is given. |

A link is then labeled, in this order:

| Label | Plausible? | Edges between organizations |
|---|---|---:|
| reverses own funding link | no | 40 |
| org matches | yes | 10,312 |
| names another publisher | no | 140 |
| unrecognized org ref | yes (cannot be checked) | 380 |
| no org ref | yes (cannot be checked) | 2,332 |

**"Reverses own funding link"** is the GALVmed pattern. Activity X reports paying to activity Y, only X reports it, and X also reports receiving funds from Y. The publisher has put its funder's activity ID in `receiver-activity-id`. This check does not use organization refs. It does not apply if a named org matches.

**Effect.** 180 links between organizations are implausible under this rule. Under the strict rule (`PLAUSIBILITY_RULE = "strict"`, where unrecognized refs also count against the link) the figure is 560 (559 before the code review of November 2026: a bare `XM-DAC` or `GB` no longer counts as an organization match). Both versions are kept as columns: `plausible` and `plausible_strict`.

**Known borderline case.** 102 of the 140 "names another publisher" edges are one pattern: UNHCR names the EU's FPI directorate (`XI-IATI-EC_FPI`) as funder of activities published by the INTPA directorate (`XI-IATI-EC_INTPA`). The activity link is probably right and the directorate code wrong. These links are counted as implausible under both rules.

## 4. Network and chains (stage 4)

**Network.** The network is built from all edges with `in_network` = TRUE: plausible links between different organizations, of all years and all countries. It has 14,716 activities and 13,024 edges. Subsets (a country, a year, a publisher group) are taken afterwards, in stage 5.

**Cycles.** A cycle is a group of activities that can reach each other through links. Including implausible links, there are 49 such groups; after excluding them, 14 remain. All 14 are 2–3 activities, and they are listed in `cycles.csv`. The pilot merged each loop into a single node. Instead, the 32 edges inside the 14 remaining cycles are set aside, and depth is measured on the network without them.

**Chain.** A chain is a path from an origin to an end:
- an origin is an activity with no traced funder in the network;
- an end is an activity with no traced recipient.

There are 12,751 chains from 3,798 origins. One origin with many recipients gives many chains: a single FCDO activity (`GB-1-202669`) is the origin of 2,206 one-step chains.

**Weakest evidence.** A chain is "confirmed" if every step is confirmed by both sides, and "one-sided" otherwise. Implausible links are not in the network, so no chain contains one; implausible links are reported from the edge table.

**Depth: three units.** Depth is reported in three units (`depth_distribution.csv`), because they answer different questions:

| Unit | Counts | Question it answers | Weakness |
|---|---|---|---|
| Per chain | each of the 12,751 chains | How long are the traceable chains? | Weighted towards funders that split money into many activities. The 10 largest origins produce 38% of chains; one FCDO activity (`GB-1-202669`) alone produces 2,206. |
| Per origin | each of the 3,798 origins, by its longest chain | How far can funding be followed from a starting activity? This is the pilot's indicator 6. | Hides the other chains from the same origin. |
| Per organization path | each of the 1,785 distinct sequences of organizations | Which multi-step funding routes between organizations are traceable at all? | Gives one large and one small route equal weight. |

| Steps | Chains | % | Origins | % | Organization paths | % |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 10,980 | 86.1 | 3,530 | 92.9 | 900 | 50.4 |
| 2 | 1,371 | 10.8 | 215 | 5.7 | 685 | 38.4 |
| 3 | 369 | 2.9 | 44 | 1.2 | 181 | 10.1 |
| 4+ | 31 | 0.2 | 9 | 0.2 | 19 | 1.1 |

Counted by organization path, half of the traceable routes have two or more steps. Counted per chain or per origin, the large majority have one. The difference comes from a few funders whose many one-step links to many activities dominate the chain counts.

An organization path's `best_evidence` is "confirmed" if at least one chain on it is confirmed at every step. This holds for 62 of 1,785 paths. The paths are listed in `org_paths.csv`.

**Robustness check: links within one organization.** Same-organization links are excluded from the network, which could cut a chain that passes through a publisher's internal hierarchy (for example, NGO parent activity → its own sub-activity → partner). As a test, chains were allowed to pass through same-organization links without counting them as steps. The network then grows from 14,716 to 40,815 activities, but depth hardly changes: per origin, the mean is 1.09 steps and the maximum 5, with 3,242 / 213 / 43 / 9 origins at 1 / 2 / 3 / 4+ steps. The exclusion therefore does not shorten measured chains.

**Why a chain stops.** This is assessed for each activity that receives funds (incoming transactions, or an incoming link in the network) but has no onward link in the network. The reason is the best evidence its outgoing commitments, disbursements and expenditure give, checked in this order:
1. an onward link exists, but only to its own organization or an implausible one;
2. an activity ID given but not published;
3. an organization ID given in the activity-ID field (as in indicator x1b);
4. a receiver organization ID only;
5. a receiver name only;
6. outgoing only to itself;
7. no receiver named;
8. no outgoing transactions.

A chain can also end at an activity whose only onward links lie inside a cycle and were set aside (see "Cycles" above). Such an activity has an onward link in the network, so it is not in the list above; its chains get the reason "onward link set aside (inside a cycle)" (3 chains, all ending at `GB-CHC-209131-A03172`; added after the code review, November 2026).

**Years of a chain.** Chains are not filtered by year: a chain can join a 2013 commitment to a 2019 disbursement. Each chain records the first and last year of the transactions behind its links. 1,975 chains begin before 2015, and 4,629 run until 2023 or later. Year subsets are applied in stage 5, not here.

**Remaining cycles.** The 14 cycles left after excluding implausible links look like genuine two-way flows, for example consortium partners funding each other, or refunds. One pair (GB-CHC-209131 ↔ GB-COH-03580586) is confirmed in both directions. They are set aside for depth and listed for an appendix.

**Countries of a chain.** The recipient countries of all activities along the chain. An activity's countries come from activity level, or from its transactions if the activity names none. Country codes are not validated; for example `DRC` occurs alongside `CD`.

## 5. Open questions for the IATI Secretariat (settings in `config.py`)

| Question | Setting | Default |
|---|---|---|
| Count expenditure in indicators 3, 4 and x1? | `INDICATOR3_INCLUDE_EXPENDITURE` | no |
| Count related-activity links in indicator 1? | `INDICATOR1_RELATED_ACTIVITIES` | none |
| Define a subset year by transaction date? If no, by the activity's start–end period. | `YEAR_BY_TRANSACTION_DATE` | yes |
| Count disbursements only (type 3 out, type 1 in) instead of pooling commitments and disbursements? Applies to links, network and indicators. | `DISBURSEMENTS_ONLY` | no |

## 6. Future work

**Disbursements only.** The McCarthy paper uses disbursements only (type 3). This pipeline pools commitments and disbursements (types 2 and 3) as outgoing flows, and incoming funds and incoming commitments (types 1 and 11) as incoming flows. Pooling counts a grant committed once and paid in four instalments as five transactions, and gives more weight to publishers that report many small disbursements. A disbursements-only version (with incoming funds, type 1, on the receiving side) would be closer to the paper and to money that actually moved, but would drop publishers that report only commitments. It can be run as a sensitivity check without re-parsing, because every transaction keeps its type.

Implemented as a sensitivity check (2 November 2026): setting `DISBURSEMENTS_ONLY` in `config.py`. When it is on, outgoing flows are disbursements (type 3) and incoming flows are incoming funds (type 1) in stages 3, 4 and 5; commitments and expenditure links are left out, and indicators 4 and x1 are limited to those two types. Because the links change, `report/sensitivity.py` reruns stages 3–5 in `outputs/<bulk>/sensitivity_disbursements_only/`, which points to the stage 2 files; the default outputs are not touched. Results are in `sensitivity.csv`, `docs/indicators.md` and the report. Whether to adopt it as the default is open.

## 7. Spatial view (stage 6, `06_spatial.py`; added 2 November 2026)

Stage 6 places activities and transactions in recipient countries and computes indicators 1 (unit: activities) and 3 (unit: transactions) per country, for the maps in the report. It reads only the columns it needs from stages 2–3 and uses the stage 5 definitions (`connected_activities()`, `indicator3_flags()`), so its numbers match stage 5's country subsets: for Uganda 2025, indicator 1 is 157 of 2,563 activities and indicator 3 is 2,128 of 8,845 transactions in both. Run time is about 2.5 minutes; peak memory about 3.5 GB.

**Which country.** (Decision 2 November 2026, as proposed in stage 0.)
- An activity's countries are its activity-level recipient countries. If it names none, they are the countries named on its transactions in the scope. This is the rule of stage 5's `in_scope()` and stage 4's `activity_countries()`.
- A transaction's countries are its activity's activity-level countries; if the activity names none, the transaction's own. Activity level comes first because the two rarely differ: of the 8,692 transactions that give a country inside an activity that also gives one, 130 (in 25 activities) name a country outside the activity's list.
- Of the 894,287 activities, 466,890 are placed by activity-level countries, 211,668 by transaction-level countries only, and 215,729 by neither (these include the activities with no transactions).

**Country codes.** Only codes of two capital letters (`COUNTRY_CODE_PATTERN`) are placed on the map. Other codes in the country field (`GLOBAL`, region numbers such as `998`, three-letter codes such as `HTI`; 1,272 entries) are dropped from the lists and reported in `spatial_unplaced.csv`. For 1,181 activities with transactions such a code is the only country given.

**Multi-country activities** (`MULTI_COUNTRY_RULE`; all three rules are always computed). 14,523 activities name two or more countries; 14,396 give percentages.

| Rule | Meaning | Countries with base ≥ 30, ind. 1 / ind. 3 | Median across those countries, ind. 1 / ind. 3 |
|---|---|---|---|
| `every` (default) | counted in full in every listed country, as in stage 5's subsets | 208 / 216 | 1.05% / 21.1% |
| `single` | only records naming one country | 200 / 209 | 0.60% / 19.0% |
| `split` | split by the published percentages (equal shares if missing or unusable) | 201 / 210 | 0.60% / 19.2% |

`every` is the default because it matches stage 5 and the counts stay whole numbers. It raises the shares most for donor countries listed in large multi-country activities. For indicator 3 (global), Norway is 55.4% under `every` and 14.0% under `split`, and Germany 50.6% against 11.5%. For the large recipient countries the rules differ little: Uganda, indicator 1, is 4.0% under `every`. The report shows the rule comparison beside the map. The choice is open.

**What cannot be placed** (global scope; `spatial_unplaced.csv`). Of 794,616 activities with transactions, 615,166 are placed in at least one country. The other 179,450 are:
- 87,699 with no country or region;
- 1,181 with a non-standard country code only;
- about 90,570 with a region only. This number is derived by subtraction; the table counts an activity once per region it names, giving 98,359 rows. The largest region is 998, "developing countries, unspecified" (56,894 activities, of which 1,052 are connected).

Region-only activities are not drawn on the country maps and are reported in a table beside them.

**Network links by placement** (`spatial_links_unplaced.csv`; unit: links; all 13,024 network links):

| End | One country | Several countries | Region only | Nothing |
|---|---:|---:|---:|---:|
| Funder activity | 4,458 | 1,748 | 6,311 | 507 |
| Receiver activity | 10,618 | 763 | 1,577 | 66 |

Funder activities are usually multi-country or regional programs, so the funder side of a flow map needs an organization country (option B), not the activity's recipient country.

**Small bases.** As elsewhere, a denominator below 30 sets `small_base`; the maps gray these countries out and give counts instead of shares.

### 7.1 Part D: locations and example chains (2 November 2026)

**Reading locations.** Stage 6 reads the `<location>` elements that are direct children of `<iati-activity>`, for the activity copy kept in stage 2 (matched by `copy_id`, with a check that the identifier agrees). Files without "<location" are skipped. The read takes about 1 minute and is kept in `locations.parquet`; it is repeated only after stage 2 is rerun. Version 2 points (`<point><pos>`) and version 1 points (`<coordinates>`) are both read. `<location ref>` elements inside results and indicators only refer back to the activity's locations and are not read. The stage 0 estimate of 1.54 million `<location>` elements counted these too; the activity counts in it were right.

| Measure (bulk copy of 30 October 2026) | Count | Unit |
|---|---:|---|
| Locations of kept activities | 649,578 | locations |
| ...with a valid point | 549,961 | locations |
| ...with no point | 97,847 | locations |
| ...unreadable (mostly `. .` and `NA NA`) | 1,479 | locations |
| ...out of range or 0,0 | 291 | locations |
| Activities with a location / a valid point / a usable point | 255,297 / 195,195 / 178,567 | activities |
| Network activities with a valid point | 2,954 of 14,716 | activities |

**Reading `<pos>`.** "lat lon" with a space, "lat,lon" and "lat, lon" are all read; a decimal comma is read as a point when the two numbers are separated by a space. Reading "lat, lon" correctly added 2,748 valid points to a first version.

**Point check** (`POINT_COUNTRY_TOLERANCE_DEG`, `COUNTRY_BORDERS`). A valid point is tested against the activity's recipient countries (as placed in part A), using the Natural Earth 1:50m borders widened by 0.25 degrees (about 25 km) for coasts and borders. The borders are exported once from R (`reference/make_country_borders.R`), so the check and the maps use the same borders. The test uses the Python package shapely (added 2 November 2026). 486,303 valid points lie inside a recipient country and 17,289 outside all of them; the rest belong to activities without a country. 25,195 points are coded as a whole country (GeoNames `PCL…`). *Usable* points (valid, inside, not country-level): 483,024. Why: the first draft of the examples included an end point in the Atlantic Ocean for a Colombian project, a point in Brussels, a point at latitude 0, and country centroids.

**Organization country** (`INTERNATIONAL_PREFIXES`). For the maps only, each organization is placed at its country of registration: the identifier's two-letter country prefix; for `XM`/`XI`/`XR` identifiers and identifiers without a country prefix, the IATI Registry headquarters country if the organization is a publisher. Of the 33,763 organizations in `org_edges`, 10,590 are placed by prefix, 127 by the Registry and 23,046 not at all (mostly names or legacy codes written as refs). This is where an organization is registered, not where it works. Agreed with the mockup of 2 November 2026; it is also the rule for part B.

**Choice of examples** (`SHOWCASE_MIN_STEPS`, `SHOWCASE_N`, `COUNTEREXAMPLE_N`, `LOCAL_ORG_TYPES`). Chains of 2+ steps are ranked: local end publisher first (types 11, 22, 23, 24, 72), then more steps confirmed by both sides, then more steps, then more activities with usable points. One chain per organization path, per end publisher, and per route before the end publisher, so the examples show different routes (without this, the top five repeated two routes). A chain that ends at an organization it already passed through is excluded (it removed VLIR-UOS → ARES → VLIR-UOS, with an end point in Brussels). The five examples are the best-ranked chains whose end activity has a usable point; the counter-example is the best-ranked chain whose end activity has none.

| Chains of 2+ steps (1,771), by their end activity | Chains |
|---|---:|
| Usable point | 176 (17 at a local organization) |
| No location | 1,434 |
| No valid point | 126 |
| Point outside the recipient country | 33 |
| Country-level point only | 2 |

28 chains are ranked as candidates. The examples are FCDO → MannionDanniels → Orbis UK → Orbis International → Orbis Ethiopia; FCDO → MannionDanniels → EMMS International → David Gordon Memorial Hospital (Malawi); Netherlands MFA → Womankind Worldwide → Siiqqee Women's Development Association (Ethiopia); FCDO → Girls' Education Challenge (PwC) → Education Development Trust → Kesho Kenya; Girls' Education Challenge (PwC) → Theatre for a Change UK → Theatre for a Change Malawi. The counter-example is FCDO → MannionDanniels → Orbis UK → Orbis International → Nepal Netra Jyoti Sangh, whose activity publishes no location.

**Caveats.** A location belongs to an activity, not to an organization. `exactness` is often missing (88,601 valid points); the figures draw a point as exact only if it says so. The examples are chosen, not sampled: they show what traceability to a mapped local organization looks like, not how common it is.

### 7.2 Part B: flows between countries (2 November 2026)

**What is counted.** The 13,024 network links (unit: links; plausible links between activities of different organizations, all years), by the country of registration of the funding organization and the recipient country of the receiving activity (`country_flows.csv`). Links, not money: a link rests on one or more transactions, and values are never added across currencies.

**Funder country.** The rule of part D: the identifier's country prefix, else the Registry headquarters country. Change made while building B: the `XM-` prefix does not always mean an international body. Several governments publish under their OECD-DAC donor code (`XM-DAC-3` Denmark, `XM-DAC-7` the Netherlands, `XM-DAC-302` United States). `XM-DAC-` codes below 900 (`DAC_INTERNATIONAL_FROM`) are therefore national. From 900 they are not governments: multilateral donors (`XM-DAC-903` IFC, `XM-DAC-928` WHO), private donors (`XM-DAC-1601` Gates Foundation), and DAC channel codes of organizations, which include multilaterals (`XM-DAC-41122` UNICEF, `XM-DAC-46002` AfDB), international NGOs (`XM-DAC-21016` ICRC) and partnerships (`XM-DAC-30010` Unitaid). Without this, 3,258 links funded by the Netherlands MFA counted as international. The `international` column of `geo_chain_orgs.csv` follows the same rule; the part D figures do not change.

*Change after the code review (November 2026).* The threshold was 40000, which treated 106 refs numbered 900 to 39999 as national governments. In the network only one link is affected: Unitaid (`XM-DAC-30010`) to an activity in several countries, now counted as funded by an international body. No drawn link changes.

**International bodies are not drawn.** 1,808 links are funded by international bodies (1,807 before the change above), 1,268 of them with no country in the Registry (mostly the EU's directorates, `XI-IATI-EC_…`, and UN pooled funds). A headquarters says little about where their money comes from, so they are counted in the tables but not drawn on the map. 1 further link has a national funder with no known country.

**Receiving side.** The receiving activity's recipient country, placed as in part A, if it names exactly one; 10,618 links. The others go to activities naming several countries (763), only a region (1,577) or nothing (66).

| Measure | Links |
|---|---:|
| All network links | 13,024 |
| To an activity in one country | 10,618 |
| Domestic (funder registered in the recipient country), all funders | 385 |
| Cross-border, all funders with a known country | 9,058 |
| Drawn: national funders, one recipient country | 8,921 (381 domestic, 8,540 cross-border; 578 country pairs) |
| ...in the 40 largest cross-border pairs (lines on the map) | 5,190 |
| Received by an organization registered in the recipient country | 803 (7.6% of links to one country) |

Two funder countries dominate the drawn cross-border links: the United Kingdom and the Netherlands. The largest pairs are United Kingdom → DR Congo (324 links), United Kingdom → Kenya (292) and United Kingdom → Zimbabwe (253). Among recipient countries with 30 or more links, the share received by an organization registered there is highest in donor countries (the Netherlands 100%, its own ministry funding Dutch organizations recorded as domestic) and below 12% in every partner country shown.

## 8. Alignment check and the chapter draft (3 November 2026)

The pipeline was compared with the IATI traceability guidance and McCarthy et al. (2026). The tables are in `docs/method_alignment.md`. No pipeline code or output changed. The points below are in the chapter draft only (the draft is not in this repository), or are open for decision.

**UNICEF partner codes (decided November 2026, code review M9).** 11,240 of the 33,763 identifiers in `org_edges` have the form `XM-DAC-41122-VN-…`. They are well formed but are UNICEF's own codes for its partners. They are flagged (`partner_code` in `organisations.parquet`, `config.PARTNER_CODE_PATTERN`) and left out of the organization network in stage 4, in `full_run.qmd` and in the chapter draft alike: 22,523 organizations and 28,270 organization links, with 99.3% of organizations in one connected group (with them: 33,763 organizations, 39,510 links, 99.6%). They stay in `org_edges`, and the draft's comparison table with McCarthy et al. merges them into UNICEF, as McCarthy et al. do. They publish no activities, so links, chains and the indicators built on activity links do not change. The 1,699 organization links that can also be followed through activity links do not change; as a share of all organization links, they rise from 4.3% to 6.0%.

**Merging suffixed identifiers (draft only).** The draft's comparison table merges a ref into a shorter ref if the shorter one also occurs, following McCarthy et al., and never into a registration agency prefix such as `XM-DAC`. Under the paper's definition this gives 15,018 organizations and 17,588 links (paper: 22,773 and 28,708).

**Reach of organizations (draft only).** Harmonic centrality on the organization network, outgoing direction, well-formed identifiers only. McCarthy et al.'s adjustment against randomized networks is not applied.

**Gaps against the guidance (open).** `participating-org` is not read: about 217,000 elements across all copies carry an activity ID (a text search of the bulk copy, so approximate). How many new links it would add has not been measured. Planned disbursements (about 435,000 elements) and Organization Files are also not read. Sale of equity (type 9, 150 transactions) has no direction, while the guidance treats it as incoming.

**Options for the flow figure (open; 3 November 2026).** Why: in the current map, all of the 40 largest country pairs have a funder registered in the United Kingdom or the Netherlands, so other funders and most of South America drop out. Four further versions are in section 11 of `report/full_run.qmd` and in the chapter draft: A, small maps by funder group; B, ribbons; C, matrix (10 funders, 28 recipients); D, shortest-path lines for pairs with 10 or more links (128 pairs, 7,382 links). A and B use all 8,540 cross-border links from national funders. The counts and `country_flows.csv` do not change. The choice is open.

## 9. Working paper additions (November 2026)

Values added to the stages for the working paper figures. The working paper and its report files are kept outside this repository; this section records only what changed in the pipeline. Each is documented in `docs/indicators.md` or `docs/data_dictionary.md` and has a test.

**D2g, traced transactions by publisher group (stage 5).** For each scope and publisher group (IATI organization type of the reporting organization), the traced transactions that the group's publishers report, out of all traced transactions in the scope. "Traced" is defined as for D2: the transaction names a published counterpart activity, is not internal and does not name its own activity; exact duplicates are left out. The group shares add up to 100%. D2 itself (the top-ten concentration) is unchanged.

**Network multipliers (stage 4, `multipliers.csv`).** An intermediary is an organization that is neither first nor last on an organization path. One row per organization identifier. Decision (16 November 2026): every count uses only the paths on which the organization is an intermediary, each path once, and never counts the organization as its own partner. Paths through: those paths; direct recipients: distinct organizations right after it; downstream and upstream: distinct organizations after and before it. The first working paper draft (Table 7) used a mixed definition: direct and downstream counts also included paths where the organization was the first funder, a path passing through an organization twice counted twice, and organizations were keyed by name. Under the new definition, for example, the Girls' Education Challenge fund manager has 22 direct recipients and 45 downstream organizations (draft: 25 and 53). Keyed by identifier, ICF Consulting Services appears twice (GB-CH-4161656 and GB-COH-04161656), so there are 143 intermediaries rather than 142.

**Country of registration of every organization (stage 6, `org_countries.csv`).** Stage 6 already computed it for the maps; it now writes the table, so the multiplier table and chain diagrams use the same rule.

**Transaction indicators by reporting organization (stage 5, `indicators_by_publisher.csv`).** Indicators 2 to 5, x1 and x1b for each reporting organization and scope, from the same transaction flags as `indicators.csv`. Used to break down changes between years (for example indicator 4 in 2025) and to compare the publisher-level distribution of indicator 3 with McCarthy et al. (2026).

**Why chains stop, by year (stage 4, `chain_end_reasons_by_year.csv`).** The same eight reasons for the activities that have at least one transaction dated in 2023, 2024 or 2025, as stage 5 selects activities for a year. The reason itself is still judged on all of the activity's outgoing transactions, of all years.

**Chain completeness score (stage 4, `completeness` in `chains.csv`).** For every chain, one point per step for each of: (1) a transaction reported by the funder names a well-formed receiver organization identifier; (2) the funder's transaction names the receiver's activity identifier, which resolves to a published activity (the funder reports the link); (3) the receiver confirms the link with an incoming transaction that names the funder's activity. The score is the total divided by three times the number of steps (0 to 1). The per-step points are in `chain_steps.csv`. The formula was proposed for this working paper; it refers to the fields that EyeOpenerWorks and Open Works (2020) traced by hand in six Uganda chains (organization identifier, activity identifier, and reporting by both funder and receiver), but the score itself is not taken from that study. Points (2) and (3) together are the pipeline's "confirmed" evidence; point (1) adds identifier quality on the funder's side.

**Indicator 6f, tentative (stage 5).** The mean chain completeness score of the chains passing through a scope, by origin publisher group, reported like indicator 6. Added at the user's request (17 November 2026) **tentatively**: the IATI Data Quality Dashboard already has a different "completeness" score, and the paper should avoid duplicating measures or adding indicators that do not follow existing methodologies. It may be renamed (for example to avoid the word "completeness") or removed before publication; removing it means deleting the 6f row in `05_indicators.py`, its test, and the working paper references.

**Flows between countries, by year (stage 6, `country_flows_by_year.csv`).** The links of `country_flows.csv` that have at least one link transaction dated in 2023, 2024 or 2025, counted in each such year (a link can count in several years). Same placement rules as the all-years table.

**Steps from the first funder to local organizations (stage 6, `local_links_steps.csv`).** For each link received by a local organization (registered in the recipient country), the position of the link in the chains that pass through it: 1 if the chain's first funder funds the local organization directly, 2 if through one intermediary, and so on; the fewest over all chains. "Direct" means direct from the first funder visible in IATI: that funder may itself have been funded by an organization that does not publish. Links that lie only inside cycles are in no chain and have no step count.

**Indicator 3 by publisher (stage 5, `indicator3_by_publisher.csv`).** Indicator 3 for each reporting organization under the default and under disbursements only. Indicator 3 depends only on the transaction's type, whether it is internal and whether its receiver identifier is well formed, so disbursements only is the same rule restricted to type 3; it does not need the rebuilt network of the disbursements-only sensitivity run. McCarthy et al. (2026) report a median funder identification rate of 7.7% and 46.3% of funders identifying no receiver; theirs are value-based and after name matching, ours are counts of transactions before any name matching.

**Correction (17 November 2026).** `indicators_by_publisher.csv` left out the transactions of activities without a reporting-org identifier (for example 648 of 5,210,271 indicator 3 transactions), so its rows did not quite add up to `indicators.csv`. They are now kept as one row with an empty identifier, and stage 5 checks that the rows add up. The breakdown of indicator 4 is regenerated from the corrected table.
