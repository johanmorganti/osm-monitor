# TODO — known issues & deferred design work

Index only — one line per item, newest/most-relevant first within each section. Full reasoning,
evidence, and remaining steps live in `docs/todo/<slug>.md`; see `CLAUDE.md`'s documentation rules for the convention. Keep this current: when something here gets fixed, delete the
line (and its file, or fold the resolution into `docs/decisions/` if it's worth remembering *why*); when
something new is deferred, add a line + file rather than letting it live only in conversation
history.

## Known issues (deferred)

- [Phase out TimescaleDB](docs/todo/timescale-deprecation.md) — ClickHouse is the default backend and primary writer; Timescale is deprecated, still written to as a fallback. Steps to stop writing, drop the schema and move to plain Postgres (which stays for app state).
- [Existence check spans old chunks](docs/todo/existence-check-wide-range.md) — a batch mixing today's changesets with an old one (comment-driven) makes the Timescale writer's existence check scan every chunk in between.
- [Poller SequenceState checkpoint granularity](docs/todo/sequencestate-write-amplification.md) — checkpoints every sequence; every-N would cut write count but changes crash-recovery granularity, needs its own decision.
- [Favicon](docs/todo/favicon.md) — the site has none; needs a design, then the icon files and a `<link rel="icon">` in the page templates.

## Planned work

- [Object changes: longer ranges, window](docs/todo/object-changes.md) — built, backfilled and checked (every closed changeset's counts equal `changes_count`); open: edits over ranges longer than a week, the 92-day window.
- [Widest changesets: find a list worth showing](docs/todo/widest-changesets.md) — paused: the area-ranked table was removed (all continent-sized, mostly ordinary edits); a threshold list was too slow on full history and empty for reasonable contributors; next idea is a ranking by area per object.
- [Decide what the README should be](docs/todo/readme-shape.md) — it overlaps with ARCHITECTURE, DEPLOYMENT and CLAUDE.md; decide its audience and what it keeps.
- [Dashboard: new graph/section ideas](docs/todo/dashboard-new-graphs.md) — hashtag filter, Objects page speed over multi-year ranges, StreetComplete quest breakdown, discussion activity, new-vs-returning contributors. Geo map, per-country breakdown, Objects page and hashtag toplist are done.
