# imagery_used stored as JSON array

`Changeset.imagery_used` is a `JSONField` holding a list of strings (e.g. `["Bing", "Mapbox"]`).
Filtering (`imagery_raw` on `ChangesetQueryView`) uses `__contains`, which on Postgres compiles to
`jsonb`'s native `@>` containment operator — a real DB-level query, not Python-side filtering.
