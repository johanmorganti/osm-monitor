# TODO — known issues & deferred design work

Index only — one line per item, newest/most-relevant first within each section. Full reasoning,
evidence, and remaining steps live in `docs/todo/<slug>.md`; see `CLAUDE.md`'s documentation rules for the convention. Keep this current: when something here gets fixed, delete the
line (and its file, or fold the resolution into `docs/decisions/` if it's worth remembering *why*); when
something new is deferred, add a line + file rather than letting it live only in conversation
history.

## Known issues (deferred)

- [Contributor queries: contributor-ordered data](docs/todo/contributor-queries.md) — the unfiltered contributor ranking over full history still takes ~19 s (a contributor-ordered rollup or projection, keyed on `user_id`; check projections under `FINAL` first).
- [Datadog: ingestion-drop monitor](docs/todo/ingestion-monitor.md) — the "Low number of changeset coming" monitor watches a retired service (`osm-to-datadog`) and is stuck in Alert; repoint it at the poller's `Processed sequence` logs, in the Datadog UI.
- [API: HTTP caching at Cloudflare](docs/todo/cloudflare-cache.md) — API responses pass through Cloudflare uncached (`DYNAMIC`, `Vary: Cookie`); cache ranges ending 2+ days ago once traffic or the 4G uplink makes it worth it (ClickHouse's query cache covers repeats for now).
- [Favicon](docs/todo/favicon.md) — the site has none; needs a design, then the icon files and a `<link rel="icon">` in the page templates.

## Planned work

- [Object changes: longer ranges, window](docs/todo/object-changes.md) — built, backfilled and checked (every closed changeset's counts equal `changes_count`); open: edits over ranges longer than a week, the 92-day window, contributors with an object filter over long ranges, a `(none)` country lag in the object rollup.
- [Widest changesets: find a list worth showing](docs/todo/widest-changesets.md) — paused: the area-ranked table was removed (all continent-sized, mostly ordinary edits); a threshold list was too slow on full history and empty for reasonable contributors; next idea is a ranking by area per object.
- [Decide what the README should be](docs/todo/readme-shape.md) — it overlaps with ARCHITECTURE, DEPLOYMENT and CLAUDE.md; decide its audience and what it keeps.
- [Dashboard: new graph/section ideas](docs/todo/dashboard-new-graphs.md) — hashtag filter, Objects page speed over multi-year ranges, StreetComplete quest breakdown, discussion activity, new-vs-returning contributors. Geo map, per-country breakdown, Objects page and hashtag toplist are done.
