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
            plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 12, usePointStyle: true } } },
            scales: {
                x: { stacked: true, grid: { display: false } },
                y: { stacked: true, beginAtZero: true, border: { color: AXIS_BASELINE } },
            },
        },
    });
}

// A ranking list is one measure, not several series — every bar takes the
// same identity color; order and label carry identity, not hue.
function horizontalBar(canvasId, labels, data, label) {
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
            plugins: { legend: { display: false } },
            scales: {
                x: { beginAtZero: true, grid: { color: GRID_HAIRLINE } },
                y: { ticks: { autoSkip: false }, grid: { display: false }, border: { color: AXIS_BASELINE } },
            },
        },
    });
}

// ── Render ───────────────────────────────────────────────────────────────────

function renderDashboard({ summary, volume, editorsTime, imageriesTime, localesTime, topEditors, topImageries, topLocales, topContributorsByObjects, topEditorsByObjects }) {
    document.getElementById('loadingIndicator').hidden = true;

    // Pre-fill the filter form with the range actually applied (e.g. the
    // default 7-day window when the page was loaded with no query params).
    // Every endpoint echoes the same start_date/end_date/contributor/editor/
    // imagery back, so any one of the responses would do here — summary is
    // always fetched regardless of what else is on the page.
    document.getElementById('start_date').value = summary.filters.start_date;
    document.getElementById('end_date').value = summary.filters.end_date;
    document.getElementById('contributor').value = summary.filters.contributor;
    document.getElementById('editor').value = summary.filters.editor;
    document.getElementById('imagery').value = summary.filters.imagery;

    document.getElementById('totalChangesets').textContent = summary.total_changesets;
    document.getElementById('totalObjects').textContent = summary.total_objects;
    document.getElementById('avgObjects').textContent = summary.avg_objects;

    // Part 1: Changeset activity
    if (volume.dates.length) {
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
                    tension: 0.1,
                    fill: true,
                }],
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { display: false } },
                    y: { beginAtZero: true, grid: { color: GRID_HAIRLINE } },
                },
            },
        });
    }

    horizontalBar('topEditorsChart', topEditors.results.map(r => r.name), topEditors.results.map(r => r.value), 'Changesets');
    stackedBar('topEditorsTimeChart', editorsTime.dates, editorsTime.series);

    horizontalBar('topImageriesChart', topImageries.results.map(r => r.name), topImageries.results.map(r => r.value), 'Changesets');
    stackedBar('topImageriesTimeChart', imageriesTime.dates, imageriesTime.series);

    horizontalBar('topLocalesChart', topLocales.results.map(r => r.name), topLocales.results.map(r => r.value), 'Changesets');
    stackedBar('topLocalesTimeChart', localesTime.dates, localesTime.series);

    // Part 2: Objects changed
    horizontalBar('topContributorsByObjectsChart', topContributorsByObjects.results.map(r => r.name), topContributorsByObjects.results.map(r => r.value), 'Objects changed');
    horizontalBar('topEditorsByObjectsChart', topEditorsByObjects.results.map(r => r.name), topEditorsByObjects.results.map(r => r.value), 'Objects changed');
}

function showError(err) {
    console.error('Failed to load dashboard data', err);
    document.getElementById('loadingIndicator').hidden = true;
    const banner = document.createElement('div');
    banner.className = 'bg-red-100 text-red-700 rounded-lg p-4 mb-8';
    banner.textContent = 'Could not load dashboard data. Please try again shortly.';
    document.querySelector('.container').prepend(banner);
}

// ── Load ─────────────────────────────────────────────────────────────────────
// The page shell renders instantly with empty charts; these fetches (against
// the standalone /api/changesets/{timeseries,summary,toplist}/ endpoints —
// each independently public, not just for this page's own use) are what
// actually populate them. Fetched in parallel so total load time is bounded
// by the slowest one, not their sum.

function apiUrl(path, extraParams) {
    const params = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(extraParams || {})) {
        params.set(key, value);
    }
    return `${path}?${params.toString()}`;
}

function fetchJson(url) {
    return fetch(url).then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
        return r.json();
    });
}

Promise.all([
    fetchJson(apiUrl('/api/changesets/summary/')),
    fetchJson(apiUrl('/api/changesets/timeseries/')),
    fetchJson(apiUrl('/api/changesets/timeseries/', { group_by: 'editor' })),
    fetchJson(apiUrl('/api/changesets/timeseries/', { group_by: 'imagery' })),
    fetchJson(apiUrl('/api/changesets/timeseries/', { group_by: 'locale' })),
    fetchJson(apiUrl('/api/changesets/toplist/', { dimension: 'editor', metric: 'count' })),
    fetchJson(apiUrl('/api/changesets/toplist/', { dimension: 'imagery', metric: 'count' })),
    fetchJson(apiUrl('/api/changesets/toplist/', { dimension: 'locale', metric: 'count' })),
    fetchJson(apiUrl('/api/changesets/toplist/', { dimension: 'contributor', metric: 'objects' })),
    fetchJson(apiUrl('/api/changesets/toplist/', { dimension: 'editor', metric: 'objects' })),
])
    .then(([summary, volume, editorsTime, imageriesTime, localesTime, topEditors, topImageries, topLocales, topContributorsByObjects, topEditorsByObjects]) =>
        renderDashboard({ summary, volume, editorsTime, imageriesTime, localesTime, topEditors, topImageries, topLocales, topContributorsByObjects, topEditorsByObjects }))
    .catch(showError);

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
