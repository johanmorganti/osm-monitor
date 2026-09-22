# TODO — known issues & deferred design work

Index only — one line per item, newest/most-relevant first within each section. Full reasoning,
evidence, and remaining steps live in `docs/todo/<slug>.md`; see `CLAUDE.md`'s "TODO.md stays an
index" section for the convention. Keep this current: when something here gets fixed, delete the
line (and its file, or fold the resolution into `CLAUDE.md` if it's worth remembering *why*); when
something new is deferred, add a line + file rather than letting it live only in conversation
history.

## Known issues (deferred)

- [DB crash-restarting recurrently](docs/todo/db-crash-instability.md) — one trigger fixed (parallel-worker `/dev/shm` exhaustion, now guarded at the role level), but crashes continued after that fix; likely general host capacity pressure, not root-caused yet.
- [Cross-dimension queries + CAgg cleanup](docs/todo/continuous-aggregates-migration.md) — all 9 dimension pairs (contributor/editor/imagery/language/country) now have their own CAggs; `GeoView` with any filter (different shape) still falls back to a raw scan and can time out; old rollup tables/index still need a drop migration.
- [CAggs only cover Aug 2025+](docs/todo/cagg-history-coverage-gap.md) — raw table goes back to 2005; the 10 stats CAggs were never backfilled to match.
- [Compression backlog paused](docs/todo/compression-backlog.md) — only 1 of ~13 chunks compressed; re-enabling needs a controlled one-chunk pass and a `compress_segmentby` decision.
- [Gunicorn gthread — unverified under real traffic](docs/todo/gunicorn-gthread.md) — parked until there's real load to check against.
- [Filter dropdown pre-population not working](docs/todo/dashboard-filter-dropdown-prepopulation.md) — not yet root-caused.
- [Poller SequenceState checkpoint granularity](docs/todo/sequencestate-write-amplification.md) — checkpoints every sequence; every-N would cut write count but changes crash-recovery granularity, needs its own decision.
- Datadog log pipeline severity remapping — Postgres `LOG:` lines showing as `status:error` in Datadog. Cosmetic; user fixing directly in the Datadog UI.
- Favicon — dashboard has none; needs an actual design.

## Planned work

- [Dashboard: new graph/section ideas](docs/todo/dashboard-new-graphs.md) — hashtags/campaign toplist, StreetComplete quest breakdown, discussion activity, new-vs-returning contributors. Geo map and per-country breakdown are done.
