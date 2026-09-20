# changesets/data/

Static reference data shipped with the app (not user data, not something the poller writes).

## country_boundaries.geojson

World country polygons with ISO 3166-1 alpha-2 codes, used by
`load_country_boundaries` (management command) to populate the
`country_boundaries` table that `Changeset.country_code`'s trigger does a
point-in-polygon lookup against.

- **Source**: [datasets/geo-countries](https://github.com/datasets/geo-countries)
  (Open Knowledge Foundation), itself Natural Earth's 1:10m admin-0
  countries. Natural Earth data is public domain.
- **Processing** (2026-09-18, see git history for the exact commands): loaded
  as-is, then `ST_SimplifyPreserveTopology(geom, 0.02)` — country-level
  aggregation doesn't need survey-grade borders, and this cuts total vertex
  count from ~547K to ~122K (~4.5x), which matters for GiST index size and
  per-row point-in-polygon cost across 17M+ changesets.
- **ISO code corrections** applied before loading (the source data has known
  gaps for these three): France and Norway were missing `ISO3166-1-Alpha-2`
  entirely (`-99`); Taiwan carried a non-standard `CN-TW` label instead of
  the real ISO 3166-1 alpha-2 code `TW`. All three corrected to their actual
  ISO codes.
- **20 features dropped** (kept out of the table entirely, not remapped to
  another country): disputed territories, military bases, and uninhabited
  rocks/reefs with no real ISO 3166-1 alpha-2 code (Somaliland, Northern
  Cyprus, Kosovo, Bir Tawil, Siachen Glacier, and similar — see git history
  for the full list). A changeset whose centroid falls in one of these
  resolves to no country match, same as one falling in international waters
  — both are the `(unknown)` bucket, not a silent misassignment.
