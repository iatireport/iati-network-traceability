# CC-06. Indicator 4 (counterpart identifiers that match an IATI publisher) in 2025 against 2024,
# broken down by reporting publisher. Sourced by working_paper.qmd (after figures.R), which
# records the numbers and draws the figure; this script also writes the check document
# docs/indicator4_2025_check.md and the full table figures/wp/indicator4_2025_by_publisher.csv.
#
# Method. Indicator 4 for all publishers is R = N / D = sum over publishers of n_p / D. So the
# change from 2024 to 2025 is exactly the sum of each publisher's contribution
#   c_p = 100 * (n_p,2025 / D_2025 - n_p,2024 / D_2024)   (percentage points).
# Each contribution splits exactly into
#   mix  = (s_p,2025 - s_p,2024) * r_p,2024   (its share of the denominator changed)
#   rate = s_p,2025 * (r_p,2025 - r_p,2024)   (its own rate changed)
# where s_p = d_p / D is its share of the denominator and r_p = n_p / d_p its rate (0 if d_p = 0).
# Input: indicators_by_publisher.csv (stage 5), organisations.parquet (names).

i4_years <- c(from = "all_2024", to = "all_2025")
bp4 <- rd("indicators_by_publisher.csv") |> filter(indicator == "4", scope %in% i4_years)
org_nm <- rd_pq("organisations.parquet", col_select = c(org_ref, org_name))
i4 <- bp4 |> select(scope, reporting_org_ref, publisher_group, numerator, denominator) |>
  mutate(scope = names(i4_years)[match(scope, i4_years)]) |>
  pivot_wider(names_from = scope, values_from = c(numerator, denominator), values_fill = 0)
i4_tot <- i4 |> summarise(N_from = sum(numerator_from), D_from = sum(denominator_from), N_to = sum(numerator_to), D_to = sum(denominator_to))
# check against indicators.csv
stopifnot(abs(100 * i4_tot$N_to / i4_tot$D_to - g("4", i4_years[["to"]])$percent) < 0.06,
          abs(100 * i4_tot$N_from / i4_tot$D_from - g("4", i4_years[["from"]])$percent) < 0.06)
i4 <- i4 |>
  mutate(rate_from = if_else(denominator_from > 0, numerator_from / denominator_from, 0),
         rate_to = if_else(denominator_to > 0, numerator_to / denominator_to, 0),
         s_from = denominator_from / i4_tot$D_from, s_to = denominator_to / i4_tot$D_to,
         contrib = 100 * (numerator_to / i4_tot$D_to - numerator_from / i4_tot$D_from),
         mix = 100 * (s_to - s_from) * rate_from, rate = 100 * s_to * (rate_to - rate_from),
         d_num = numerator_to - numerator_from, d_den = denominator_to - denominator_from) |>
  left_join(org_nm, by = c(reporting_org_ref = "org_ref")) |>
  mutate(org_name = clean_name(coalesce(org_name, reporting_org_ref)))
stopifnot(abs(sum(i4$contrib) - sum(i4$mix) - sum(i4$rate)) < 1e-9)
i4_change <- sum(i4$contrib)
# The 20 publishers with the largest change in their numerator or denominator (as asked)
i4_top20 <- i4 |> mutate(size = pmax(abs(d_num), abs(d_den))) |> slice_max(size, n = 20, with_ties = FALSE)
# Concentration: publishers ranked by the size of their contribution
i4_rank <- i4 |> arrange(desc(abs(contrib))) |> mutate(k = row_number(), cum = cumsum(contrib))
i4_k <- \(k) sum(i4_rank$contrib[seq_len(k)])
i4_neg <- i4 |> filter(contrib < 0)
i4_summary <- list(
  rate_from = 100 * i4_tot$N_from / i4_tot$D_from, rate_to = 100 * i4_tot$N_to / i4_tot$D_to, change = i4_change,
  top4 = i4_k(4), top10 = i4_k(10), top20 = i4_k(20),
  mix = sum(i4$mix), rate = sum(i4$rate),
  n_pub = nrow(i4), n_neg = nrow(i4_neg), n_pos = sum(i4$contrib > 0),
  D_from = i4_tot$D_from, D_to = i4_tot$D_to)
write_csv(i4 |> arrange(desc(abs(contrib))) |>
            transmute(reporting_org_ref, org_name, publisher_group,
                      numerator_2024 = numerator_from, denominator_2024 = denominator_from, rate_2024 = round(100 * rate_from, 1),
                      numerator_2025 = numerator_to, denominator_2025 = denominator_to, rate_2025 = round(100 * rate_to, 1),
                      contribution_points = round(contrib, 3), mix_points = round(mix, 3), rate_points = round(rate, 3)),
          file.path(wp_dir, "indicator4_2025_by_publisher.csv"), na = "")

# ---- check document -----------------------------------------------------------------
pp <- \(x, d = 2) formatC(x, format = "f", digits = d)
row_md <- \(r) paste0("| ", r$org_name, " | ", r$publisher_group, " | ", fmt(r$numerator_from), " / ", fmt(r$denominator_from), " (", pct_lab(100 * r$rate_from), ") | ",
                      fmt(r$numerator_to), " / ", fmt(r$denominator_to), " (", if (r$denominator_to > 0) pct_lab(100 * r$rate_to) else "none", ") | ",
                      pp(r$contrib), " | ", pp(r$mix), " | ", pp(r$rate), " |")
tab_md <- \(d) c("| Publisher | Type | 2024: matched / named (rate) | 2025: matched / named (rate) | Contribution (points) | of which mix | of which rate |",
                 "|---|---|---:|---:|---:|---:|---:|", vapply(seq_len(nrow(d)), \(i) row_md(d[i, ]), ""))
top_names <- \(k) paste(i4_rank$org_name[seq_len(k)], collapse = ", ")
# "A few publishers" if the four largest contributions make up half or more of the change
concentrated <- abs(i4_summary$top4) / abs(i4_change) >= 0.5
# Publishers among the ten largest contributions whose share of the denominator fell (mix < 0), by type
mix10 <- head(i4_rank, 10) |> filter(mix < 0)
mix10_types <- count(mix10, publisher_group, sort = TRUE) |> with(paste0(n, " ", publisher_group, collapse = ", "))
us10 <- mix10 |> filter(str_detect(reporting_org_ref, "^US-")) |> pull(org_name)
# Headline on mix against rate: "mainly" if one part is two thirds or more of the change
mix_share <- i4_summary$mix / i4_change
mix_head <- if (mix_share >= 2 / 3) "It is mainly a change in who reports, not in how well they identify counterparts." else
  if (mix_share <= 1 / 3) "It is mainly a change in how well publishers identify counterparts, not in who reports." else
  "Both matter: who reports in 2025, and how well publishers identify their counterparts."
rate3 <- i4 |> arrange(rate) |> head(3) |> with(paste0(org_name, " ", pp(rate), collapse = "; "))
doc <- c(
  "# Indicator 4 in 2025: which publishers explain the fall (CC-06)",
  "",
  "Written by `report/indicator4_2025_check.R` when `report/working_paper.qmd` is rendered. Do not edit by hand.",
  "",
  paste0("Indicator 4 (counterpart identifiers that match an IATI publisher; Table 4, transaction level, Section 5.2; the 'matched' level of Table 2) is ",
         pct_lab(i4_summary$rate_to), " for transactions dated 2025, against ", pct_lab(i4_summary$rate_from), " for 2024: a change of ", pp(i4_change, 1), " percentage points. ",
         "Unit: transactions. Source: `indicators_by_publisher.csv` (stage 5), which adds up exactly to `indicators.csv`."),
  "",
  "## Method",
  "",
  "Indicator 4 for all publishers is the sum, over publishers, of each publisher's matched transactions divided by all transactions in the denominator. The change between years is therefore exactly the sum of each publisher's contribution (its matched transactions as a share of the 2025 denominator, minus the same for 2024). Each contribution splits exactly into a **mix** part (the publisher's share of the denominator changed, valued at its 2024 rate) and a **rate** part (its own rate changed, weighted by its 2025 share).",
  "",
  "## Result",
  "",
  paste0("**", if (concentrated) "The fall comes from a few publishers." else "The fall is general.", "** (Rule: a few publishers if the four largest contributions make up half or more of the change.) ",
         "The four publishers with the largest contributions (", top_names(4), ") account for ", pp(i4_summary$top4, 1), " points of the ", pp(i4_change, 1), "-point change (",
         pct_lab(100 * i4_summary$top4 / i4_change), "); the ten largest for ", pp(i4_summary$top10, 1), " points. ",
         fmt(i4_summary$n_neg), " of ", fmt(i4_summary$n_pub), " publishers contribute to the fall and ", fmt(i4_summary$n_pos), " offset it."),
  "",
  paste0("**", mix_head, "** Summed over all publishers, the mix part is ", pp(i4_summary$mix, 1), " points (", pct_lab(100 * i4_summary$mix / i4_change),
         " of the change) and the rate part ", pp(i4_summary$rate, 1), " points (", pct_lab(100 * i4_summary$rate / i4_change), "). The largest contributions are mostly mix; the rate part is spread over many publishers ",
         "(largest rate parts: ", rate3, "). The denominator fell from ", fmt(i4_summary$D_from), " transactions (2024) to ", fmt(i4_summary$D_to), " (2025). ",
         "Of the ten largest contributions, ", nrow(mix10), " come from publishers whose share of the denominator fell (", mix10_types, "); these are publishers whose counterparts are usually IATI publishers, with far fewer transactions dated 2025 than 2024. ",
         "This is not the pattern of a large publisher adding many transactions with non-publisher counterparts. ",
         "Some of it may be reporting lag (a publisher that has not yet published all its 2025 transactions; see CC-10)",
         if (length(us10)) paste0(", and some may be real reductions in funding (US agencies among them: ", paste(us10, collapse = ", "), ")") else "", "."),
  "",
  "### The ten largest contributions to the change",
  "",
  tab_md(head(i4_rank, 10)),
  "",
  "### The 20 publishers with the largest change in numerator or denominator",
  "",
  "As asked: ranked by the larger of the change in their numerator (matched transactions) and in their denominator (transactions naming a counterpart identifier, not internal).",
  "",
  tab_md(i4_top20),
  "",
  "The full table for all publishers is `report/figures/wp/indicator4_2025_by_publisher.csv`.")
writeLines(doc, "../docs/indicator4_2025_check.md")
