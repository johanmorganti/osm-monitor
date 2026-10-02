# Stats split into timeseries/summary/toplist, not one bundled endpoint

`TimeseriesView`/`SummaryView`/`ToplistView` replaced a single `ChangesetStatsView` that returned
everything (daily counts, every top-N breakdown, object totals) in one bundled response. Split by
resource *shape* (analogous to Datadog's widget types), not by business concept: `timeseries`
handles anything date-bucketed (plain volume via `group_by=none`, or per-name series via
`group_by=editor|imagery|language|contributor`), `toplist` handles any ranked list
(`dimension` × `metric=count|objects`), `summary` is the handful of single-number KPIs. This
means a `contributor`×`count` or `language`×`objects` toplist — combinations the old bundled
endpoint never exposed — are just other parameter values on the same endpoint, not new code.
Shared filter-resolution logic lives in `changesets/api/params.py` (`resolve_filters`, `pick_interval`)
and the backend's query helpers (`filtered_changesets`, `DIMENSION_FIELDS`) rather than being
duplicated per view. Trade-off: the dashboard now makes ~10 parallel requests to render instead
of 1 — acceptable since they're fetched concurrently (bounded by the slowest, not the sum) and
each is now independently small/cacheable, but a real cost avoided if the "aggregate everything"
endpoint had stayed.

**Widgets load lazily (2026-10-02).** `loadWidget` (`static/js/common.js`) only fetches once a
widget's card is within 400px of the viewport (`whenNearViewport`, an `IntersectionObserver`), so
a page load costs what's on screen, not every widget: first paint went from 11 to 2 calls on
Overview, 10 to 2 on Objects, 28 to 10 on Editors (36 when fully scrolled). Use `{ eager: true }`
only when a widget's response also fills something above the fold (the Objects KPIs). This, not
more gunicorn threads, was the lever: measured with ClickHouse, a full Overview load is bound by
ClickHouse CPU (each query uses all 4 cores), not by request slots (see `entrypoint.sh`).
