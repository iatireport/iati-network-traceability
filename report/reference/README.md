# Reference files for the results page

## The OECD DAC List of ODA Recipients (not included: download it yourself)

The country figures use the OECD DAC List of ODA Recipients for reporting on 2025 flows. It is not included in this repository, because its terms of reuse were not confirmed. Before rendering `report/results.qmd`, make the file yourself:

1. Open the OECD page "DAC List of ODA Recipients" (https://www.oecd.org/en/topics/sub-issues/oda-eligibility-and-conditions/dac-list-of-oda-recipients.html) and use the list for reporting on 2025 flows. Read the OECD's terms of use before you copy it.
2. Make a CSV file with one row per country or territory and three columns:
   - `iso2`: the ISO 3166 two-letter code (Kosovo: `XK`; West Bank and Gaza Strip: `PS`);
   - `name`: the name as in the list;
   - `group`: the group as in the list (`Least developed countries`, `Other low income countries`, `Lower middle income countries and territories`, `Upper middle income countries and territories`).
3. Save it as `report/reference/dac_oda_recipients_2025.csv` (UTF-8). This file name is in `.gitignore`, so it is never committed.

The results page used a list of 141 countries and territories, read on 17 November 2026 (the list set in the 2023 review; São Tomé and Príncipe appears after its graduation from LDC status in December 2024). The DAC list changes over time (separate lists exist for 2022–23 and 2024 flows); the page uses the 2025 list for all years. Read the file with `na = ""`, because Namibia's code is NA.

## Other files

- `ne_capitals.csv`: national capitals from Natural Earth (public domain), made by `make_capitals.R`. Used by `report/location_precision.py`.
- `external_values.csv`: two published values from McCarthy et al. (2026), arXiv:2512.17243 v3, shown for comparison.
