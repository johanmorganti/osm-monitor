// ── Design tokens ────────────────────────────────────────────────────────────
// Validated categorical palette (fixed hue order, never cycled — see the
// dataviz skill's references/palette.md). Light mode only; this dashboard
// has no dark theme.

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
// every filter (start_date/end_date/contributor/editor/imagery) already
// lives in the URL and every fetch reads from it, so this needs no client-
// side re-fetch orchestration, just a normal navigation to the new query.
// Additive: only the clicked field changes, any other filter already set
// stays in place.
function applyFilter(field, value) {
    const params = new URLSearchParams(window.location.search);
    params.set(field, value);
    window.location.search = params.toString();
}

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

// ── Render ───────────────────────────────────────────────────────────────────

function renderVolumeChart(volume) {
    if (!volume.dates.length) return;
    new Chart(document.getElementById('changesetChart').getContext('2d'), {
        type: 'line',
        data: {
            labels: volume.dates,
            datasets: [{
                label: 'Changesets',
                data: volume.series[0].counts,
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
            plugins: { legend: { display: false } },
            // Points are invisible (pointRadius: 0) until hovered, so the
            // default intersect:true mode — which requires the cursor to
            // land exactly on the thin line itself — made the chart feel
            // unresponsive. index/intersect:false triggers on proximity to
            // a data point's x position instead, anywhere in that column.
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: dateAxis(volume.dates),
                y: { beginAtZero: true, grid: { color: GRID_HAIRLINE } },
            },
        },
    });
}

// ── Load ─────────────────────────────────────────────────────────────────────
// The page shell renders instantly with empty charts; these fetches (against
// the standalone /api/changesets/{timeseries,summary,toplist}/ endpoints —
// each independently public, not just for this page's own use) are what
// actually populate them. Each widget fetches and renders independently
// (not gated behind Promise.all) so a slow or failed one only affects its
// own spinner/canvas, not the rest of the page.

function apiUrl(path, extraParams) {
    const params = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(extraParams || {})) {
        params.set(key, value);
    }
    return `${path}?${params.toString()}`;
}

function fetchJson(url) {
    return fetch(url).then(r => {
        if (!r.ok) return r.json().then(body => { throw new Error(body.error || `HTTP ${r.status} for ${url}`); });
        return r.json();
    });
}

// Fetches `url`, hands the result to `onSuccess` (which is expected to
// un-hide `canvasId` itself once it actually has something to draw), and
// on failure replaces the spinner with an inline error instead of leaving
// it spinning forever. Widgets are independent: one failing doesn't block
// or hide any other widget on the page.
//
// Hides the spinner via style.display rather than the `hidden` attribute —
// the spinner also carries Tailwind's `flex` class, and [hidden] and .flex
// have equal CSS specificity, so whichever rule comes later in the compiled
// stylesheet wins regardless of the hidden attribute (it's `.flex` here,
// so `hidden = true` alone silently does nothing). An inline style always
// wins over a class, hidden attribute or not.
function loadWidget(canvasId, url, onSuccess) {
    return fetchJson(url)
        .then(data => {
            document.getElementById(`${canvasId}-spinner`).style.display = 'none';
            onSuccess(data);
        })
        .catch(err => {
            console.error(`Failed to load ${url}`, err);
            document.getElementById(`${canvasId}-spinner`).innerHTML =
                '<span class="text-sm text-red-600">Failed to load</span>';
        });
}

function showChart(canvasId) {
    document.getElementById(canvasId).hidden = false;
}

loadWidget('changesetChart', apiUrl('/api/changesets/timeseries/'), volume => {
    showChart('changesetChart');
    renderVolumeChart(volume);
});

loadWidget('topEditorsChart', apiUrl('/api/changesets/toplist/', { dimension: 'editor', metric: 'count' }), topEditors => {
    showChart('topEditorsChart');
    horizontalBar('topEditorsChart', topEditors.results.map(r => r.name), topEditors.results.map(r => r.value), 'Changesets', 'editor');
});
loadWidget('topEditorsTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'editor' }), editorsTime => {
    showChart('topEditorsTimeChart');
    stackedBar('topEditorsTimeChart', editorsTime.dates, editorsTime.series);
});

loadWidget('topImageriesChart', apiUrl('/api/changesets/toplist/', { dimension: 'imagery', metric: 'count' }), topImageries => {
    showChart('topImageriesChart');
    horizontalBar('topImageriesChart', topImageries.results.map(r => r.name), topImageries.results.map(r => r.value), 'Changesets', 'imagery');
});
loadWidget('topImageriesTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'imagery' }), imageriesTime => {
    showChart('topImageriesTimeChart');
    stackedBar('topImageriesTimeChart', imageriesTime.dates, imageriesTime.series);
});

loadWidget('topLocalesChart', apiUrl('/api/changesets/toplist/', { dimension: 'locale', metric: 'count' }), topLocales => {
    showChart('topLocalesChart');
    horizontalBar('topLocalesChart', topLocales.results.map(r => r.name), topLocales.results.map(r => r.value), 'Changesets');
});
loadWidget('topLocalesTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'locale' }), localesTime => {
    showChart('topLocalesTimeChart');
    stackedBar('topLocalesTimeChart', localesTime.dates, localesTime.series);
});

loadWidget('topContributorsByObjectsChart', apiUrl('/api/changesets/toplist/', { dimension: 'contributor', metric: 'objects' }), topContributorsByObjects => {
    showChart('topContributorsByObjectsChart');
    horizontalBar('topContributorsByObjectsChart', topContributorsByObjects.results.map(r => r.name), topContributorsByObjects.results.map(r => r.value), 'Objects changed', 'contributor');
});
loadWidget('topEditorsByObjectsChart', apiUrl('/api/changesets/toplist/', { dimension: 'editor', metric: 'objects' }), topEditorsByObjects => {
    showChart('topEditorsByObjectsChart');
    horizontalBar('topEditorsByObjectsChart', topEditorsByObjects.results.map(r => r.name), topEditorsByObjects.results.map(r => r.value), 'Objects changed', 'editor');
});

// Summary has no chart/spinner of its own — it fills the KPI numbers (which
// already show "–" as their placeholder) and pre-fills the filter form with
// the range actually applied (e.g. the default 7-day window when the page
// loads with no query params).
fetchJson(apiUrl('/api/changesets/summary/'))
    .then(summary => {
        document.getElementById('start_date').value = summary.filters.start_date;
        document.getElementById('end_date').value = summary.filters.end_date;
        document.getElementById('contributor').value = summary.filters.contributor;
        document.getElementById('editor').value = summary.filters.editor;
        document.getElementById('imagery').value = summary.filters.imagery;

        document.getElementById('totalChangesets').textContent = summary.total_changesets;
        document.getElementById('totalObjects').textContent = summary.total_objects;
        document.getElementById('avgObjects').textContent = summary.avg_objects;
    })
    .catch(err => {
        console.error('Failed to load summary', err);
        document.getElementById('totalChangesets').textContent = 'Error';
        document.getElementById('totalObjects').textContent = 'Error';
        document.getElementById('avgObjects').textContent = 'Error';
    });

// ── Filter autocomplete ─────────────────────────────────────────────────────

function wireAutocomplete(inputId, datalistId, field) {
    document.getElementById(inputId).addEventListener('input', function () {
        const q = this.value;
        if (q.length < 1) return;
        fetch(`/api/autocomplete/?field=${field}&q=${encodeURIComponent(q)}`)
            .then(r => r.json())
            .then(values => {
                const dl = document.getElementById(datalistId);
                dl.innerHTML = values.map(v => `<option value="${v}"></option>`).join('');
            });
    });
}
wireAutocomplete('contributor', 'contributor-list', 'contributor');
wireAutocomplete('editor',      'editor-list',      'editor');
wireAutocomplete('imagery',     'imagery-list',     'imagery');
