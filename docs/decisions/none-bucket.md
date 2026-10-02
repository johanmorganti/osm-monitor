# NULL-tag volume gets its own "(none)" bucket, never silently excluded

`cagg_imagery_daily`/`cagg_locale_daily` (and any future per-dimension CAgg) group untagged rows
under a real `COALESCE(<field>, '(none)')` bucket rather than filtering them out with `WHERE
<field> IS NOT NULL`. A large campaign that never sets a given tag (e.g. imagery) still counts
toward total volume and must not vanish from that dimension's breakdown just because it left one
field blank — a `WHERE ... IS NOT NULL` CAgg silently drops that volume from its own chart while
`SummaryView`'s total keeps counting it, which reads as a data bug from the dashboard rather than a
query choice. The Timescale backend's `filtered_changesets` special-cases the `NONE_BUCKET = '(none)'`
sentinel value to filter on `<field>__isnull=True` (not `__iexact='(none)'`) so clicking that
bucket matches the real NULL rows; `dashboard.js` styles it with the same neutral gray as "Other"
rather than a random hue. `cagg_editor_daily`/`cagg_contributor_daily` don't need this —
`created_by_family`/`user` are essentially always populated in practice — but any *new*
per-dimension CAgg on a field that can legitimately be blank should use this pattern from the
start rather than adding it as a fix later.
