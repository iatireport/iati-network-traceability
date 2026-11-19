# Code review of the traceability pipeline (November 2026)

*The chapter draft and the working-paper report files named below (for example `report/chapter6_draft.qmd` and `report/figures.R`) are kept outside this repository. Commit hashes refer to this repository's history.*

**Status (18 November 2026).** The review below was done on 11 November 2026, before any fix. The fixes were committed in `5351929`, and the whole pipeline was then rerun from that commit with `run_all.py` (11 November 2026). All published numbers come from the code after the fixes. The table gives the outcome of each finding; the effect of the method decisions on the counts is in `docs/methods_decisions.md`. The line numbers in section 2 refer to commit `a6d6509`, the state that was reviewed.

| Finding | Outcome | What was done |
|---|---|---|
| M1 | Fixed | The manifest records the code version and which code made each stage's outputs; full rerun with `run_all.py` |
| M2 | Fixed | Indicator docs brought up to date with the outputs |
| M3 | Decided | The codelist rule stays for indicators 2 and 3; publishers' own identifiers outside the codelist are reported as new indicators 2b and 3b |
| M4 | Fixed | Stages 5 and 6 share one country rule (`common.valid_countries`, `common.transaction_countries`) |
| M5 | Fixed | `report/sensitivity.py` always restores the default outputs (test added) |
| M6 | Fixed | A file with a namespaced root element is reported as failed (test added); unreadable files 9 → 10 |
| M7 | Fixed | Numbers in the report text are computed at render |
| M8 | Fixed | The report's description of international identifiers corrected |
| M9 | Decided | UNICEF partner codes are left out of the organization network (`config.PARTNER_CODE_PATTERN`): 33,763 → 22,523 organizations |
| M10 | Partly done | Tests T1–T9, T11 and T18–T20 added; T10 and T12–T17 not yet written |
| m1 | Fixed | Comment on `MAX_CHAINS` says which measures depend on it |
| m2 | Fixed | The docs list every reason why a chain stops |
| m3 | Decided | Interest and loan repayments stay in indicator 4; docs updated |
| m4 | Fixed | "NA" (Namibia) is no longer read as missing |
| m5 | Fixed | A link with a missing reporting-org ref compares publisher folders |
| m6 | Fixed | A bare agency prefix or country code is not an organization match (strict rule: 559 → 560 implausible links) |
| m7 | Fixed | An edge whose values are all unreadable shows NA, not 0 |
| m8 | Fixed | The 3 chains that end inside a cycle get an end reason |
| m9 | Fixed | Duplicate codes removed from the map lookup |
| m10 | Fixed | `XM-DAC` codes from 900 are not national governments (1,807 → 1,808 links funded by international bodies) |
| m11 | Fixed | Sale of equity (type 9) is incoming, as in the guidance (x1 61.2% → 61.1%) |
| m12 | Fixed | README corrected |
| m13 | Partly done | Stage 1 checks that no file reaches the `copy_id` limit; `locations.parquet` is still reused by comparing file times |
| m14 | Fixed | Duplicate rule ("identical in every field") documented precisely |
| m15 | Open | A decimal comma (`1.234,56`) is still misread |
| m16 | Partly done | Reused object names made unique and examples picked by kind; shared figure code not moved |
| m17 | Fixed | Indicator 7b documented |
| m18 | Fixed | Ruff targets Python 3.15; R packages listed |
| s1 | Open | Stage 4's `run()` and stage 6 not split |
| s2 | Open | Four copies of `load_stage()` remain |
| s3 | Fixed | Dead code removed |
| s4 | Fixed | Example chain JSON writes numbers as numbers |

Percentages in the report are shown with one decimal (commit `0560ad2`).

Review of commit `a6d6509` (branch `master`) and the outputs in `outputs/bulk_2026-10-30/`. Read only: no project file was changed except this one, no stage was rerun, and no report was rendered. Checks on the outputs were run from scratch scripts outside the project.

- Tests: `.venv/bin/python -m pytest`: 29 passed (7 s).
- Lint: `ruff check .`: all checks passed. `ruff format --check .`: 22 files already formatted.
- Every finding below was checked against the code and, where possible, against the outputs. Anything not checked is marked **unverified**.

## 1. Overall assessment

The pipeline is well built. Each stage is short to describe and has a clear input and output. The rules sit in `config.py` and in named functions. Joins are mostly guarded by assertions, and the tests check hand-worked answers. I found **no error that makes a currently published number wrong**. The headline numbers I recomputed from the outputs agree with `indicators.csv`. For example, indicator 1 is 14,353 of 794,616 activities (1.8%), x1 is 228,664 of 373,853 transactions (61.2%), and indicator 6 is 1.09 steps over 3,798 origins.

Three things must be done before the numbers are published.

1. **Rerun the whole pipeline once, from a committed state, with `run_all.py`** (M1). The current `run_manifest.json` dates from 30 October. It lists stages 1 to 5 only and records no code version. Stages 5 and 6 were rerun separately on 2 November. The outputs cannot be tied to one commit and one set of settings. I compared the code of stages 1 to 4 with their state at the time of that run, and the changes have no effect under the default settings. So a rerun should reproduce the numbers, but this has not been shown.
2. **Fix or disclose three method points that a reader would otherwise misread:**
   - The "valid identifier" rule rejects the identifiers that some large publishers use for themselves, such as USAID's `US-GOV-1` (M3).
   - Stage 5 and stage 6 place transactions in countries by different rules, although the docs and the report say the rules are the same (M4).
   - The Word draft and `full_run.qmd` give different organization counts, because only the draft removes UNICEF partner codes (M10).
3. **Bring the documents up to date with the outputs, and remove fixed numbers from the report and the draft** (M2, M7, M8 and the known points).

The remaining findings are robustness points: code that is correct on this copy of the data but could fail or silently change on a new download. Examples are M5, M6 and m1 to m11.

## 2. Findings

Severity: **critical**, a published number is wrong. **Major**, it could become wrong, or it cannot be checked. **Minor**, correct but confusing or fragile. **Style**, readability only. Known points from the brief are marked (K).

| ID | Sev. | Category | `file:line` | Finding | Evidence | Effect on published numbers | Proposed fix |
|---|---|---|---|---|---|---|---|
| M1 | major | coherence | `run_all.py:130-153`; `outputs/.../run_manifest.json`; `report/full_run.qmd:119` | The manifest does not describe the current outputs. | The manifest was finished 2026-10-31 01:30 UTC. `stages_run` lists stages 1 to 5. `code_version` is `null`, because the run predates the first commit (`ca3c73f`, 30 Oct 19:51). `indicators.csv` and `06_spatial.json` were written on 2 Nov. `PACKAGES` (`common.py:22`) omits shapely. | None directly. But no published number can be traced to a commit and its settings, and the report's run time and row counts come from the old manifest. | Commit, then run `run_all.py` from stage 1 with a clean tree. Add `shapely` to `PACKAGES`. Optionally have each stage write the git version into its own summary, so a single-stage run is traceable. |
| M2 | major | coherence | `docs/indicators.md:33-51, 71-91` | Stale indicator documentation (K). x1 is given as 79.7% and x1b as "pending". The whole x1 column of the sensitivity table is stale. x1b has no row in the definitions table. The country-level versions of indicators 1 and 3 are not described. | `sensitivity.csv` gives x1 as 61.2 (global, default), 57.0 (2025), 62.1 (with expenditure), 57.3 (with expenditure, 2025) and 51.0 (activity-period year). The docs give 79.7, 78.6, 80.0, 78.7 and 74.2. | The document is wrong for x1 in 5 of 7 rows (unit: transactions). The report computes its numbers from the outputs, so the report is not affected. | Regenerate the table from `sensitivity.csv`. Add an x1b row. Mark the x1 decision as taken. Add a section on the country versions of indicators 1 and 3 (`country_indicators.csv`). |
| M3 | major | error (method) | `common.py:111-127`; `02_clean.py:204-206`; `reference/org_registration_agencies.csv` | "Valid ID" means "the prefix is in the registration-agency codelist, or the ID is in the legacy list". `US-GOV` is in neither, so USAID's own `US-GOV-1` counts as invalid. So do `SE-0`, `DAC-1601` (Gates) and `44000` (World Bank). | 86 of 1,974 publishing organizations have a reporting-org ref that fails the rule, and they publish 380,887 of 893,847 activities. `US-GOV` is absent from the reference file and from the IATI 2.03 codelist page (that page was read only in part). | Indicator 2 is 62.8% (879,852 of 1,401,645 transactions). It would be 68.4% if refs of known publishers were accepted (+78,415, of which 36,731 are `US-GOV-*`). Indicator 3 would rise from 19.8% to 20.3% (+24,603). | Decide whether this is intended. Either keep it and say plainly in the chapter that "valid" means "codelist-conformant", so that USAID's own identifier fails, or add a third class, "known publisher identifier". Report the size either way. |
| M4 | major | coherence | `05_indicators.py:177-184`; `06_spatial.py:762-766`; `docs/methods_decisions.md:180`; `report/full_run.qmd:524` | The two stages place transactions in countries by different rules. Stage 5 places a transaction in a country if the activity **or** the transaction names it. Stage 6 uses the activity's countries first, and the transaction's own only if the activity names none. The docs and the report say the two are the same rule, with identical results. | Indicator 3 denominators, global, stage 5 rule against stage 6 "every": Sweden 115 against 98, Kenya 125,900 against 125,885. Uganda agrees, which is why the check on Uganda 2025 passed. | None today, because the only country subset in stage 5 is UG 2025. Any new country subset can differ from the map (unit: transactions). | Use one function, for example `common.transaction_countries()`, in stages 4, 5 and 6. Then the report's claim becomes true. Add test T3. |
| M5 | major | error | `report/sensitivity.py:76-93` | The default outputs are restored only if every alternative succeeds. `s5.run(out)` sits after the loop and outside `try/finally`. | Code reading (**unverified** by running, because running it overwrites outputs). If an alternative raises an error, `config` is restored, but `indicators.csv` and `indicator_settings.json` keep the alternative's values. | None today. On a failure, the report would silently show an alternative as the default. | Put `s5.run(out)` in a `finally` block, or write each alternative to its own subfolder and never touch the default folder. |
| M6 | major | error | `01_parse_bulk.py:419-436, 450` | A file whose root element has a default XML namespace is read as having no activities, and it is marked `status = ok`. The `tag=` filter matches only tags without a namespace. | `ipmc-humanitaire-activities.xml` declares `xmlns="http://iatistandard.org/activity-schema"`. It holds 14 activities and 0 transactions, and `files.csv` shows `kind = unknown`, `status = ok`. It is the only such file in this copy. | 14 activities missing, with no transactions, so no indicator changes. A new download could bring more such files, and they would be lost without a warning. | Treat `kind == "unknown"` as a failure, with a logged warning, and count it in the summary. Or strip namespaces, for example with `etree.QName(el).localname`. Add test T1. |
| M7 | major | coherence | `report/full_run.qmd:188, 198-199, 204, 522, 915`; `report/chapter6_draft.qmd:60, 66, 129, 143, 159-160, 275, 279, 306` | Fixed numbers and names in text that is otherwise computed at render (K: "130 transactions (25 activities)"). Examples: "2,206 CAFOD activities", "FCDO → CAFOD", "(5 steps)", `` `r 15` `` (a literal, not `CONTEXT_FLOWS_N`), "33,763", "Half", "Bangladesh, Indonesia and the Philippines", "10 funders and 28 recipients", "UK and Netherlands". The text of Figure 6.7 names a chain (Netherlands MFA → Womankind → Siiqqee), while the figure plots `show_ids[3]`, whatever ranks third. | Checked as still true: 130 / 25 (130 of 7,938 non-duplicate transactions, in 25 activities), 2,206, 5 steps, 15, 33,763, and 49.6% ("half"). **Unverified**: the Asian destinations and the 28 recipients. | None today. After any rerun the text and the figures can disagree without warning. | Compute each value inline, or `stopifnot()` the assumption next to the text. Select the Figure 6.7 chain by its path, not by its rank. |
| M8 | major | coherence | `report/full_run.qmd:904` | Section 10 says all `XM-`, `XI-` and `XR-` identifiers are international (K). Since part B, `XM-DAC-` codes below 40000 are national. | The code is `06_spatial.py:511-516`. The `international` column of `geo_chain_orgs.csv` follows the new rule. | The text misstates the rule. The figures are not affected. | Reword as in section 11 (`full_run.qmd:1045`). |
| M9 | major | coherence | `report/chapter6_draft.qmd:33-38, 60, 65`; `report/full_run.qmd:61, 317` | UNICEF partner codes (K). The pipeline and `full_run.qmd` count 33,763 organizations, and 99.6% are in one group. The draft removes 11,240 codes with a pattern written only in R (`is_vendor`) and gives 22,523 organizations and 99.3%. The code handles them consistently: they are `well_formed`, they publish no activities, and `org_countries()` treats them as international, because 41122 ≥ 40000. | Pattern `^XM-DAC-41122-VN-`. The counts are as in `docs/method_alignment.md:56`. | Two documents give two organization counts (unit: organizations). Links, chains and indicators are unaffected. | Make the choice in Python, as a flag column in `organisations.parquet` (for example `partner_code`), and use the same count in both documents. |
| M10 | major | test | `tests/` | There are no tests for several rules whose failure would be silent: the namespace case, the agreement of the country rules, `MAX_CHAINS` truncation, cycles, the "other publisher" and strict labels, the activity-period year, and recovery in the sensitivity script. | See section 3. | None now. | Add tests T1 to T20 (section 3). |
| m1 | minor | coherence | `config.py:158-160`; `04_network.py:302-306` | The comment "the depth measures do not depend on it [`MAX_CHAINS`]" is only half true. The per-chain and per-path distributions, indicators 6c to 6e and `n_chains` all use the listed chains. Only the per-origin depth (`longest_from`) is exact. | The total is 12,751 chains, far below 5,000,000. | None today. | Correct the comment. When the list is truncated, write `chains_truncated = True` into `indicators.csv`. |
| m2 | minor | coherence | `docs/methods_decisions.md:145-152`; `04_network.py:47-56` | The docs list 7 reasons why a chain stops. The code has 8: "organization ID given as activity ID" is missing from the docs. | Compare the two lists. | None. | Add the reason to the docs. |
| m3 | minor | coherence | `docs/indicators.md:27`; `config.py:165-166`; `05_indicators.py:80, 124-126` | The docs say "Outgoing for indicators 3, 4 and x1" means types 2 and 3. Only indicator 3 uses that set. Indicators 4 and x1 use every in or out direction. Under `DISBURSEMENTS_ONLY`, 4 and x1 are cut to types 1 and 3, so that sensitivity row changes two things at once. | Denominator of indicator 4 by type: 198,220 of 2,236,776 transactions are types 5, 6, 7, 8, 10, 12 or 13 (132,593 of them type 5, 64,725 type 6). | None. The definition is consistent, but it is described wrongly. | Correct the docs and the comments. Decide whether types 5 and 6 (repayments) belong in indicator 4. |
| m4 | minor | error | `05_indicators.py:437-439`; `06_spatial.py:805-808`; `report/full_run.qmd:33` | Some CSV files are read with the default NA strings, so the country code `NA` (Namibia) becomes missing (K, for the older report sections). | 2 rows of `chains.csv` have `countries` exactly `NA`. 252 rows contain `NA` in a list, and those are read correctly. `country_flows.csv` has 4 cells equal to `NA`, and `country_indicators.csv` has 12. Both are read correctly with `rd_codes()`. | 2 chains show no country in the report's chain table (unit: chains). | In Python, use `keep_default_na=False, na_values=[""]`. In R, make `rd()` use `na = ""`, and drop `rd_codes()`. |
| m5 | minor | error | `03_links.py:298`; `docs/data_dictionary.md:235` | `same_org` compares reporting-org refs, not publishers. Two activities without a ref give `NA == NA`, which becomes "different", so the link enters the network. | 6 network links lie inside one publisher folder (`fawe-rs`, refs `NL-KVK-41201644` and `KE-NCB-218-051-93197-173`). 2 network links have a missing `from_org` or `to_org`. | At most 8 of 13,024 network links (unit: links). | Treat a missing ref as "cannot tell", and keep such links out of the network. Say "same reporting-org ref" in the data dictionary. |
| m6 | minor | error | `03_links.py:144-149` | The "linked ID starts with `<ref>-`" test accepts a bare agency prefix as a match. | 5 link transactions name `XM-DAC` and 1 names `GB`, and all 6 count as "match". | Could flip a few links to plausible (6 transactions). | Skip the prefix test when the ref is itself an agency prefix or has no hyphen. |
| m7 | minor | error | `03_links.py:233-243` | `sum()` of values that are all missing gives 0, so an edge with unreadable values shows `type 3 USD 0`, not `NA`. The `fillna("NA")` never applies. | 2 of 54,920 (edge, side, type, currency) groups. | Display only (values are not used in indicators). | Use `sum(min_count=1)`. |
| m8 | minor | error | `04_network.py:344, 413` | 3 chains have no `end_reason`. **Inferred cause:** the end activity's only outgoing links lie inside a cycle. `with_onward` is taken from the graph with cycles (`g`), not from the graph without them (`dag`). | `chains.csv`: 3 rows with `end_reason` missing. | 3 chains are missing from the "why chains stop" view for chains (unit: chains). | Take `with_onward` from `dag`, or add a reason "onward link set aside (cycle)". |
| m9 | minor | error | `report/full_run.qmd:1023, 1038-1039, 1088, 1092` | The map lookup has duplicate codes (K): Australia 3 times and `-99` 3 times (`rnaturalearth`, medium scale). `pos_ee` is joined without `distinct()`, while line 1058 does use it. | No Australian pair is in the top 40, or in the countries with 30 or more links. | None today. An Australian pair would be counted 3 times (unit: links). | Deduplicate once, where `world_ne` is built. Add `stopifnot(!anyDuplicated(iso2))`. |
| m10 | minor | error | `config.py:241-248`; `06_spatial.py:512-515` | The rule "`XM-DAC` below 40000 = national government" also catches non-government bodies. | `XM-DAC-30010` (Unitaid) funds 1 network link and is counted as national. `XM-DAC-236` (Benin's finance ministry) is correctly national. | 1 link (unit: links). | List the national DAC codes explicitly, or treat 30000 to 39999 as international. |
| m11 | minor | coherence | `config.py:56-89` | Transaction type 9 (sale of equity) has no direction. The guidance treats it as incoming (K). | 150 transactions. 120 transactions have no type. | Up to 150 transactions missing from the denominators of indicators 4 and x1. No links are affected. | Add `"9"` to `INCOMING`, or state the choice in the docs. |
| m12 | minor | coherence | `README.md:9, 53, 127-158`; `06_spatial.py:10-20` | README: (a) says a stage 5 setting needs only stage 5, but stage 6 also uses `INDICATOR3_INCLUDE_EXPENDITURE` and `INDICATOR1_RELATED_ACTIVITIES`; (b) parts D and B are missing from the stage diagram, and the run time and memory are those of part A (K); (c) the file table omits `06_spatial.py`, `chapter6_draft.qmd` and `docs/method_alignment.md`, and its last two rows sit after a code block, so they no longer render as a table. The stage 6 docstring omits `geo_chain_orgs.csv`, `geo_chain_context.csv` and `country_flows.csv`. | Read side by side. | None. | Update the README and the docstring. Say "rerun from stage 5". |
| m13 | minor | legibility | `01_parse_bulk.py:438`; `06_spatial.py:380, 447-450` | `copy_id = file_idx × 1,000,000 + position` has a hidden limit of 1,000,000 activities per file. It also depends on the sorted file list. Stage 6 reuses `locations.parquet` by comparing file times. | The largest file has 9,165 activities. | None. | Assert `activities < 1_000_000` in stage 1. Store the stage 2 run time in the summary and compare against it, not against file times. |
| m14 | minor | legibility | `docs/methods_decisions.md:57`; `02_clean.py:40-58` | "Identical in every field": `CONTENT_COLS` leaves out the organization types and compares the raw type code and the normalized refs. | Read side by side. | None. | Say "identical in the content fields listed in `CONTENT_COLS`". |
| m15 | minor | error | `01_parse_bulk.py:196-204` | `parse_value()` removes every comma, so a decimal comma is misread: `"1.234,56"` gives 1.23456, and `"12,5"` gives 125. | In the bulk copy, 29 `<value>` texts contain a comma, and all are thousands separators. | None in this copy. | Flag values with a comma that are not thousands separators as `value_valid = False`. Test T17. |
| m16 | minor | legibility | `report/chapter6_draft.qmd:20-29`; `report/full_run.qmd:184, 541` | The draft runs chunks of `full_run.qmd` by label, in the global environment. This is fragile: a renamed label stops the render (good, `stopifnot`). But a reused name changes results silently. `ex` is first the example-chain list (line 184), then a tibble (line 541). `ends` was already hit (line 31 of the draft). `ex[[5]]` and `ex[[4]]` pick examples by position. A closing fence with trailing spaces is not found. | Code reading. Nothing is broken today. | None today. | Move shared code into `report/figures.R` (functions that take data and return a plot), and `source()` it from both documents. Name the examples in the JSON (`kind`), not by position. |
| m17 | minor | coherence | `05_indicators.py:247`; `docs/indicators.md:46` | Indicator 7b drops activities without a reporting-org ref, because `groupby` drops missing keys. | 440 activities have no reporting-org ref. | Small (unit: organizations; denominator of 7b). | State it, or count missing as "unknown". |
| m18 | minor | legibility | `pyproject.toml:3`; `requirements.txt` | Ruff targets `py313` while the environment is 3.15 (K). The R packages are not pinned (`sf`, `rnaturalearth`, `ggraph` and others), and the map borders and the R-side counts depend on them. | — | None. | Set `target-version = "py315"` if ruff supports it, else note why not. Record R package versions (`renv`, or `sessionInfo()` written by the report). |
| s1 | style | legibility | `04_network.py:248-525`; `06_spatial.py:735-893` | `run()` in stage 4 is 280 lines. Stage 6 is 904 lines, holding parts A, D and B. Each does several things. | — | — | Split into `run_chains()`, `run_ends()`, `run_org_network()`. Put parts A, D and B in separate modules or functions. |
| s2 | style | legibility | `run_all.py:32`; `06_spatial.py:75`; `report/sensitivity.py:53`; `tests/test_pipeline.py:28` | Four copies of `load_stage()`. | — | — | Put one in `common.py`. |
| s3 | style | legibility | `05_indicators.py:230, 455`; `checks/compare_uganda.py` | Dead code: the `all_tx_n` parameter is never used. `checks/compare_uganda.py` is no longer used. | — | — | Remove the parameter, and move the script to an `archive/` folder. |
| s4 | style | legibility | `report/example_chains.py:278` | `json.dumps(..., default=str)` writes numbers as strings (`"chain_id": "4108"`). | `example_chains.json` | — | Convert NumPy types to Python numbers first. |

### Notes on the known points

- **K1 (x1 79.7%, x1b undecided):** confirmed. See M2. The sensitivity table is stale in more rows than x1 alone.
- **K2 (README and stage 6 docstring):** confirmed. See m12.
- **K3 (country versions of indicators 1 and 3 not in `indicators.md`):** confirmed. See M2.
- **K4 (section 10 XM text):** confirmed. See M8.
- **K5 ("130 transactions (25 activities)" fixed):** confirmed. The number is correct now: 130 of 7,938 non-duplicate transactions that give a country inside an activity that also gives one, in 25 activities. See M7.
- **K6 (CSV read without `na = ""`):** confirmed. See m4. It affects 2 chains.
- **K7 (duplicate map codes):** confirmed for AU and `-99`. No current effect. See m9.
- **K8 (type 9):** confirmed: 150 transactions. See m11.
- **K9 (UNICEF codes):** handled consistently in the code. The inconsistency is between the two documents. See M9.

### Checked and found correct

- Stage 1 to 2 hand-offs: `rows_act` and `rows_tx` equal the sums over kept files. `transactions_kept` equals the sum of `n_transactions` over kept copies.
- Copy selection is deterministic: the last tie-break is `copy_id`, and files are sorted.
- Stage 3: `n_transactions.sum() == len(links)`. There are no duplicate edges. No link transaction has a missing currency, and all currencies are three capital letters. One transaction never makes both a provider-side and a receiver-side link, because the type sets are disjoint.
- Values are never summed across currency or transaction type (`values_by_currency`).
- Exact activity-ID matching, case kept. Org refs are trimmed and upper-cased. The prefixes in the codelist are all upper case.
- The example chains in `example_chains.json` match the current `chains.csv` by path.
- `sensitivity.csv` default rows equal `indicators.csv` (40 rows compared).
- The changes to stages 1 to 4 since the run (`git diff ca3c73f HEAD`) have no effect under the default settings.

## 3. Missing tests

Each test lists an input (a small fixture or a direct function call) and the expected answer.

| ID | Rule | Input | Expected answer |
|---|---|---|---|
| T1 | Namespaced files (M6) | A file with `<iati-activities xmlns="http://iatistandard.org/activity-schema">` and 1 activity with 1 transaction | Either 1 activity and 1 transaction read, or `status = failed` with a warning. Never `ok` with 0. |
| T2 | Type 9 (m11) | A transaction with `code="9"`, and a version 1 transaction with `code="QS"` | Both recoded to `"9"`. `direction` is `"in"` if the rule is adopted, otherwise `"other"`, pinned explicitly. |
| T3 | Stage 5 and 6 agree (M4) | Activity naming UG, with an outgoing transaction naming KE and a valid receiver ref, dated 2025; `SUBSETS = [("KE", 2025)]` | Indicator 3 for KE 2025 is the same in `indicators.csv` and in `country_indicators.csv` (rule "every"). Today: 1 against 0 transactions. |
| T4 | Missing org refs (m5) | Two activities with no reporting-org ref, linked by a disbursement | `same_org` is not treated as FALSE. The link is not in the network, or is flagged. |
| T5 | Bare agency prefix (m6) | Link transaction with `named_org_ref = "XM-DAC"` to activity `XM-DAC-7-123` | `org_check = "unrecognised ref"`, not `"match"` |
| T6 | Unreadable values (m7) | Edge with one disbursement whose `<value>` is `"abc"` | `provider_value_by_currency = "type 3 USD NA"` |
| T7 | `MAX_CHAINS` (m1) | Graph A→B, A→C, A→D; `enumerate_chains(dag, 2)` | `total = 3`; 2 chains listed; `longest_from(A) = 1` |
| T8 | Cycles and ends (m8) | Edges A→B, B→A, B→C (all plausible, different orgs) | A→B and B→A set aside. Chain B→C. Every chain has an `end_reason`. |
| T9 | All version 1 codes | `recode_transaction_type()` on IF, C, D, E, IR, LR, R, QP, QS, CG, lower case `"d"`, `" D "` | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 3, 3 |
| T10 | Reverse-funding check with a match | X→Y reported only by X, Y→X reported by X as incoming, and the named ref on X→Y equals Y's publisher | Label `"org matches"`, plausible |
| T11 | Other publisher and strict rule | Named ref = another publisher's ref; a second edge with an unrecognized ref | First: `"names another publisher"`, implausible under both rules. Second: plausible under `known_publishers`, implausible under `strict`. |
| T12 | Exact duplicates (m14) | Two transactions of one activity identical except `receiver-org type` | Pins the current behavior: the second is flagged `exact_duplicate` |
| T13 | Missing last-updated | Two copies of one identifier, one without `last-updated-datetime` | The dated copy is kept, with reason `"older last-updated-datetime"` |
| T14 | Activity-period year | `YEAR_BY_TRANSACTION_DATE = False`; an activity without a start date, transactions dated 2024 and 2026, end date 2026 | The activity is in the 2025 scope, and all its transactions count |
| T15 | Small base | Denominators 29 and 30 | `small_base` is TRUE and FALSE |
| T16 | Split with a filtered list | Activity countries `"UG;GLOBAL"`, percentages `"50;50"` | Percentages dropped. Weight for UG = 1.0. |
| T17 | Decimal comma (m15) | `parse_value("1.234,56")`, `parse_value("1,234")`, `parse_value("12,5")` | Invalid (or 1234.56), 1234.0, invalid |
| T18 | DAC national rule (m10) | `org_countries(["XM-DAC-30010", "XM-DAC-302"])` | Unitaid international; United States national |
| T19 | Namibia round trip (m4) | Chain whose countries are `"NA"`, read back by stage 5 or 6 | `"NA"`, not missing |
| T20 | Sensitivity recovery (M5) | An alternative that raises an error | After the run, `indicator_settings.json` shows the defaults |

Also worth adding: the data dictionary test checks that every column is listed, but not the outputs in `examples/` or `sensitivity_disbursements_only/`, and not the JSON summaries.

## 4. Legibility notes

These are the four changes that would help a new reader most.

1. **One function for each placement rule, shared by all stages.** Countries are worked out in three places: `04_network.activity_countries()`, `05_indicators.in_scope()` with `names_country()`, and `06_spatial.activity_countries()` with `valid_countries()`. They follow different rules (M4). A single `common.py` function per rule (activity countries, transaction countries) would let a reader check the rule once. In R terms: one `place_countries()` function that every script calls, instead of three similar `mutate()` blocks.
2. **Split stage 6, and shorten the `run()` functions.** `06_spatial.py` holds three analyzes (A, D, B). Stage 4's `run()` is 280 lines. Give each part its own `run_part_x()`, each with a docstring stating its inputs, outputs and unit. Move `load_stage()` into `common.py`.
3. **Add missing values in pandas to the README's R section.** The code uses `.fillna(False).astype(bool)` and `.where()` everywhere. An R reader needs four lines on this: pandas has `NaN`, `None` and `pd.NA`. A comparison with a missing value gives missing, as in R. `.fillna(False)` is `coalesce(x, FALSE)`. `.where(cond, other)` is `if_else(cond, x, other)`. `read_csv` turns `"NA"` into missing in both languages unless told not to.
4. **No fixed numbers in prose, and one shared R file for figures.** Put the shared figure code in `report/figures.R`, as functions, and `source()` it from both `.qmd` files instead of `run("label")`. Every number in the text should come from an inline `r` expression. Where the text names a specific case (CAFOD, Womankind), add a `stopifnot()` that checks the case still holds. In the docs, give each count table the output file it comes from, so a reader can check it.
