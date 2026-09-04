const {
    dailyCounts,
    topEditors,        topEditorsTime,
    topImageries,      topImageriesTime,
    topLocales,        topLocalesTime,
    topContributorsByObjects,
    topEditorsByObjects,
} = window.dashboardData;

// ── Colour palette ────────────────────────────────────────────────────────────

const PALETTE = [
    'rgba(54, 162, 235, 0.8)',
    'rgba(255, 99, 132, 0.8)',
    'rgba(75, 192, 192, 0.8)',
    'rgba(255, 206, 86, 0.8)',
    'rgba(153, 102, 255, 0.8)',
    'rgba(255, 159, 64, 0.8)',
    'rgba(76, 175, 80, 0.8)',
    'rgba(233, 30, 99, 0.8)',
    'rgba(0, 188, 212, 0.8)',
    'rgba(255, 87, 34, 0.8)',
    'rgba(121, 85, 72, 0.8)',
    'rgba(96, 125, 139, 0.8)',
];

function color(i) { return PALETTE[i % PALETTE.length]; }

// ── Helpers ───────────────────────────────────────────────────────────────────

function stackedBar(canvasId, dates, series) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !series.length) return;
    new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: {
            labels: dates,
            datasets: series.map((s, i) => ({
                label: s.name,
                data: s.counts,
                backgroundColor: color(i),
                borderWidth: 0,
            })),
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { stacked: true },
                y: { stacked: true, beginAtZero: true },
            },
        },
    });
}

function horizontalBar(canvasId, labels, data, label) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !labels.length) return;
    new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: {
            labels,
            datasets: [{ label, data, backgroundColor: labels.map((_, i) => color(i)), borderWidth: 0 }],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { x: { beginAtZero: true } },
        },
    });
}

// ── Part 1: Changeset activity ────────────────────────────────────────────────

// Changesets over time
if (dailyCounts && dailyCounts.length) {
    new Chart(document.getElementById('changesetChart').getContext('2d'), {
        type: 'line',
        data: {
            labels: dailyCounts.map(d => d.date),
            datasets: [{
                label: 'Changesets',
                data: dailyCounts.map(d => d.count),
                borderColor: 'rgba(54, 162, 235, 1)',
                backgroundColor: 'rgba(54, 162, 235, 0.15)',
                tension: 0.1,
                fill: true,
            }],
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true } },
        },
    });
}

// Editors
horizontalBar('topEditorsChart',
    topEditors.map(e => e.editor),
    topEditors.map(e => e.count),
    'Changesets');
stackedBar('topEditorsTimeChart', topEditorsTime.dates, topEditorsTime.editors);

// Imagery
horizontalBar('topImageriesChart',
    topImageries.map(i => i.imagery),
    topImageries.map(i => i.count),
    'Changesets');
stackedBar('topImageriesTimeChart', topImageriesTime.dates, topImageriesTime.imageries);

// Language
horizontalBar('topLocalesChart',
    topLocales.map(l => l.locale),
    topLocales.map(l => l.count),
    'Changesets');
stackedBar('topLocalesTimeChart', topLocalesTime.dates, topLocalesTime.locales);

// ── Part 2: Objects changed ───────────────────────────────────────────────────

horizontalBar('topContributorsByObjectsChart',
    topContributorsByObjects.map(r => r.user),
    topContributorsByObjects.map(r => r.total),
    'Objects changed');

horizontalBar('topEditorsByObjectsChart',
    topEditorsByObjects.map(r => r.editor),
    topEditorsByObjects.map(r => r.total),
    'Objects changed');
