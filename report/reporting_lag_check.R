# CC-10. How much can a year's values change after the year ends (reporting lag)?
# Sourced by working_paper.qmd (after figures.R). Writes docs/reporting_lag_check.md and
# figures/wp/reporting_lag.csv; the .qmd records the numbers.
#
# Only one Bulk Data Service copy is available (30 October 2026; iati-data.zip is the same
# copy), so the earlier-copy comparison asked for in CC-10 cannot be made without a new
# download of about 13 GB. This script uses the fallback: it looks at the dates of the
# transactions behind each link.
#
# Lower bound of the lag for year Y (2023, 2024): a link or a confirmation that rests only on
# transactions dated after Y could not have been in the data when Y ended. The script
# recounts indicators 1, 5 and 7b for Y without them:
#   links "late": network links all of whose link transactions are dated Y+1 or later
#                 (they can still connect an activity that has a transaction dated Y);
#   confirmations "late": a confirmed link whose receiver-side link transactions are all
#                 dated Y+1 or later.
# This is a lower bound: links whose transactions are dated Y but were published after Y
# ended cannot be seen in a single copy. As context, the share of receiving activities last
# updated after Y is given; it is an upper-bound signal only, because the last-updated date
# is the latest version, not the first.
# Baselines are recomputed with the stage 5 rules and must equal indicators.csv.

lag_years <- c(2023, 2024)
edges_a <- rd_pq("activity_edges.parquet", col_select = c(from_activity, to_activity, in_network, reported_by_provider, reported_by_receiver))
ltx <- rd_pq("link_transactions.parquet", col_select = c(side, from_activity, to_activity, transaction_year))
act_l <- rd_pq("activities_clean.parquet", col_select = c(iati_identifier, reporting_org_ref, last_updated_datetime))
txy <- open_dataset(file.path(out, "transactions_clean.parquet")) |>
  filter(transaction_year %in% lag_years, !exact_duplicate) |>
  select(iati_identifier, reporting_org_ref, transaction_year, transaction_type, internal, counterpart_activity_published, self_reference, receiver_activity_id) |>
  collect()
flow_out <- c("2", "3") # config.FLOW_OUT (commitments and disbursements; INDICATOR3/5 default)

# earliest dated link transaction of each link (NA if none is dated), and earliest receiver-side one
fmin <- \(x) if (any(!is.na(x))) min(x, na.rm = TRUE) else NA_real_
edge_years <- ltx |> group_by(from_activity, to_activity) |>
  summarise(min_year = fmin(transaction_year), min_rec_year = fmin(transaction_year[side == "receiver"]), .groups = "drop")
edges_all_y <- edges_a |> left_join(edge_years, by = c("from_activity", "to_activity"))
edges_y <- filter(edges_all_y, in_network) # indicator 1 and 7b use the network; indicator 5 all links

lag_one <- function(Y) {
  tY <- filter(txy, transaction_year == Y)
  scope_acts <- unique(tY$iati_identifier)
  # indicator 1 and 7b
  ind1 <- function(e) {
    conn <- unique(c(e$from_activity, e$to_activity))
    a <- tibble(iati_identifier = scope_acts) |> mutate(connected = iati_identifier %in% conn) |>
      left_join(act_l |> select(iati_identifier, reporting_org_ref), by = "iati_identifier")
    pubs <- a |> filter(!is.na(reporting_org_ref)) |> group_by(reporting_org_ref) |> summarise(any = any(connected), .groups = "drop")
    c(n1 = sum(a$connected), d1 = nrow(a), n7b = sum(!pubs$any), d7b = nrow(pubs))
  }
  late_edge <- with(edges_y, !is.na(min_year) & min_year >= Y + 1) # every dated link transaction is after Y
  base1 <- ind1(edges_y); lb1 <- ind1(edges_y[!late_edge, ])
  # indicator 5
  d5 <- tY |> filter(as.character(transaction_type) %in% flow_out, !internal, counterpart_activity_published, !self_reference)
  conf <- edges_all_y |> filter(reported_by_receiver) |> mutate(late_conf = !is.na(min_rec_year) & min_rec_year >= Y + 1)
  d5 <- d5 |> left_join(conf |> select(iati_identifier = from_activity, receiver_activity_id = to_activity, late_conf), by = c("iati_identifier", "receiver_activity_id"))
  n5 <- sum(!is.na(d5$late_conf)); n5_lb <- sum(!is.na(d5$late_conf) & !d5$late_conf)
  # links with a transaction dated Y, and how their evidence is dated
  LY <- edges_y |> semi_join(filter(ltx, transaction_year == Y), by = c("from_activity", "to_activity"))
  conf_Y <- LY |> filter(reported_by_receiver)
  conf_late <- conf_Y |> filter(!is.na(min_rec_year), min_rec_year >= Y + 1)
  upd <- act_l |> transmute(iati_identifier, upd_year = suppressWarnings(as.integer(substr(last_updated_datetime, 1, 4))))
  LYu <- LY |> left_join(upd, by = c(to_activity = "iati_identifier"))
  tibble(year = Y,
         ind1_base = 100 * base1[["n1"]] / base1[["d1"]], ind1_lb = 100 * lb1[["n1"]] / lb1[["d1"]], ind1_num = base1[["n1"]], ind1_num_lb = lb1[["n1"]], ind1_den = base1[["d1"]],
         ind7b_base = 100 * base1[["n7b"]] / base1[["d7b"]], ind7b_lb = 100 * lb1[["n7b"]] / lb1[["d7b"]], ind7b_num = base1[["n7b"]], ind7b_num_lb = lb1[["n7b"]], ind7b_den = base1[["d7b"]],
         ind5_base = 100 * n5 / nrow(d5), ind5_lb = 100 * n5_lb / nrow(d5), ind5_num = n5, ind5_num_lb = n5_lb, ind5_den = nrow(d5),
         late_links = sum(late_edge & (edges_y$from_activity %in% scope_acts | edges_y$to_activity %in% scope_acts)),
         links_Y = nrow(LY), links_Y_confirmed = nrow(conf_Y), links_Y_conf_late = nrow(conf_late),
         conf_late_pct = 100 * nrow(conf_late) / max(nrow(conf_Y), 1),
         recv_updated_after_pct = 100 * mean(LYu$upd_year >= Y + 1, na.rm = TRUE))
}
lag <- map_dfr(lag_years, lag_one)
# Baselines must reproduce stage 5 (indicators.csv) exactly
for (Y in lag_years) {
  r <- filter(lag, year == Y); sc <- paste0("all_", Y)
  stopifnot("Lag check: indicator 1 baseline differs from indicators.csv" = r$ind1_num == g("1", sc)$numerator && r$ind1_den == g("1", sc)$denominator,
            "Lag check: indicator 7b baseline differs from indicators.csv" = r$ind7b_num == g("7b", sc)$numerator && r$ind7b_den == g("7b", sc)$denominator,
            "Lag check: indicator 5 baseline differs from indicators.csv" = r$ind5_num == g("5", sc)$numerator && r$ind5_den == g("5", sc)$denominator)
}
write_csv(lag |> mutate(across(where(is.double), \(x) round(x, 2))), file.path(wp_dir, "reporting_lag.csv"))

# ---- check document ---------------------------------------------------------------
L <- \(Y, k) lag[[k]][lag$year == Y]
pp1 <- \(x) formatC(x, format = "f", digits = 1)
row_ind <- \(code, nm, k) paste0("| ", code, " | ", nm, " | ", paste0(sapply(lag_years, \(Y) paste0(pct_lab(L(Y, paste0(k, "_base"))), " | ", pct_lab(L(Y, paste0(k, "_lb"))), " | ",
                                                                                             pp1(L(Y, paste0(k, "_lb")) - L(Y, paste0(k, "_base"))))), collapse = " | "), " |")
doc <- c(
  "# Reporting lag check (CC-10)",
  "",
  "Written by `report/reporting_lag_check.R` when `report/working_paper.qmd` is rendered. Do not edit by hand.",
  "",
  "**Question.** Indicator 7b (publishing organisations with no connected activity; Table 4, organisation level, Section 3.2) is ",
  paste0(pct_lab(g("7b", "all_2025")$percent), " for 2025 against ", pct_lab(g("7b", "all_2023")$percent), " for 2023 and ", pct_lab(g("7b", "all_2024")$percent),
         " for 2024. Links can be added after a year ends, when receivers report the funds they received. How much does a year's value change after the year ends?"),
  "",
  "**Data available.** Only one Bulk Data Service copy is in the project (indexed 30 October 2026; `iati-data.zip` holds the same copy). An earlier copy would allow the direct comparison CC-10 asks for (indicators 1, 5 and 7b for 2024 from a copy taken in early 2025), but it would have to be downloaded (about 13 GB; 24 GB of disk free on 17 November 2026). Not downloaded; ask first.",
  "",
  "**Method used instead (lower bound).** A transaction dated after a year cannot have been published before that year ended. For each year Y, the script removes the links whose link transactions are all dated Y+1 or later, and the confirmations whose receiver-side transactions are all dated Y+1 or later, and recounts indicators 1, 5 and 7b for Y with the stage 5 rules (the baselines reproduce `indicators.csv` exactly). What it cannot see: transactions dated within Y but published after Y ended. So the true lag is at least this large.",
  "",
  "## Result",
  "",
  paste0("| Code | Indicator | ", paste0(sapply(lag_years, \(Y) paste0(Y, " now | ", Y, " without later evidence | change (points)")), collapse = " | "), " |"),
  paste0("|---|---|", paste(rep("---:|---:|---:", length(lag_years)), collapse = "|"), "|"),
  row_ind("1", "Activities connected to another organisation's activity", "ind1"),
  row_ind("5", "Outgoing links confirmed by the receiver", "ind5"),
  row_ind("7b", "Publishing organisations with no connected activity", "ind7b"),
  "",
  paste0("Of the links with a transaction dated 2024 (", fmt(L(2024, "links_Y")), " links), ", fmt(L(2024, "links_Y_confirmed")), " are confirmed by the receiver; for ",
         fmt(L(2024, "links_Y_conf_late")), " of these (", pct_lab(L(2024, "conf_late_pct")), ") every confirming receiver transaction is dated 2025 or later. For 2023 the same share (dated 2024 or later) is ",
         pct_lab(L(2023, "conf_late_pct")), ". As context only: ", pct_lab(L(2024, "recv_updated_after_pct")), " of the 2024 links point to a receiving activity last updated in 2025 or later (",
         pct_lab(L(2023, "recv_updated_after_pct")), " for 2023 and later updates); the last-updated date is the latest version, so this says little about when a link first appeared."),
  "",
  "## Reading",
  "",
  paste0("The lower bound moves indicator 5 most, because receivers confirm funds after the funder reports them: without later confirmations, indicator 5 for 2024 would be ",
         pct_lab(L(2024, "ind5_lb")), " rather than ", pct_lab(L(2024, "ind5_base")), ". Indicators 1 and 7b move by ", pp1(L(2024, "ind1_lb") - L(2024, "ind1_base")), " and ",
         pp1(L(2024, "ind7b_lb") - L(2024, "ind7b_base")), " points for 2024. ",
         "The rise of 7b for 2025 (", pct_lab(g("7b", "all_2025")$percent), ") is larger than these lower bounds, so this check cannot show how much of it is lag; an earlier bulk copy is needed for that. ",
         "The paper should keep the caution that the most recent year may be revised upward, and report 2025 values as provisional."),
  "",
  "Full values: `report/figures/wp/reporting_lag.csv`.")
writeLines(doc, "../docs/reporting_lag_check.md")
