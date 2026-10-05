# imagery_used stored as JSON array

> **2026-10-05:** TimescaleDB and Postgres were removed ([timescale-removal.md](timescale-removal.md)); what this says about them (CAggs, the hypertable, Postgres queries) is history, the rest still holds.

`Changeset.imagery_used` is a `JSONField` holding a list of strings (e.g. `["Bing", "Mapbox"]`).
Filtering (`imagery_raw` on `ChangesetQueryView`) uses `__contains`, which on Postgres compiles to
`jsonb`'s native `@>` containment operator — a real DB-level query, not Python-side filtering.
