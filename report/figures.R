# Shared code for the working paper figures (report/working_paper.qmd).
#
# Builds on what already exists: it runs the setup chunks of full_run.qmd, as
# chapter6_draft.qmd does, so the working paper uses the same data readers (rd()),
# helpers, colours and Equal Earth map base as the pipeline report and the first draft.
# Each working paper figure starts from its existing chunk in full_run.qmd (run it with
# run_chunk(), or copy and edit it) and changes only what its task asks.
# Below the setup are the few additions the working paper rules need.

# ---- reuse full_run.qmd -------------------------------------------------------------
# run_chunk(label, edits, file): runs one chunk of full_run.qmd (or of another report
# file, e.g. chapter6_draft.qmd). `edits` replaces text in the chunk first (same
# mechanism as run() in chapter6_draft.qmd).
run_chunk <- function(label, edits = list(), file = "full_run.qmd") {
  src <- readLines(file)
  start <- grep(paste0("^```\\{r ", label, "[ ,}]"), src)
  stopifnot(length(start) == 1)
  end <- start + which(src[(start + 1):length(src)] == "```")[1]
  code <- src[(start + 1):(end - 1)]
  for (k in names(edits)) code <- gsub(k, edits[[k]], code, fixed = TRUE)
  eval(parse(text = code[!grepl("^#\\|", code)]), envir = globalenv())
}
# setup: rd() (reads "NA" as Namibia), indicators, fmt(), g(), ev_col
# spatial-setup: Equal Earth crs (ee), world map, country indicators
# geo-setup: example chains, clean_name(), role_col
# flows-setup: country flows
for (ch in c("setup", "summary-numbers", "spatial-setup", "geo-setup", "flows-setup")) invisible(run_chunk(ch))

# ---- colours: the existing ones, collected in one place ------------------------------
# The pipeline report already uses one set of colour slots (blue, orange, green, purple,
# gold, grey) for publisher type (first-draft Figure 6.1), funder country (flow options)
# and role in chain (role_col). They are gathered here so every working paper figure takes
# the same colour for the same category.
pub_group_lev <- c("Government", "Multilateral", "NGO", "Private sector", "Other")
pub_group_col <- c(Government = "#2a78d6", Multilateral = "#e8743b", NGO = "#2f9e6e",
                   `Private sector` = "#8a63c9", Other = "grey55")
# Same grouping of IATI organisation types as first-draft Figure 6.1
pub_group <- function(org_type) {
  t <- coalesce(org_type, "")
  factor(case_when(t == "Government" ~ "Government", t == "Multilateral" ~ "Multilateral",
                   str_detect(t, "NGO") ~ "NGO", str_detect(t, "Private") ~ "Private sector",
                   TRUE ~ "Other"), pub_group_lev)
}
# Funder country: as funder_cols / b_cols in the flow options
funder_col <- c(`United Kingdom` = "#2a78d6", Netherlands = "#e8743b", `United States` = "#2f9e6e",
                Sweden = "#8a63c9", Denmark = "#c7a100", `Other national funders` = "grey45")
# Link evidence (ev_col) and role in chain (role_col) come from full_run.qmd unchanged.

# ---- additions the working paper rules need ---------------------------------------
rd_pq <- function(f, ...) read_parquet(file.path(out, f), ...)
# Percentages always with one decimal (Fix 16), e.g. "52.0%"; "8.1% (47 of 581)"
pct_lab <- function(x, d = 1) paste0(formatC(x, format = "f", digits = d), "%")
pct_n_lab <- function(pct, num, den) paste0(pct_lab(pct), " (", fmt(num), " of ", fmt(den), ")")
# Organisation names cleaned and wrapped, never truncated (replaces short_name() in figures)
wrap_name <- function(x, width = 28) str_wrap(clean_name(x), width)
# No titles inside the image: captions go in the paper (captions.md)
no_title <- theme(plot.title = element_blank(), plot.subtitle = element_blank())

# save_fig(): PNG (300 dpi) and SVG in figures/wp/, name starting with the working paper
# figure number (e.g. "fig05_link_quality_by_type"); the caption (unit and years) goes
# to captions.md.
wp_dir <- "figures/wp"
dir.create(wp_dir, recursive = TRUE, showWarnings = FALSE)
.captions <- list(); .notes <- list()
# note: optional "Note for the text" (e.g. a paragraph the paper still needs), listed under the caption
save_fig <- function(p, name, caption, width = 6.5, height = 4, note = NULL) {
  stopifnot(str_detect(name, "^fig"), nzchar(caption))
  p <- p & no_title
  ggsave(file.path(wp_dir, paste0(name, ".png")), p, width = width, height = height, dpi = 300, bg = "white")
  ggsave(file.path(wp_dir, paste0(name, ".svg")), p, width = width, height = height, bg = "white")
  .captions[[name]] <<- caption
  if (!is.null(note)) .notes[[name]] <<- note
  invisible(p)
}
write_captions <- function() {
  txt <- c("# Working paper figure captions", "", "Written by report/working_paper.qmd. Do not edit by hand.", "")
  for (n in names(.captions)) {
    txt <- c(txt, paste0("## ", n), "", .captions[[n]], "")
    if (!is.null(.notes[[n]])) txt <- c(txt, paste0("**Note for the text.** ", .notes[[n]]), "")
  }
  writeLines(txt, file.path(wp_dir, "captions.md"))
}

# wp_num(): one number the paper quotes. scope: "all years", "2023", "2024", "2025" or a
# country code. source_file: the pipeline output it was read from.
.wp_numbers <- tibble(key = character(), value = character(), unit = character(),
                      scope = character(), source_file = character(), description = character())
wp_num <- function(key, value, unit, scope, source_file, description) {
  stopifnot(!key %in% .wp_numbers$key)
  .wp_numbers <<- add_row(.wp_numbers, key = key, value = as.character(value), unit = unit,
                          scope = scope, source_file = source_file, description = description)
  invisible(value)
}
write_wp_numbers <- function() write_csv(.wp_numbers, file.path(wp_dir, "wp_numbers.csv"), na = "")

# ---- chain diagrams (CC-08) ------------------------------------------------------
# Builds on chain_row() of full_run.qmd (fig-longest): one node per organisation, arrows
# coloured by link evidence (ev_col, the same in every chain figure). Added: full names
# (wrapped, never truncated), country of registration and IATI organisation type under each
# name, the completeness points of each step, and a marker on steps between affiliates.

# Affiliates of one organisation (documented rule, listed in the caption by affiliate_rule):
# the IATI Registry has no publisher-group field in the pipeline's copy, so names are compared.
affiliate_rule <- paste0("'Affiliates' under an arrow: the two organisations are separately registered branches of the same organisation, ",
                         "often in different countries (for example IRC-UK and the International Rescue Committee Inc. in the United States; Concern Worldwide UK and Concern Worldwide in Ireland). ",
                         "Funding between them is a real transfer, but not a step towards a different, local organisation. They are found by name, because the data hold no field for this: ",
                         "the names are the same after dropping country and legal-form words (UK, Inc., Ltd, Limited, USA, GB), or they share a distinctive first word, ",
                         "or one name starts with an acronym of the other (IRC for International Rescue Committee).")
.aff_drop <- c("uk", "inc", "ltd", "limited", "usa", "us", "gb", "the")
.aff_generic <- c("international", "national", "ministry", "department", "directorate", "european", "united", "world", "global", "the",
                  "de", "stichting", "foundation", "fund", "agency", "office", "government", "federal", "royal", "uk", "us", "un", "african",
                  "asian", "development", "health", "women", "children", "community", "association", "society", "institute", "centre", "center")
.aff_words <- function(x) str_split(str_squish(str_to_lower(str_replace_all(clean_name(x), "[^A-Za-z0-9 ]", " "))), " ")[[1]]
is_affiliate <- function(a, b) {
  wa <- .aff_words(a); wb <- .aff_words(b)
  same <- identical(setdiff(wa, .aff_drop), setdiff(wb, .aff_drop))
  first <- wa[1] == wb[1] && !(wa[1] %in% .aff_generic) && nchar(wa[1]) >= 4
  acr <- function(x, y) { t <- str_match(clean_name(x), "^([A-Z]{2,6})\\b")[, 2]
    w <- setdiff(.aff_words(y), c(.aff_drop, "of", "and", "for")); !is.na(t) && length(w) >= 2 && t == str_to_upper(paste(substr(w, 1, 1), collapse = "")) }
  same || first || acr(a, b) || acr(b, a)
}

# Organisation facts used under each node
.org_facts <- function() {
  if (!exists(".org_facts_cache", envir = globalenv())) {
    on <- rd_pq("organisations.parquet", col_select = c(org_ref, org_name, org_type_label))
    oc <- rd("org_countries.csv") |> select(org_ref, org_country)
    assign(".org_facts_cache", left_join(on, oc, by = "org_ref"), envir = globalenv())
  }
  get(".org_facts_cache", envir = globalenv())
}

# Steps of one chain (chain_steps.csv), or of the best chain on an organisation path
# (org_paths.csv; the chain with the highest completeness, then most confirmed steps)
chain_steps_of <- function(chain_id) {
  st <- rd("chain_steps.csv") |> filter(chain_id == !!chain_id) |> arrange(step)
  stopifnot(nrow(st) > 0)
  st
}
path_steps_of <- function(org_path_id) {
  p <- rd("org_paths.csv") |> filter(org_path_id == !!org_path_id)
  ch <- rd("chains.csv") |> filter(path_org_refs == p$path_org_refs) |> arrange(desc(completeness), desc(n_confirmed_steps), chain_id)
  chain_steps_of(ch$chain_id[1])
}

# Horizontal chain diagram from a steps table (columns from_org, to_org, evidence, and
# optionally step_points). stop_note: text drawn after the last organisation (e.g. why the
# chain stops). name_width: characters per line of the wrapped names.
# fill_levels: publisher type groups to show in the legend even if absent (for figures that
# share one legend across several diagrams).
chain_diagram <- function(st, stop_note = NULL, name_width = 18, text_size = 2.3, fill_levels = NULL) {
  f <- .org_facts()
  refs <- c(st$from_org[1], st$to_org)
  nodes <- tibble(x = seq_along(refs) - 1, org_ref = refs) |>
    left_join(f, by = "org_ref") |>
    mutate(name = coalesce(org_name, org_ref), grp = pub_group(org_type_label),
           lab = paste0(wrap_name(name, name_width)), sub = paste0(coalesce(org_country, "country unknown"), "\n", coalesce(org_type_label, "type unknown")))
  arr <- st |> mutate(x0 = step - 1, x1 = step, ev = factor(evidence, names(ev_col)),
                      aff = mapply(is_affiliate, nodes$name[step], nodes$name[step + 1]),
                      pts = if ("step_points" %in% names(st)) paste0(step_points, "/3") else NA_character_)
  ev_lab <- c(confirmed = "Confirmed (both sides report)", `one-sided` = "One side reports", implausible = "Implausible")
  p <- ggplot() +
    geom_segment(data = arr, aes(x = x0 + 0.09, xend = x1 - 0.09, y = 0, yend = 0, colour = ev), linewidth = 1.3,
                 arrow = arrow(length = unit(2.2, "mm"), type = "closed")) +
    geom_point(data = nodes, aes(x, 0, fill = grp), shape = 21, size = 4.2, colour = "white", stroke = 0.6) +
    geom_text(data = nodes, aes(x, -0.12, label = lab), vjust = 1, size = text_size, lineheight = 0.9, fontface = "bold") +
    # the country and type start on the line after the name (blank lines push them down)
    geom_text(data = nodes, aes(x, -0.12, label = paste0(strrep("\n", str_count(lab, "\n") + 1), sub)), vjust = 1, size = text_size,
              lineheight = 0.9, colour = "grey35") +
    # zero-length segments so the evidence legend always shows all three colours
    geom_segment(data = tibble(ev = factor(names(ev_col), names(ev_col))), aes(x = 0, xend = 0, y = 0, yend = 0, colour = ev), linewidth = 0)
  if (any(!is.na(arr$pts))) p <- p + geom_text(data = arr, aes(x = (x0 + x1) / 2, y = 0.09, label = pts), size = text_size * 0.95, colour = "grey20")
  if (any(arr$aff)) p <- p + geom_text(data = filter(arr, aff), aes(x = (x0 + x1) / 2, y = -0.05, label = "affiliates"), size = text_size * 0.95, fontface = "italic", colour = "grey20")
  if (!is.null(fill_levels)) p <- p + geom_point(data = tibble(grp = factor(fill_levels, levels(nodes$grp))), aes(x = 0, y = 0, fill = grp),
                                                 shape = 21, size = 0, alpha = 0, show.legend = TRUE)
  if (!is.null(stop_note)) p <- p +
    annotate("segment", x = max(nodes$x) + 0.1, xend = max(nodes$x) + 0.42, y = 0, yend = 0, colour = "#c0392b", linetype = "22", linewidth = 0.9) +
    annotate("text", x = max(nodes$x) + 0.45, y = 0, hjust = 0, size = text_size, colour = "#c0392b", label = str_wrap(stop_note, 22), lineheight = 0.88)
  p + scale_colour_manual(values = ev_col, labels = ev_lab, name = "Link evidence", drop = FALSE) +
    scale_fill_manual(values = pub_group_col, name = "Publisher type") +
    scale_x_continuous(limits = c(-0.55, max(nodes$x) + if (is.null(stop_note)) 0.55 else 1.35)) +
    scale_y_continuous(limits = c(-0.42, 0.16)) +
    theme_void(base_size = 7.5) + theme(legend.position = "bottom", legend.box = "vertical", legend.spacing.y = unit(0.5, "mm"),
                                       legend.margin = margin(0, 0, 0, 0), legend.key.width = unit(6, "mm")) +
    guides(fill = guide_legend(override.aes = list(size = 3, alpha = 1), order = 2), colour = guide_legend(override.aes = list(linewidth = 1.2), order = 1))
}
chain_caption_key <- function(with_points = TRUE) paste0(
  "Each circle is an organisation, coloured by publisher type group (as in Figures 4 and 5), with its full name, country of registration and IATI organisation type. ",
  "Arrows: activity links, coloured by evidence (blue: confirmed by both funder and receiver; green: reported by one side; orange: implausible), the same colours in every chain figure. ",
  if (with_points) paste0("The fraction above each arrow (1/3, 2/3 or 3/3) counts how many of three pieces of information the link has: ",
                          "(1) the funder's payment record gives the receiving organisation's identifier; (2) it gives the identifier of the receiver's activity (project), which the receiver publishes; ",
                          "(3) the receiver also reports the money coming in, naming the funder's activity. 3/3 means all three; 1/3 usually means only the receiver reports the link. ",
                          "These are the points of the chain completeness score (tentative indicator 6f, which may be removed). ") else "",
  affiliate_rule, " ")
