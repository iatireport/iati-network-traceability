# IATI as network data: traceability pipeline

This pipeline measures **traceability** in IATI data: how far funding can be followed from funders, through intermediaries, to the organizations that implement the work. It uses the organization identifiers and activity identifiers that publishers write in their transactions. It reads a dated copy of the IATI Bulk Data Service and writes tables (CSV and Parquet) that R can read directly.

It was written for the network and traceability chapter of the IATI State of the Data report. The results reported there come from the Bulk Data Service copy with index date **30 October 2026**.

**A note on the code.** The author works mainly in R. The pipeline is in Python (pandas, lxml, networkx) for speed on 11 million transactions, but it is written to be read by R users: each method rule is a named function, operations work on whole columns as in dplyr, and every output can be opened in R. The last section of this file translates the main pandas idioms into tidyverse terms.

## What is in this repository

| Folder or file | Content |
|---|---|
| `01_parse_bulk.py` … `06_spatial.py` | The six stages |
| `run_all.py` | Runs the stages in order and writes `run_manifest.json` |
| `config.py` | All paths and method settings, each with a comment |
| `common.py` | Shared helpers: logging, CSV reading (`read_csv`, which keeps "NA" as Namibia), the identifier rules (`norm`, `clean_id`, `well_formed`, `load_codelists`), the country rule shared by stages 5 and 6 |
| `reference/` | Reference data: IATI organization registration agencies, legacy organization identifiers, Natural Earth country borders (`country_borders.geojson`, made by `make_country_borders.R`) |
| `report/` | The results page (`results.qmd`) and the scripts it needs (see [Report](#report)) |
| `tests/` | Small hand-made XML files with known answers (`tests/fixtures/bulk/`) and the tests (`test_pipeline.py`) |
| `docs/` | Method decisions, indicator definitions, data dictionary, code review (see [Documentation](#documentation)) |
| `requirements.txt` | Exact Python package versions |
| `pyproject.toml` | Settings for `pytest` and `ruff` |

The repository holds the code, the documentation and the report source, not the data. The IATI bulk copy (about 12 GB) and the output tables of a run (about 1 GB) are left out. The results of the published run can be read in the rendered report (see [Report](#report)). The drafts of the chapter are kept separately.

## Data source

The pipeline reads a local copy of the [IATI Bulk Data Service](https://bulk-data.iatistandard.org/). The service keeps a cached copy of every dataset registered with IATI and offers all of them as one ZIP file, with index files.

**To get a copy:**

1. On <https://bulk-data.iatistandard.org/>, download the bulk data ZIP and the index files. The pipeline needs `datasets-minimal.json` and `reporting-orgs.json`; `datasets-full.json` is optional (it gives the reason why a dataset is missing).
2. Unzip into one folder so that it looks like this:
   ```
   iati-data/
     datasets-minimal.json
     datasets-full.json
     reporting-orgs.json
     datasets/<publisher>/<dataset>.xml
   ```
3. Put the folder at `iati-data/` inside this project (the default), or anywhere else and set `CH6_BULK_DIR` (see [How to run](#how-to-run)).

A copy takes about **12 GB** of disk.

**Reproducing the published numbers.** The Bulk Data Service always serves the current data; an older copy cannot be downloaded again. A new copy gives different counts, because publishers add and change data every day. The published results use the copy with index date 30 October 2026 (`index_created` in `datasets-minimal.json`): 13,989 datasets in the index, 747 with no file in the copy, 10 files that cannot be read. The output folder of each run is named after the index date, and `run_manifest.json` records the copy, the settings, the package versions and the code version.

**Data licenses.** Each IATI dataset is published under its publisher's own license (`licence_id` in `datasets-minimal.json`; IATI requires an open license). This repository does not redistribute any IATI data.

## Requirements

- **Python 3.15** (the version used and tested). The packages and their exact versions are in `requirements.txt`: pandas, pyarrow, lxml, networkx, shapely, pytest, ruff and their dependencies.
- **Memory:** stage 2 uses up to about 13 GB. A machine with 16 GB is enough if other large programs are closed.
- **Disk:** about 12 GB for the bulk copy and about 1 GB for the outputs of one run.
- **R (only for the report):** R 4.3 or later and [Quarto](https://quarto.org/). R packages: tidyverse, arrow, jsonlite, igraph, ggraph, ggrepel, patchwork, knitr, sf, rnaturalearth, leaflet, visNetwork, plotly, DT, scales, viridisLite. The pipeline itself does not need R.

## Setup

From the project folder, in a terminal:

```bash
python3.15 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

This makes a Python environment in `.venv/` that belongs to this project, like `renv` in R. All commands below use `.venv/bin/python`, so the system Python is never used.

## How to run

```bash
.venv/bin/python run_all.py
```

This runs stages 1 to 6. To use a bulk copy outside the project folder, or to write the outputs elsewhere:

```bash
CH6_BULK_DIR=/path/to/iati-data .venv/bin/python run_all.py
```

```bash
CH6_BULK_DIR=/path/to/iati-data CH6_OUTPUT_DIR=/path/to/outputs .venv/bin/python run_all.py
```

| Variable | Meaning | Default |
|---|---|---|
| `CH6_BULK_DIR` | Folder of the Bulk Data Service copy (the one that holds `datasets-minimal.json`) | `iati-data/` in the project folder |
| `CH6_OUTPUT_DIR` | Folder for the outputs of this run | `outputs/bulk_<index date>/` in the project folder |

To rerun from one stage onwards, or to run one stage alone:

```bash
.venv/bin/python run_all.py --from-stage 5
```

```bash
.venv/bin/python 03_links.py
```

**Run times** of the last full run (12 November 2026, copy of 30 October 2026, on a laptop with 16 GB of memory): about 12 minutes in total.

| Stage | What it does | Time |
|---|---|---|
| 1 `01_parse_bulk.py` | Reads all XML files into tables | 4 min |
| 2 `02_clean.py` | One copy per activity; identifiers cleaned and checked; duplicates flagged | 2.5 min |
| 3 `03_links.py` | Links between activities and between organizations | 0.5 min |
| 4 `04_network.py` | Network: cycles, chains, depth, where chains stop | 0.5 min |
| 5 `05_indicators.py` | Indicators, overall, by publisher group, by country and year | 2 min |
| 6 `06_spatial.py` | Indicators by recipient country; locations; links between countries | 3 min |

Each stage writes a log to `logs/<stage>.log` in the output folder. Outputs of different bulk copies never overwrite each other.

## Stages

Each stage reads the previous stage's files and writes its own, so any intermediate table can be inspected in R. Changing an indicator setting needs only stages 5 and 6 to be rerun (`run_all.py --from-stage 5`): stage 6 uses the stage 5 definitions of indicators 1 and 3.

```
 Bulk Data Service copy (config.BULK_DIR, ~12 GB XML)
   datasets/<publisher>/<dataset>.xml, datasets-minimal.json, reporting-orgs.json
                     |
                     v
 01_parse_bulk.py    XML -> tables; activity defaults applied; v1 and malformed
                     type codes recoded; failed files logged
                     -> activities, transactions, related_activities, publishers (.parquet)
                        files.csv, datasets_missing.csv
                     |
                     v
 02_clean.py         one copy per activity; IDs normalized and validated;
                     direction, counterpart, internal, exact duplicates flagged
                     -> activities_clean, transactions_clean, related_activities_clean,
                        organisations (.parquet), duplicates_removed.csv
                     |
                     v
 03_links.py         links between activities (with evidence and plausibility)
                     and between organizations
                     -> link_transactions, activity_edges, org_edges (.parquet; edges also .csv)
                     |
                     v
 04_network.py       global activity network: cycles, chains, depth, where chains
                     stop; organization network
                     -> chains.csv, chain_steps.csv, org_paths.csv, depth_distribution.csv,
                        chain_end_reasons.csv, cycles.csv, network_nodes, org_nodes
                     |
                     v
 05_indicators.py    indicators 1-7b, 6-6e, x1, x1b, D1-D2, overall and by
                     publisher group, globally and per (country, year) subset
                     -> indicators.csv, depth_by_scope.csv
                     |
                     v
 06_spatial.py       A: indicators 1 and 3 by recipient country, under three
                        multi-country rules; what cannot be placed on a map
                        -> country_indicators.csv, spatial_unplaced.csv,
                           spatial_links_unplaced.csv
                     D: activity locations; example chains that end at a mapped point
                        -> locations.parquet, geo_chains.csv, geo_chain_points.csv,
                           geo_chain_orgs.csv, geo_chain_context.csv
                     B: network links by funder country and recipient country
                        -> country_flows.csv
                     |
                     v
 run_all.py          runs 01-06 in order -> run_manifest.json
```


Stage 6 reads only the columns it needs. The first run after stage 2 also reads the `<location>` elements from the bulk copy (about 1 more minute) and keeps them in `locations.parquet`.

## Settings

All method rules are in `config.py`. The settings that answer open method questions are:

| Setting | Question | Default |
|---|---|---|
| `INDICATOR3_INCLUDE_EXPENDITURE` | Count expenditure as outgoing in indicators 3, 4 and x1? | `False` |
| `INDICATOR1_RELATED_ACTIVITIES` | Count related-activity links in indicator 1? (`"none"`, `"other_publishers"`, `"all"`) | `"none"` |
| `YEAR_BY_TRANSACTION_DATE` | Define a subset's year by transaction date (`True`) or activity period (`False`)? | `True` |
| `DISBURSEMENTS_ONLY` | Count disbursements only (type 3 out, type 1 in) instead of pooling commitments and disbursements? | `False` |
| `PLAUSIBILITY_RULE` | `"known_publishers"` or `"strict"` | `"known_publishers"` |
| `SUBSETS` | Country-year subsets to report | 2023, 2024, 2025, and Uganda 2025 |

After changing a stage 5 setting, rerun with `--from-stage 5` (about 5 minutes). After changing `PLAUSIBILITY_RULE`, `DISBURSEMENTS_ONLY` or a network setting, rerun with `--from-stage 3`. After changing a spatial setting (section 6 of `config.py`), rerun with `--from-stage 6`.

## Outputs

All outputs go to `outputs/bulk_<index date>/`, for example `outputs/bulk_2026-10-30/`. Every column of every file is described in [`docs/data_dictionary.md`](docs/data_dictionary.md). The main files:

| File | Content |
|---|---|
| `indicators.csv` | All indicators, with numerator, denominator and unit, globally and for each subset and publisher group |
| `activity_edges.csv`, `org_edges.csv` | Links between activities and between organizations, with their evidence |
| `chains.csv`, `chain_steps.csv`, `org_paths.csv` | Funding chains, their steps and the organizations on them |
| `chain_end_reasons.csv` | Why each chain stops |
| `country_indicators.csv`, `country_flows.csv` | Indicators by recipient country; links between countries |
| `run_manifest.json` | What was run, on which data, with which settings, package versions and code version |

The large Parquet files (for example `transactions_clean.parquet`, 11.4 million rows) need not be loaded into memory. [DuckDB](https://duckdb.org/) can query them where they are, from R or Python:

```r
library(duckdb)
con <- dbConnect(duckdb())
dbGetQuery(con, "
  SELECT transaction_type, count(*) AS n
  FROM 'outputs/bulk_2026-10-30/transactions_clean.parquet'
  GROUP BY transaction_type")
```

DuckDB reads the outputs; it does not replace the bulk copy, because stage 1 needs the XML files.


## Tests

```bash
.venv/bin/python -m pytest
```

The tests run the stages on the small XML files in `tests/fixtures/bulk/`, whose answers are known by hand, and check the results (58 tests, about 10 seconds). They need no bulk copy.

## Report

`report/results.qmd` is the results page: the figures of the working paper on network and traceability, with their captions, and interactive tables, charts, a network and a map to explore the same data. It reads only the output folder. To build it after a run, from the project folder:

**Before the first render**, download the OECD DAC List of ODA Recipients and save it as `report/reference/dac_oda_recipients_2025.csv`, as described in `report/reference/README.md`. It is not included in this repository.

```bash
.venv/bin/python report/example_chains.py
```

```bash
.venv/bin/python report/sensitivity.py
```

```bash
.venv/bin/python report/case_pair.py
```

```bash
.venv/bin/python report/location_precision.py
```

```bash
cd report && quarto render results.qmd
```

The four scripts must run first:

| Script | What it writes |
|---|---|
| `example_chains.py` | Five example chains chosen by rule, with the XML behind each step (`examples/` in the output folder) |
| `sensitivity.py` | Stage 5 rerun under each alternative setting (`sensitivity.csv`); for disbursements only, stages 3 to 5 in `sensitivity_disbursements_only/` |
| `case_pair.py` | A traceable and a broken chain in the same sector and country (`report/figures/wp/case_pair.json`) |
| `location_precision.py` | Whether published location points sit at capital cities (`report/figures/wp/location_precision.csv`) |

Other files in `report/`:

| File | Content |
|---|---|
| `full_run.qmd` | The first report on the full run. `results.qmd` reuses its setup and interactive parts by chunk label, so it stays here |
| `figures.R` | Shared figure code: colors, chain diagrams, and the reuse of `full_run.qmd` chunks |
| `indicator4_2025_check.R`, `reporting_lag_check.R` | Two checks drawn on the page: indicator 4 in 2025 by publisher, and reporting lag |
| `top_publishers.py` | Names the publishers behind indicator D2 and behind duplicate and internal transactions |
| `reference/` | Natural Earth capitals and two published comparison values. The OECD DAC list of ODA recipients is not included: download it first (see `report/reference/README.md`) |

If the output folder has a new date, change `out_dir` at the top of `results.qmd`, or render with `quarto render results.qmd -P out_dir:../outputs/bulk_<date>`.

## Documentation

| File | Content |
|---|---|
| [`docs/methods_decisions.md`](docs/methods_decisions.md) | Every method decision, why it was made, and its effect on the counts. Starts with definitions of activity, transaction, link, chain, origin and organization path |
| [`docs/indicators.md`](docs/indicators.md) | Each indicator: definition, numerator, denominator, unit, and sensitivity to the settings |
| [`docs/method_alignment.md`](docs/method_alignment.md) | Comparison with the IATI traceability guidance and with McCarthy et al. |
| [`docs/data_dictionary.md`](docs/data_dictionary.md) | Every column of every output file |
| [`docs/code_review_2026-11.md`](docs/code_review_2026-11.md) | Code review of November 2026: findings, and the outcome of each |

To tidy the code layout after editing: `.venv/bin/ruff format .`

## How to cite

> Bernhard, K.P. (2026). *IATI as network data: traceability pipeline*. Code repository: https://github.com/iatireport/iati-network-traceability. Results use the IATI Bulk Data Service copy of 30 October 2026.

## License

- **Code:** MIT license (see `LICENSE`). Anyone may use, change and share the code, if they keep the license notice.
- **Documentation** (`docs/`, this file): [Creative Commons Attribution 4.0 (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). Anyone may reuse it with credit.
- **Reference data** in `reference/` keeps the license of its source:
  - `country_borders.geojson`: made from [Natural Earth](https://www.naturalearthdata.com/) 1:50m countries, which are in the public domain. Made with Natural Earth.
  - `legacy_org_identifiers.csv`: IATI OrganisationIdentifier codelist, © Development Initiatives on behalf of the IATI Secretariat, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Attribution: "IATI Standard (http://iatistandard.org/)".
  - `org_registration_agencies.csv`: IATI OrganisationRegistrationAgency codelist, copied by IATI from the [org-id.guide](https://org-id.guide/) register, which is under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/). It is redistributed here unchanged, under the ODbL.
- **IATI data** is not included; each dataset carries its publisher's license.

## Reading this code as an R user

**Packages and objects**

| Python (pandas) | R (tidyverse) |
|---|---|
| `import pandas as pd` | `library(dplyr)` |
| `df = pd.read_parquet("x.parquet")` | `df <- arrow::read_parquet("x.parquet")` |
| `df["col"]` | `df$col` |
| `df[df["col"] == 1]` | `filter(df, col == 1)` |
| `df[["a", "b"]]` | `select(df, a, b)` |
| `df.assign(new = df["a"] + 1)` | `mutate(df, new = a + 1)` |
| `df.groupby("g").agg(n=("x", "size"))` | `df \|> group_by(g) \|> summarise(n = n())` |
| `df.merge(other, on="id", how="left")` | `left_join(df, other, by = "id")` |
| `np.select([cond1, cond2], ["a", "b"], default="c")` | `case_when(cond1 ~ "a", cond2 ~ "b", .default = "c")` |
| `s.isin(values)` | `s %in% values` |
| `s.notna()` / `s.isna()` | `!is.na(s)` / `is.na(s)` |
| `~flag` | `!flag` |
| `a & b`, `a \| b` | `a & b`, `a \| b` (always the vectorised form) |
| `s.str.split(";")` | `stringr::str_split(s, ";")` |
| `df.explode("col")` | `tidyr::unnest(df, col)` |
| `pd.concat([a, b])` | `bind_rows(a, b)` |
| `networkx.DiGraph` | `igraph::graph_from_data_frame(edges, directed = TRUE)` |

**The language**
- Indexing starts at 0, not 1.
- A list `[a, b]` is like `c(a, b)` or `list(a, b)`.
- A dict `{"k": v}` is like `list(k = v)`.
- `None` is like `NULL` or `NA`.
- Indentation is part of the syntax: the body of an `if`, a `for` loop or a function is whatever is indented under it.
- `def f(x: str) -> int:` defines a function. `x: str` and `-> int` are *type hints*: they document what goes in and what comes out, and they do not change how the code runs.
- The text in triple quotes right after `def` is the function's documentation (docstring), like a roxygen comment.

**Missing values**
- pandas has three kinds of missing value: `NaN` (in number columns), `None` and `pd.NA` (in text and nullable columns). `s.isna()` finds all three, like `is.na()`.
- As in R, a comparison with a missing value gives missing, not FALSE: `pd.NA == "GB-1"` is `<NA>`. So the code often ends a test with `.fillna(False).astype(bool)`, which is `coalesce(x, FALSE)` in dplyr.
- `x.where(cond, other)` keeps `x` where `cond` is TRUE and uses `other` elsewhere: `if_else(cond, x, other)`. `x.fillna(y)` is `coalesce(x, y)`.
- `sum()` over values that are all missing gives 0 in pandas (`sum(x, na.rm = TRUE)` in R); the pipeline marks such sums as missing where it matters.
- Both pandas and `readr::read_csv()` read the text "NA" as missing by default, and "NA" is Namibia's country code. The pipeline reads CSV files with `common.read_csv()`, and the report with `rd()` (`na = ""`), so only empty cells are missing.

**How the code is organized**
- Each methodological rule is a named function (`pick_one_copy_per_activity()`, `is_plausible_link()`, `why_chain_stops()`), so a reviewer can find each choice by name.
- Operations work on whole columns at once, as in dplyr. There is no row-by-row `apply()`.
- `assert condition` stops the run if a condition fails, like `stopifnot()`. Assertions sit at the hand-offs between stages.
- `logging` writes timestamped messages to the screen and to `logs/<stage>.log` in the output folder.

**Reading the outputs in R**

```r
library(arrow); library(readr); library(dplyr)
out <- "outputs/bulk_2026-10-30"   # path from the project folder
ind    <- read_csv(file.path(out, "indicators.csv"))
edges  <- read_parquet(file.path(out, "activity_edges.parquet"))
chains <- read_csv(file.path(out, "chains.csv"))
tx     <- read_parquet(file.path(out, "transactions_clean.parquet"),
                       col_select = c(iati_identifier, transaction_type, value, currency))
```

`transactions_clean.parquet` holds 11.4 million rows. Read only the columns you need with `col_select`, or query it without loading it, using `arrow::open_dataset()` or DuckDB.
