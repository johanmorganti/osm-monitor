// ── Shared dashboard/editors-page helpers ───────────────────────────────────
// Extracted from dashboard.js (pure extraction, no behavior change) so the
// Editors page can reuse the same chart factories, filter/URL helpers, and
// geo map without duplicating ~140 lines of renderGeoMap plus every chart
// factory into a second file. Loaded before dashboard.js/editors.js — no
// bundler in this project, so script-tag order is what enforces this
// dependency (both page-specific files reference these as plain globals).

// ── Design tokens ────────────────────────────────────────────────────────────
// Validated categorical palette (fixed hue order, never cycled — see the
// dataviz skill's references/palette.md). Light mode only; these pages have
// no dark theme.

const CATEGORICAL = [
    '#2a78d6', // blue
    '#eb6834', // orange
    '#1baf7a', // aqua
    '#eda100', // yellow
    '#e87ba4', // magenta
    '#008300', // green
    '#4a3aa7', // violet
];
const OTHER_COLOR = '#c3c2b7';   // neutral gray — "Other" isn't an identity, so it doesn't spend a hue
const NONE_BUCKET = '(none)';   // the backend's bucket for changesets with no tag (see NONE_BUCKET in changesets/analytics/base.py) — gets a normal categorical color, not OTHER_COLOR: it's real, often-dominant volume (not a leftover-tail catch-all like "Other"), so graying it out would hide exactly the spikes it exists to reveal
const RANKING_COLOR = CATEGORICAL[0]; // single-series ranking bars all take one slot, per the nominal-categorical rule

const INK_SECONDARY = '#52514e';
const GRID_HAIRLINE = '#e1e0d9';
const AXIS_BASELINE = '#c3c2b7';

Chart.defaults.color = INK_SECONDARY;
Chart.defaults.borderColor = GRID_HAIRLINE;
Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';

const MAX_SERIES = CATEGORICAL.length; // beyond this, fold into "Other" rather than generating more hues

// ── Helpers ───────────────────────────────────────────────────────────────────

// String-sliced rather than Date-parsed — the API's date labels ("2026-09-02"
// or "2026-09-02 0:00") aren't full ISO 8601 (no "T"), which native Date
// parsing handles inconsistently across browsers. Only used for axis tick
// display; tooltips still show the original full label.
const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function shortDate(dateStr) {
    const m = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})/);
    return m ? `${MONTH_ABBR[parseInt(m[2], 10) - 1]} ${parseInt(m[3], 10)}` : dateStr;
}

// KPI numbers (total changesets/objects) can run into the hundreds of
// millions at full history scale — Intl's built-in compact notation ("14M",
// "126K") instead of hand-rolled suffix logic. Full precision is still
// available on hover via a small custom tooltip (elementId + '-tooltip' in
// the template) rather than the native `title` attribute — title's hover
// delay and complete absence on touch devices made it easy to miss.
const compactNumber = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });
function setKpiNumber(elementId, value) {
    document.getElementById(elementId).textContent = compactNumber.format(value);
    document.getElementById(`${elementId}-tooltip`).textContent = value.toLocaleString('en-US');
}

// Shared x-axis config for every time-series chart — a handful of short,
// evenly-spaced date labels instead of Chart.js's default of showing (and
// often rotating) every single one, which is what was crowding out the
// actual chart in the smaller "Over Time" widgets.
function dateAxis(dates, stacked) {
    return {
        stacked: !!stacked,
        grid: { display: false },
        ticks: { maxTicksLimit: 6, autoSkip: true, maxRotation: 0, callback: (val, idx) => shortDate(dates[idx]) },
    };
}

// Collapses series past MAX_SERIES into a single "Other" series (summed
// elementwise), instead of cycling colors past the fixed categorical set.
function foldToTopSeries(series) {
    if (series.length <= MAX_SERIES) return series;
    const kept = series.slice(0, MAX_SERIES);
    const rest = series.slice(MAX_SERIES);
    const other = { name: 'Other', counts: rest[0].counts.map((_, i) => rest.reduce((sum, s) => sum + s.counts[i], 0)) };
    return [...kept, other];
}

function stackedBar(canvasId, dates, rawSeries) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !rawSeries.length) return;
    const series = foldToTopSeries(rawSeries);
    new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: {
            labels: dates,
            datasets: series.map((s, i) => ({
                label: s.name,
                data: s.counts,
                backgroundColor: s.name === 'Other' ? OTHER_COLOR : CATEGORICAL[i],
                borderWidth: 0,
                maxBarThickness: 24,
            })),
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            // No legend here — the ranking chart directly above this one
            // (horizontalBar, same palette, same top-N order) already shows
            // every name's color, so a second legend on this smaller chart
            // was purely redundant, at the cost of most of its height.
            plugins: { legend: { display: false } },
            scales: {
                x: dateAxis(dates, true),
                y: { stacked: true, beginAtZero: true, border: { color: AXIS_BASELINE } },
            },
        },
    });
}

// Sets `field` to the clicked bar's name in the current URL and reloads —
// every filter (start_date/end_date/contributor/editor/imagery/country) already
// lives in the URL and every fetch reads from it, so this needs no client-
// side re-fetch orchestration, just a normal navigation to the new query.
// Additive: only the clicked field changes, any other filter already set
// stays in place.
function applyFilter(field, value) {
    const params = new URLSearchParams(window.location.search);
    params.set(field, value);
    window.location.search = params.toString();
}

// Same "URL is the single source of truth" navigation as applyFilter, but
// for the Time Range presets — those set start_date *and* end_date together,
// so they can't reuse applyFilter's single-field form. Any other filter
// already set (contributor/editor/imagery/country) stays in place.
function applyDateRangePreset(days) {
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - days);
    const iso = d => d.toISOString().slice(0, 10);
    const params = new URLSearchParams(window.location.search);
    params.set('start_date', iso(start));
    params.set('end_date', iso(end));
    window.location.search = params.toString();
}
document.querySelectorAll('.date-preset-btn').forEach(btn => {
    btn.addEventListener('click', () => applyDateRangePreset(parseInt(btn.dataset.days, 10)));
});

// A ranking list is one measure, not several series — every bar takes the
// same identity color; order and label carry identity, not hue.
// `filterField` (optional) is the URL filter param clicking a bar should
// set (e.g. 'editor') — omit it for a dimension with no filter support yet.
function horizontalBar(canvasId, labels, data, label, filterField) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !labels.length) return;
    new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label,
                data,
                backgroundColor: RANKING_COLOR,
                borderRadius: 4,
                borderSkipped: 'left',
                maxBarThickness: 24,
            }],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: !filterField ? undefined : {
                    callbacks: { footer: () => 'Click to filter' },
                    footerFont: { style: 'italic', weight: 'normal' },
                },
            },
            scales: {
                x: { beginAtZero: true, grid: { color: GRID_HAIRLINE } },
                y: { ticks: { autoSkip: false }, grid: { display: false }, border: { color: AXIS_BASELINE } },
            },
            onClick: !filterField ? undefined : (evt, elements, chart) => {
                if (!elements.length) return;
                applyFilter(filterField, chart.data.labels[elements[0].index]);
            },
            onHover: !filterField ? undefined : (evt, elements) => {
                evt.native.target.style.cursor = elements.length ? 'pointer' : 'default';
            },
        },
    });
}

// Single-series line chart — extracted from dashboard.js's renderVolumeChart
// so any page can draw a plain volume-over-time trend for an arbitrary
// canvas id/label, not just the main dashboard's hardcoded #changesetChart.
function lineChart(canvasId, dates, counts, label) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !dates.length) return;
    new Chart(ctx.getContext('2d'), {
        type: 'line',
        data: {
            labels: dates,
            datasets: [{
                label,
                data: counts,
                borderColor: CATEGORICAL[0],
                backgroundColor: 'rgba(42, 120, 214, 0.10)',
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4,
                pointHoverBackgroundColor: CATEGORICAL[0],
                tension: 0.1,
                fill: true,
            }],
        },
        options: {
            responsive: true,
            // Fill the container's width *and* height: Chart.js's default
            // 2:1 aspect ratio capped a chart in a short, wide card at twice
            // its height (half the card's width on the Objects page).
            // Callers give the container an explicit height.
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            // Points are invisible (pointRadius: 0) until hovered, so the
            // default intersect:true mode — which requires the cursor to
            // land exactly on the thin line itself — made the chart feel
            // unresponsive. index/intersect:false triggers on proximity to
            // a data point's x position instead, anywhere in that column.
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: dateAxis(dates),
                y: { beginAtZero: true, grid: { color: GRID_HAIRLINE } },
            },
        },
    });
}

// Several measures of the same unit side by side per category (e.g. share
// of changesets vs. share of objects per size bucket) — one shared y-axis,
// never two. `series` = [{name, values}], colored in fixed categorical order
// with a legend, since identity can't be color-alone at >= 2 series.
function groupedBar(canvasId, labels, series, valueFormat) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !labels.length) return;
    const format = valueFormat || (v => v.toLocaleString('en-US'));
    new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: {
            labels,
            datasets: series.map((s, i) => ({
                label: s.name,
                data: s.values,
                backgroundColor: CATEGORICAL[i],
                borderRadius: 4,
                borderSkipped: 'bottom',
                maxBarThickness: 24,
            })),
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'top', align: 'start', labels: { boxWidth: 12, boxHeight: 12 } },
                tooltip: { callbacks: { label: c => `${c.dataset.label}: ${format(c.parsed.y)}` } },
            },
            scales: {
                x: { grid: { display: false }, border: { color: AXIS_BASELINE } },
                y: { beginAtZero: true, grid: { color: GRID_HAIRLINE }, ticks: { callback: v => format(v) } },
            },
        },
    });
}

// Several lines over the same dates and the same unit (e.g. p50 and p90
// changeset size) — lineChart's multi-series sibling, with a legend.
// `logScale` when the lines differ by orders of magnitude (p50 ~5 vs p90
// ~130), so the smaller one isn't flattened against the baseline.
function multiLineChart(canvasId, dates, series, logScale) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !dates.length) return;
    new Chart(ctx.getContext('2d'), {
        type: 'line',
        data: {
            labels: dates,
            datasets: series.map((s, i) => ({
                label: s.name,
                data: s.values,
                borderColor: CATEGORICAL[i],
                backgroundColor: CATEGORICAL[i],
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4,
                tension: 0.1,
            })),
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'top', align: 'start', labels: { boxWidth: 12, boxHeight: 2 } } },
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: dateAxis(dates),
                y: logScale
                    ? { type: 'logarithmic', grid: { color: GRID_HAIRLINE },
                        ticks: { callback: v => ([1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000].includes(v) ? v.toLocaleString('en-US') : '') } }
                    : { beginAtZero: true, grid: { color: GRID_HAIRLINE } },
            },
        },
    });
}

// Spread of a measure per category, as a compact box: a floating bar from
// p25 to p75 (the middle half) plus a dot at the median, on a log axis since
// sizes span 1 to 10,000. `groups` = [{name, p25, p50, p75, p90, changesets}]
// (SizeBreakdownView). The tooltip carries every number, so nothing hides
// behind the encoding. `filterField` works like horizontalBar's.
function quartileChart(canvasId, groups, filterField) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !groups.length) return;
    // A log axis can't place 0 (an empty changeset), so floor at 1 for drawing only.
    const pos = v => Math.max(v, 1);
    new Chart(ctx.getContext('2d'), {
        data: {
            labels: groups.map(g => g.name),
            datasets: [
                {
                    type: 'bar',
                    label: 'p25–p75',
                    data: groups.map(g => [pos(g.p25), pos(g.p75)]),
                    backgroundColor: 'rgba(42, 120, 214, 0.35)',
                    borderRadius: 4,
                    borderSkipped: false,
                    maxBarThickness: 20,
                },
                {
                    type: 'scatter',
                    label: 'Median',
                    data: groups.map(g => ({ x: pos(g.p50), y: g.name })),
                    backgroundColor: CATEGORICAL[0],
                    borderColor: '#ffffff',
                    borderWidth: 2,
                    pointRadius: 5,
                    pointHoverRadius: 6,
                },
            ],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    callbacks: {
                        title: items => items[0].label,
                        label: () => null,
                        afterBody: items => {
                            const g = groups[items[0].dataIndex];
                            return [
                                `Median: ${g.p50.toLocaleString('en-US')} objects`,
                                `Middle half: ${g.p25.toLocaleString('en-US')}–${g.p75.toLocaleString('en-US')}`,
                                `p90: ${g.p90.toLocaleString('en-US')}`,
                                `Average: ${g.avg_objects.toLocaleString('en-US')}`,
                                `${g.changesets.toLocaleString('en-US')} changesets`,
                            ];
                        },
                        footer: () => (filterField ? 'Click to filter' : ''),
                    },
                    footerFont: { style: 'italic', weight: 'normal' },
                },
            },
            scales: {
                x: {
                    type: 'logarithmic',
                    min: 1,
                    grid: { color: GRID_HAIRLINE },
                    ticks: { maxRotation: 0, callback: v => ([1, 10, 100, 1000, 10000].includes(v) ? v.toLocaleString('en-US') : '') },
                    title: { display: true, text: 'Objects per changeset' },
                },
                y: { type: 'category', ticks: { autoSkip: false }, grid: { display: false }, border: { color: AXIS_BASELINE } },
            },
            onClick: !filterField ? undefined : (evt, elements, chart) => {
                if (!elements.length) return;
                applyFilter(filterField, chart.data.labels[elements[0].index]);
            },
            onHover: !filterField ? undefined : (evt, elements) => {
                evt.native.target.style.cursor = elements.length ? 'pointer' : 'default';
            },
        },
    });
}

// ── Fetch/widget-loading helpers ─────────────────────────────────────────────
// Each widget fetches and renders independently (not gated behind
// Promise.all) so a slow or failed one only affects its own spinner/canvas,
// not the rest of the page.

function apiUrl(path, extraParams) {
    const params = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(extraParams || {})) {
        params.set(key, value);
    }
    return `${path}?${params.toString()}`;
}

// Resolves once Datadog RUM (optional, base.html) has started. Its init
// returns synchronously, but it only starts injecting trace headers into
// requests ~10-50ms later (measured), after setting up its session, so API
// calls fired straight away at page load would lose their RUM <-> APM link.
// Resolves immediately when RUM isn't on the page (not configured or
// blocked), and after at most RUM_READY_TIMEOUT_MS otherwise (e.g. a session
// sampled out, where RUM never starts), so the dashboard never waits long.
const RUM_READY_TIMEOUT_MS = 300;
const rumReady = new Promise(resolve => {
    if (!window.DD_RUM || !window.DD_RUM.getInternalContext) return resolve();
    const deadline = performance.now() + RUM_READY_TIMEOUT_MS;
    (function poll() {
        if (window.DD_RUM.getInternalContext() || performance.now() > deadline) resolve();
        else setTimeout(poll, 10);
    })();
});

function fetchJson(url, options) {
    return rumReady.then(() => fetch(url, options)).then(r => {
        if (!r.ok) return r.json().then(body => { throw new Error(body.error || `HTTP ${r.status} for ${url}`); });
        return r.json();
    });
}

// Lazy loading: a widget's request only starts once its card is within
// LAZY_MARGIN of the viewport, so a page load fetches what's on screen (and
// just below) instead of every widget at once — several of these queries
// are CPU-heavy on the analytics database, and most visits never scroll to
// the bottom. Browsers without IntersectionObserver load everything at once.
const LAZY_MARGIN = '400px 0px';
const lazyStarts = new Map(); // observed element -> callbacks waiting on it
const lazyObserver = 'IntersectionObserver' in window
    ? new IntersectionObserver(entries => {
        entries.forEach(entry => {
            if (!entry.isIntersecting) return;
            lazyObserver.unobserve(entry.target);
            const starts = lazyStarts.get(entry.target) || [];
            lazyStarts.delete(entry.target);
            starts.forEach(start => start());
        });
    }, { rootMargin: LAZY_MARGIN })
    : null;

function whenNearViewport(element) {
    return new Promise(resolve => {
        if (!lazyObserver || !element) return resolve();
        if (!lazyStarts.has(element)) {
            lazyStarts.set(element, []);
            lazyObserver.observe(element);
        }
        lazyStarts.get(element).push(resolve);
    });
}

// Fetches `url`, hands the result to `onSuccess` (which is expected to
// un-hide `canvasId` itself once it actually has something to draw), and
// on failure replaces the spinner with an inline error instead of leaving
// it spinning forever. Widgets are independent: one failing doesn't block
// or hide any other widget on the page. Resolves to true once rendered,
// false on failure, for callers with more to fill than the widget itself.
//
// Waits for the widget to scroll near the viewport first (see LAZY_MARGIN),
// watching the spinner's parent: the canvas itself is hidden (no box) until
// it has data. `{ eager: true }` fetches straight away, for a widget whose
// response also fills something higher up the page (e.g. KPIs).
//
// Hides the spinner via style.display rather than the `hidden` attribute —
// the spinner also carries Tailwind's `flex` class, and [hidden] and .flex
// have equal CSS specificity, so whichever rule comes later in the compiled
// stylesheet wins regardless of the hidden attribute (it's `.flex` here,
// so `hidden = true` alone silently does nothing). An inline style always
// wins over a class, hidden attribute or not.
function loadWidget(canvasId, url, onSuccess, options) {
    const spinner = document.getElementById(`${canvasId}-spinner`);
    const ready = options && options.eager ? Promise.resolve() : whenNearViewport(spinner && spinner.parentElement);
    return ready
        .then(() => fetchJson(url))
        .then(data => {
            spinner.style.display = 'none';
            onSuccess(data);
            return true;
        })
        .catch(err => {
            console.error(`Failed to load ${url}`, err);
            spinner.innerHTML = '<span class="text-sm text-red-600">Failed to load</span>';
            return false;
        });
}

function showChart(canvasId) {
    document.getElementById(canvasId).hidden = false;
}

// ── Geo map ──────────────────────────────────────────────────────────────────
// Grid cells with an unreliable bbox are already excluded server-side (see
// GeoView) — geo.cells is safe to plot as-is.
//
// Drawn as actual rectangles (one per cell, exact bounds from
// lat_size_degrees/lon_size_degrees), not a Leaflet.heat blurred/
// interpolated blob layer — a heat blob only approximates a smooth surface
// *between* cell centers, which reads as "imprecise" and gives zooming in
// no real extra detail past the cell resolution. Rectangles show the true
// (geohash-derived, not square — see drawGeoCells) cell boundary and color,
// and make it visually honest that this is the actual resolution of the
// underlying data (see changesets/geo.py's GEOHASH_PREFIX_LENGTH if that
// resolution itself needs to change — that's a data change, not a
// rendering choice).
//
// Sequential blue ramp, light→dark, one hue not a rainbow (dataviz skill's
// palette.md) — quantized into GEO_COLOR_RAMP.length bins on a log scale:
// raw counts are heavily skewed (a few dense urban cells vs. many sparse
// ones), so equal-width bins on the raw count would put nearly every cell
// in the lightest bucket. `preferCanvas` (set on the map, below) renders
// the (potentially thousands of) rectangles on canvas instead of one SVG
// element each, which matters once cell count climbs into the thousands.
const GEO_COLOR_RAMP = ['#cde2fb', '#9ec5f4', '#5598e7', '#2a78d6', '#1c5cab', '#0d366b'];

// Two resolutions, one metric choice — see GeoView/changesets/geo.py.
// Coarse (global, cell size adapts to viewport) loads once at page load;
// fine (viewport-scoped, cell size also adapts — see
// geohash_precision_for_bbox in geo.py) is fetched on demand once the user
// zooms in past GEO_FINE_ZOOM_THRESHOLD. Only one resolution is ever shown
// at a time — switching between already-fetched coarse/fine data is
// instant (no refetch). The metric toggle (count vs. objects) never
// refetches either — both values are already in every cell, so it just
// restyles whichever layer is currently on screen.
//
// Panning/zooming while in fine mode does NOT fetch on every settle — see
// scheduleFineFetch's own comments for why a naive "fetch on every
// moveend" was found to be sending far more DB queries than normal map
// browsing needs.
const GEO_FINE_ZOOM_THRESHOLD = 7;
const GEO_FINE_FETCH_DEBOUNCE_MS = 600;
// Fetch this much extra area beyond the visible viewport (as a fraction of
// its own size, per Leaflet's LatLngBounds.pad) so a small pan/zoom-out
// within territory already covered needs no new request at all — reused
// straight from `fineCells` instead. 0.5 = fetch 50% extra on every side,
// i.e. a viewport-sized area of slack in every direction before the next
// pan forces a real fetch.
const GEO_FINE_PREFETCH_PAD = 0.5;
const GEO_METRICS = { count: 'Changesets', objects: 'Objects changed' };

function geoColorScale(cells, metric) {
    const logMax = Math.log1p(Math.max(...cells.map(c => c[metric]), 0)) || 1;
    return cell => GEO_COLOR_RAMP[Math.min(GEO_COLOR_RAMP.length - 1, Math.floor((Math.log1p(cell[metric]) / logMax) * GEO_COLOR_RAMP.length))];
}

// latSizeDegrees/lonSizeDegrees, not one square size: geohash cells aren't
// square (a 6-char cell is ~1.2km wide x ~0.6km tall everywhere on Earth,
// unlike the old fixed-degree grid which was square by construction) — see
// GeoView/changesets/geo.py's geohash_cell_size_degrees. Drawing a square
// here would misrepresent the true cell shape.
// `onCellClick(cell)` (optional) makes cells clickable; `selectedCell` (a
// cell's geohash) is outlined.
function drawGeoCells(layerGroup, cells, latSizeDegrees, lonSizeDegrees, metric, onCellClick, selectedCell) {
    layerGroup.clearLayers();
    const halfLat = latSizeDegrees / 2;
    const halfLon = lonSizeDegrees / 2;
    const colorFor = geoColorScale(cells, metric);
    cells.forEach(c => {
        const selected = c.cell === selectedCell;
        const rect = L.rectangle([[c.lat - halfLat, c.lon - halfLon], [c.lat + halfLat, c.lon + halfLon]], {
            stroke: selected,
            color: '#0d366b',
            weight: 2,
            fillColor: colorFor(c),
            fillOpacity: 0.85,
        })
            .bindTooltip(`${c.count.toLocaleString()} changesets<br>${c.objects.toLocaleString()} objects${onCellClick ? '<br><i>Click to list them</i>' : ''}`, { sticky: true })
            .addTo(layerGroup);
        if (onCellClick) rect.on('click', () => onCellClick(c));
    });
}

// ── Map cell panel ───────────────────────────────────────────────────────────
// Lists the changesets of a clicked map cell (/api/changesets/geo/cell/,
// same filters and range as the map), newest first, a page at a time. The
// cell's total is the count the map already has, so no count query.
const GEO_CELL_PAGE_SIZE = 50;

function geoCellPanel(onClose) {
    const panel = document.getElementById('geoCellPanel');
    if (!panel) return null;
    const list = document.getElementById('geoCellPanel-list');
    const more = document.getElementById('geoCellPanel-more');
    const status = document.getElementById('geoCellPanel-status');
    let current = null;   // { cell, offset }
    let abort = null;

    function close() {
        if (abort) abort.abort();
        current = null;
        panel.style.display = 'none';
        onClose();
    }
    document.getElementById('geoCellPanel-close').addEventListener('click', close);
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && current) close(); });

    // Built with DOM nodes, not innerHTML: usernames and comments are user input.
    function row(r) {
        const li = document.createElement('li');
        li.className = 'px-4 py-2';
        const top = document.createElement('div');
        top.className = 'flex items-baseline justify-between gap-2';
        const a = document.createElement('a');
        a.href = `https://www.openstreetmap.org/changeset/${r.changeset_id}`;
        a.target = '_blank';
        a.rel = 'noopener';
        a.className = 'font-medium text-blue-600 hover:underline';
        a.textContent = r.changeset_id;
        const when = document.createElement('span');
        when.className = 'text-xs text-gray-400 whitespace-nowrap';
        when.textContent = r.created_at.slice(0, 16).replace('T', ' ');
        top.append(a, when);
        const meta = document.createElement('div');
        meta.className = 'text-xs text-gray-600';
        meta.textContent = [r.user, r.editor, `${r.changes_count.toLocaleString('en-US')} object${r.changes_count === 1 ? '' : 's'}`].filter(Boolean).join(' · ');
        li.append(top, meta);
        if (r.comment) {
            const comment = document.createElement('div');
            comment.className = 'text-xs text-gray-400 truncate';
            comment.textContent = r.comment;
            comment.title = r.comment;
            li.append(comment);
        }
        return li;
    }

    function loadPage() {
        const { cell, offset } = current;
        if (abort) abort.abort();
        abort = new AbortController();
        more.style.display = 'none';
        status.textContent = 'Loading…';
        fetchJson(apiUrl('/api/changesets/geo/cell/', { cell, offset, limit: GEO_CELL_PAGE_SIZE }), { signal: abort.signal })
            .then(page => {
                if (!current || current.cell !== cell) return;
                page.results.forEach(r => list.appendChild(row(r)));
                current.offset = offset + page.results.length;
                status.textContent = page.has_more ? '' : (current.offset ? `All ${current.offset.toLocaleString('en-US')} shown` : 'No changesets');
                more.style.display = page.has_more ? '' : 'none';
            })
            .catch(err => {
                if (err.name === 'AbortError') return;
                console.error('Failed to load cell changesets', err);
                status.textContent = 'Failed to load';
                more.style.display = '';
            });
    }
    more.addEventListener('click', loadPage);

    return function open(c) {
        current = { cell: c.cell, offset: 0 };
        list.innerHTML = '';
        document.getElementById('geoCellPanel-title').textContent =
            `${c.count.toLocaleString('en-US')} changesets · ${c.objects.toLocaleString('en-US')} objects`;
        document.getElementById('geoCellPanel-subtitle').textContent =
            `Cell ${c.cell} around ${c.lat.toFixed(2)}, ${c.lon.toFixed(2)} · newest first`;
        panel.style.display = 'flex';
        loadPage();
    };
}

// Bins are log-scale, so a bare color swatch would be uninterpretable —
// label each with the real value (relative to this view's own max, same
// as the color scale itself) it tops out at.
function updateGeoLegend(legendDiv, cells, metric) {
    const logMax = Math.log1p(Math.max(...cells.map(c => c[metric]), 0)) || 1;
    legendDiv.innerHTML = `<div class="font-medium text-gray-700 mb-1">${GEO_METRICS[metric]}</div>` + GEO_COLOR_RAMP.map((color, i) => {
        const upper = Math.round(Math.expm1((i + 1) / GEO_COLOR_RAMP.length * logMax));
        return `<div class="flex items-center gap-1.5"><span style="display:inline-block;width:12px;height:12px;background:${color}"></span>up to ${upper.toLocaleString()}</div>`;
    }).join('');
}

function renderGeoMap(initialGeo) {
    const map = L.map('geoMap', { preferCanvas: true }).setView([20, 0], 2);
    // OpenFreeMap's Positron style: free, no API key or usage limits, and a
    // light, low-contrast base so the density cells stay the focus.
    L.maplibreGL({
        style: 'https://tiles.openfreemap.org/styles/positron',
        attribution: '<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> <a href="https://www.openmaptiles.org/" target="_blank">&copy; OpenMapTiles</a> Data from <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
    }).addTo(map);

    const coarseLayer = L.layerGroup().addTo(map);
    const fineLayer = L.layerGroup();
    const coarseCells = initialGeo.cells;
    const coarseLatSize = initialGeo.lat_size_degrees;
    const coarseLonSize = initialGeo.lon_size_degrees;
    let fineCells = [];
    let fineLatSize = null;
    let fineLonSize = null;
    let metric = 'count';
    let showingFine = false;
    // The (padded) bounds fineCells actually covers, the zoom level it was
    // fetched at, and the in-flight request for it if any — see
    // scheduleFineFetch. The zoom level matters as much as the bounds:
    // cell size adapts to the viewport (geohash_precision_for_bbox in
    // geo.py), so a cached fetch only remains valid while zoomed in *no
    // further* than when it was made — otherwise "still geographically
    // contained" alone would keep reusing the same coarser cells forever
    // as the user zooms in deeper, since zooming in stays within the old
    // (larger, padded) area without ever leaving it.
    let fineFetchedBounds = null;
    let fineFetchedZoom = null;
    let fineFetchAbort = null;

    let selectedCell = null;
    const openCellPanel = geoCellPanel(() => { selectedCell = null; redraw(); });
    const onCellClick = openCellPanel && (c => {
        selectedCell = c.cell;
        redraw();
        openCellPanel(c);
    });

    let legendDiv;
    const legend = L.control({ position: 'bottomright' });
    legend.onAdd = () => {
        legendDiv = L.DomUtil.create('div', 'bg-white rounded-md shadow-lg px-3 py-2 text-xs leading-relaxed');
        return legendDiv;
    };
    legend.addTo(map);

    function redraw() {
        const cells = showingFine ? fineCells : coarseCells;
        const latSize = showingFine ? fineLatSize : coarseLatSize;
        const lonSize = showingFine ? fineLonSize : coarseLonSize;
        drawGeoCells(showingFine ? fineLayer : coarseLayer, cells, latSize, lonSize, metric, onCellClick, selectedCell);
        updateGeoLegend(legendDiv, cells, metric);
    }

    // Segmented control, top-right: switch which value drives cell color.
    // disableClickPropagation so a click/scroll on the control doesn't
    // also pan/zoom the map underneath it.
    const metricControl = L.control({ position: 'topright' });
    metricControl.onAdd = () => {
        const div = L.DomUtil.create('div', 'bg-white rounded-md shadow-lg text-xs overflow-hidden flex');
        L.DomEvent.disableClickPropagation(div);
        div.innerHTML = Object.entries(GEO_METRICS).map(([key, label]) =>
            `<button type="button" data-metric="${key}" class="geo-metric-btn px-2 py-1.5 ${key === metric ? 'bg-blue-500 text-white' : 'text-gray-700 hover:bg-gray-100'}">${label}</button>`
        ).join('');
        div.querySelectorAll('.geo-metric-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                metric = btn.dataset.metric;
                div.querySelectorAll('.geo-metric-btn').forEach(b => {
                    const active = b.dataset.metric === metric;
                    b.classList.toggle('bg-blue-500', active);
                    b.classList.toggle('text-white', active);
                    b.classList.toggle('text-gray-700', !active);
                });
                redraw();
            });
        });
        return div;
    };
    metricControl.addTo(map);

    redraw();

    // Naive "fetch the exact viewport on every moveend/zoomend, debounced
    // 400ms" was found (2026-09-19) to send a real DB query for every
    // single pan/zoom settle while browsing, so normal map exploration
    // alone put real, avoidable load on the DB. Two changes fix that without changing what's drawn:
    //
    // 1. Fetch a PADDED area (GEO_FINE_PREFETCH_PAD extra on every side),
    //    not just the exact visible viewport, and skip the fetch entirely
    //    whenever the current viewport is still inside the padded area the
    //    last fetch already covered (`fineFetchedBounds.contains(...)`).
    //    Panning around within already-explored territory then costs zero
    //    requests — only a pan/zoom that actually leaves covered ground
    //    triggers a real fetch, same principle as how tile layers only
    //    fetch tiles that scroll into view.
    // 2. Cancel a still-in-flight fetch (AbortController) when a newer one
    //    is needed, instead of letting both run — otherwise fast browsing
    //    (faster than a request round-trip) can pile up multiple concurrent
    //    DB queries whose results arrive out of order and get thrown away
    //    anyway once a newer one lands.
    let fineFetchTimer = null;
    function scheduleFineFetch() {
        clearTimeout(fineFetchTimer);
        fineFetchTimer = setTimeout(() => {
            const viewBounds = map.getBounds();
            const currentZoom = map.getZoom();
            // Reuse the cache only when not zoomed in past where it was
            // fetched — zooming in further always needs a real fetch, even
            // if the new (smaller) viewport is still geographically inside
            // the old padded area, since finer cells are owed at a deeper
            // zoom and "still contained" alone can't tell the difference.
            if (fineFetchedBounds && fineFetchedBounds.contains(viewBounds) && currentZoom <= fineFetchedZoom) return;

            const fetchBounds = viewBounds.pad(GEO_FINE_PREFETCH_PAD);
            const bbox = `${fetchBounds.getWest()},${fetchBounds.getSouth()},${fetchBounds.getEast()},${fetchBounds.getNorth()}`;

            if (fineFetchAbort) fineFetchAbort.abort();
            fineFetchAbort = new AbortController();

            fetchJson(apiUrl('/api/changesets/geo/', { resolution: 'fine', bbox }), { signal: fineFetchAbort.signal })
                .then(geo => {
                    fineCells = geo.cells;
                    fineLatSize = geo.lat_size_degrees;
                    fineLonSize = geo.lon_size_degrees;
                    fineFetchedBounds = fetchBounds;
                    fineFetchedZoom = currentZoom;
                    if (showingFine) redraw();
                })
                .catch(err => {
                    if (err.name !== 'AbortError') console.error('Failed to load fine-resolution geo data', err);
                });
        }, GEO_FINE_FETCH_DEBOUNCE_MS);
    }

    map.on('zoomend moveend', () => {
        const isFine = map.getZoom() >= GEO_FINE_ZOOM_THRESHOLD;
        if (isFine !== showingFine) {
            showingFine = isFine;
            if (showingFine) { coarseLayer.remove(); fineLayer.addTo(map); }
            else { fineLayer.remove(); coarseLayer.addTo(map); }
            redraw();
        }
        if (showingFine) scheduleFineFetch();
    });

    map.invalidateSize(); // container was `hidden` (zero-size) at construction time
}

// ── Filter suggestions ───────────────────────────────────────────────────────
// A small custom dropdown, not native <input list>/<datalist> — datalist's
// on-focus-show-all and option-ordering behavior both vary enough across
// browsers that "show 3 suggestions on an empty click" and "show top-20
// matches before /api/autocomplete/ results, in that order" aren't
// reliably controllable through it. This gives exact control over both.

const SUGGEST_ON_FOCUS_COUNT = 3;
const SUGGEST_MAX_RESULTS = 10; // matches AutocompleteView's own [:10] cap

// Each field's current top-~20 names (set by the loadWidget callbacks that
// already fetch each dimension's ranking chart, reusing that response — no
// extra request) — read live by wireSuggestions' handlers below, not
// captured at wire-time, since ranking data usually hasn't arrived yet
// when wireSuggestions itself runs at page load. Those widgets load lazily,
// so the on-focus preview stays empty until the ranking has been scrolled
// into view; typing still queries /api/autocomplete/.
const topValueCache = {};
function cacheTopValues(field, names) {
    topValueCache[field] = names;
}

function renderSuggestions(dropdownId, inputId, values) {
    const dropdown = document.getElementById(dropdownId);
    if (!dropdown) return;
    if (!values.length) {
        dropdown.classList.add('hidden');
        dropdown.innerHTML = '';
        return;
    }
    dropdown.innerHTML = values.map(v =>
        `<button type="button" class="block w-full text-left px-3 py-1.5 text-sm hover:bg-gray-100" data-value="${v.replace(/"/g, '&quot;')}">${v}</button>`
    ).join('');
    dropdown.classList.remove('hidden');
    dropdown.querySelectorAll('button').forEach(btn => {
        // mousedown, not click: fires before the input's blur handler would
        // otherwise hide this dropdown first and swallow the click.
        btn.addEventListener('mousedown', e => {
            e.preventDefault();
            document.getElementById(inputId).value = btn.dataset.value;
            dropdown.classList.add('hidden');
        });
    });
}

// Empty input (on focus, or cleared while typing): just the top 3 — a
// small unobtrusive preview, not the full ranking. Non-empty input: top-20
// matches first (client-side substring filter over the cached ranking,
// instant, no request), then /api/autocomplete/ results appended for
// anything beyond the top 20, deduped, capped at SUGGEST_MAX_RESULTS —
// exactly the "suggest top 20 first, before autocomplete" ordering asked
// for, since a plain <datalist> can't guarantee that ordering reliably.
function wireSuggestions(inputId, dropdownId, field) {
    const input = document.getElementById(inputId);
    if (!input) return;

    function showTopSlice() {
        renderSuggestions(dropdownId, inputId, (topValueCache[field] || []).slice(0, SUGGEST_ON_FOCUS_COUNT));
    }

    input.addEventListener('focus', () => {
        if (!input.value) showTopSlice();
    });

    input.addEventListener('input', () => {
        const q = input.value;
        if (!q) {
            showTopSlice();
            return;
        }
        const qLower = q.toLowerCase();
        const topMatches = (topValueCache[field] || []).filter(v => v.toLowerCase().includes(qLower));
        fetch(`/api/autocomplete/?field=${field}&q=${encodeURIComponent(q)}`)
            .then(r => r.json())
            .then(serverValues => {
                if (input.value !== q) return; // a later keystroke already superseded this response
                const seen = new Set(topMatches.map(v => v.toLowerCase()));
                const merged = [...topMatches, ...serverValues.filter(v => !seen.has(v.toLowerCase()))].slice(0, SUGGEST_MAX_RESULTS);
                renderSuggestions(dropdownId, inputId, merged);
            })
            .catch(err => console.error(`Failed to load autocomplete for ${field}`, err));
    });

    input.addEventListener('blur', () => {
        document.getElementById(dropdownId).classList.add('hidden');
    });
    input.addEventListener('keydown', e => {
        if (e.key === 'Escape') document.getElementById(dropdownId).classList.add('hidden');
    });
}
