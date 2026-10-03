# TODO — known issues & deferred design work

Index only — one line per item, newest/most-relevant first within each section. Full reasoning,
evidence, and remaining steps live in `docs/todo/<slug>.md`; see `CLAUDE.md`'s documentation rules for the convention. Keep this current: when something here gets fixed, delete the
line (and its file, or fold the resolution into `docs/decisions/` if it's worth remembering *why*); when
something new is deferred, add a line + file rather than letting it live only in conversation
history.

## Known issues (deferred)

- [Some changesets miss their final update](docs/todo/stale-changesets.md) — 2 of ~2M stored as open with too small a `changes_count` (found by the object diffs, which also give a detector).
- [Phase out TimescaleDB](docs/todo/timescale-deprecation.md) — ClickHouse is the default backend and primary writer; Timescale is deprecated, still written to as a fallback. Steps to stop writing, drop the schema and move to plain Postgres (which stays for app state).
- [Cross-dimension queries + CAgg cleanup](docs/todo/continuous-aggregates-migration.md) — all 9 dimension pairs (contributor/editor/imagery/language/country) now have their own CAggs; `GeoView` with any filter (different shape) still falls back to a raw scan and can time out; old rollup tables/index still need a drop migration.
- [Existence check spans old chunks](docs/todo/existence-check-wide-range.md) — a batch mixing today's changesets with an old one (comment-driven) makes the Timescale writer's existence check scan every chunk in between.
- [Filter dropdown pre-population not working](docs/todo/dashboard-filter-dropdown-prepopulation.md) — not yet root-caused.
- [Poller SequenceState checkpoint granularity](docs/todo/sequencestate-write-amplification.md) — checkpoints every sequence; every-N would cut write count but changes crash-recovery granularity, needs its own decision.
- Favicon — dashboard has none; needs an actual design.

## Planned work

- [Object changes: upkeep and longer ranges](docs/todo/object-changes.md) — built and backfilled (92 days, every closed changeset's counts equal `changes_count`); next: bound the rollup refresh, try codecs on `object_versions` (~26 bytes per version), maybe edits over longer ranges. Tag changes wait for the full history planet.
- [Widest changesets: find a list worth showing](docs/todo/widest-changesets.md) — paused: the area-ranked table was removed (all continent-sized, mostly ordinary edits); a threshold list was too slow on full history and empty for reasonable contributors; next idea is a ranking by area per object.
- [Dashboard: new graph/section ideas](docs/todo/dashboard-new-graphs.md) — hashtag filter, Objects page speed over multi-year ranges, StreetComplete quest breakdown, discussion activity, new-vs-returning contributors. Geo map, per-country breakdown, Objects page and hashtag toplist are done.
