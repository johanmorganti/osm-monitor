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

function renderDashboard(data) {
    // Pre-fill the filter form with the range actually applied (e.g. the
    // default 7-day window when the page was loaded with no query params).
    document.getElementById('start_date').value = data.filters.start_date;
    document.getElementById('end_date').value = data.filters.end_date;
    document.getElementById('contributor').value = data.filters.contributor;
    document.getElementById('editor').value = data.filters.editor;
    document.getElementById('imagery').value = data.filters.imagery;

    document.getElementById('totalChangesets').textContent = data.total_changesets;
    document.getElementById('totalObjects').textContent = data.total_objects;
    document.getElementById('avgObjects').textContent = data.avg_objects;

    // Part 1: Changeset activity
    if (data.daily_counts.length) {
        new Chart(document.getElementById('changesetChart').getContext('2d'), {
            type: 'line',
            data: {
                labels: data.daily_counts.map(d => d.date),
                datasets: [{
                    label: 'Changesets',
                    data: data.daily_counts.map(d => d.count),
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

    horizontalBar('topEditorsChart',
        data.top_editors.map(e => e.name),
        data.top_editors.map(e => e.count),
        'Changesets');
    stackedBar('topEditorsTimeChart', data.top_editors_time.dates, data.top_editors_time.series);

    horizontalBar('topImageriesChart',
        data.top_imageries.map(i => i.name),
        data.top_imageries.map(i => i.count),
        'Changesets');
    stackedBar('topImageriesTimeChart', data.top_imageries_time.dates, data.top_imageries_time.series);

    horizontalBar('topLocalesChart',
        data.top_locales.map(l => l.name),
        data.top_locales.map(l => l.count),
        'Changesets');
    stackedBar('topLocalesTimeChart', data.top_locales_time.dates, data.top_locales_time.series);

    // Part 2: Objects changed
    horizontalBar('topContributorsByObjectsChart',
        data.top_contributors_by_objects.map(r => r.name),
        data.top_contributors_by_objects.map(r => r.total),
        'Objects changed');

    horizontalBar('topEditorsByObjectsChart',
        data.top_editors_by_objects.map(r => r.name),
        data.top_editors_by_objects.map(r => r.total),
        'Objects changed');
}

function showError(err) {
    console.error('Failed to load dashboard data', err);
    const banner = document.createElement('div');
    banner.className = 'bg-red-100 text-red-700 rounded-lg p-4 mb-8';
    banner.textContent = 'Could not load dashboard data. Please try again shortly.';
    document.querySelector('.container').prepend(banner);
}

// ── Load ─────────────────────────────────────────────────────────────────────
// The page shell renders instantly with empty charts; this fetch (against the
// standalone /api/dashboard/ endpoint) is what actually populates them.

fetch(`/api/dashboard/${window.location.search}`)
    .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    })
    .then(renderDashboard)
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
