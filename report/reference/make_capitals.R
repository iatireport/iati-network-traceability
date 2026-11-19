# National capitals for the location check (CC-13), from Natural Earth 1:50m populated
# places (the same source as the country borders). Run from report/reference/:
#   Rscript make_capitals.R
# Writes ne_capitals.csv: one row per capital point (iso2, name, lon, lat, featurecla).
# Countries with more than one capital (e.g. Bolivia, South Africa) keep all of them
# ("Admin-0 capital" and "Admin-0 capital alt").
suppressMessages({ library(rnaturalearth); library(sf); library(dplyr); library(readr) })
pp <- ne_download(scale = 50, type = "populated_places", category = "cultural", returnclass = "sf", destdir = tempdir())
names(pp) <- tolower(names(pp))
cap <- pp |> filter(featurecla %in% c("Admin-0 capital", "Admin-0 capital alt")) |>
  mutate(iso2 = if_else(iso_a2 == "-99", NA_character_, iso_a2), lon = st_coordinates(geometry)[, 1], lat = st_coordinates(geometry)[, 2]) |>
  st_drop_geometry() |> transmute(iso2, name, lon = round(lon, 5), lat = round(lat, 5), featurecla) |> filter(!is.na(iso2)) |> arrange(iso2, name)
write_csv(cap, "ne_capitals.csv", na = "")
cat(nrow(cap), "capital points for", n_distinct(cap$iso2), "countries\n")
