# Export Natural Earth country borders (1:50m, the same as the report's maps) for
# the point-in-country check in 06_spatial.py. Run once from the project folder:
#   Rscript reference/make_country_borders.R
# Writes reference/country_borders.geojson with columns iso2, name, label_lon,
# label_lat (Natural Earth's label point, used to place organisations by country).
library(sf)
library(rnaturalearth)
library(dplyr)

ne_countries(scale = "medium", returnclass = "sf") |>
  mutate(iso2 = if_else(iso_a2_eh == "-99", iso_a2, iso_a2_eh)) |>
  filter(iso2 != "-99") |>
  select(iso2, name = name_long, label_lon = label_x, label_lat = label_y) |>
  group_by(iso2) |>
  summarise(name = first(name), label_lon = first(label_lon), label_lat = first(label_lat), .groups = "drop") |>
  st_make_valid() |>
  st_write("reference/country_borders.geojson", delete_dsn = TRUE, quiet = TRUE)
cat("Natural Earth version:", as.character(packageVersion("rnaturalearthdata")), "\n")
