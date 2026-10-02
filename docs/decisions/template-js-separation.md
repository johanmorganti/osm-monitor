# Template / JS separation, async data fetch

`dashboard.html` is a thin HTML shell only (no server-rendered chart data) — it renders
instantly and reads only `{{ request.GET.xxx }}` for filter-input defaults. All Chart.js
initialisation lives in `static/js/dashboard.js`.

**Contract:** `dashboard.js` fetches from `/api/changesets/timeseries/`, `/summary/`, and
`/toplist/` (in parallel, several calls — one per chart/KPI group; see the file's `Promise.all`)
and renders the JSON responses into the KPI numbers and charts. Those endpoints are standalone,
public JSON APIs (same query params the dashboard UI uses: `start_date`, `end_date`,
`contributor`, `editor`, `imagery`, plus `group_by`/`dimension`/`metric` depending on the
endpoint) — usable directly by anyone, not just the dashboard's own JS. `DashboardView` itself is
a bare `TemplateView` with no `get_context_data` — it does no DB access.

**Why:** when the whole chart code lived inline in the template with server-rendered
`window.dashboardData`, any edit caused Claude to rewrite the entire file and risk breaking
layout or charts, and the data was only reachable by loading the HTML page. The split means:
- Chart logic changes → edit `static/js/dashboard.js` only
- HTML/layout changes → edit `dashboard.html` only
- API params/validation/response shape → `changesets/api/`; how the data is fetched → the analytics backend (`changesets/analytics/<backend>/`), see [analytics-backends.md](analytics-backends.md)
- The aggregated data itself is a real API endpoint other tools can call directly
