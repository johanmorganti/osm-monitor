// ── Objects page widgets ─────────────────────────────────────────────────────
// How big changesets are and who changes the most objects. Chart factories
// and fetch helpers live in common.js (loaded first); this file only wires
// this page's DOM ids to API calls. Same filters/date range as the Overview
// page (both in the URL), same 7-day default.

const percent = v => `${(v * 100).toFixed(v < 0.1 ? 1 : 0)}%`;

// ── KPIs + size histogram: one /distribution/ call fills both ───────────────
// Eager: the KPIs and the filter form at the top of the page wait on it.
loadWidget('sizeHistogramChart', apiUrl('/api/changesets/distribution/'), dist => {
    document.getElementById('start_date').value = dist.filters.start_date;
    document.getElementById('end_date').value = dist.filters.end_date;
    document.getElementById('contributor').value = dist.filters.contributor;
    document.getElementById('editor').value = dist.filters.editor;
    document.getElementById('imagery').value = dist.filters.imagery;
    document.getElementById('country').value = dist.filters.country;

    setKpiNumber('totalObjects', dist.total_objects);
    document.getElementById('avgObjects').textContent = dist.avg_objects;
    document.getElementById('medianObjects').textContent = dist.percentiles.p50 ?? '–';
    document.getElementById('whaleShare').textContent =
        dist.top_1pct_objects_share === null ? '–' : percent(dist.top_1pct_objects_share);

    // Shares rather than raw counts: changesets and objects differ by ~100x,
    // and one y-axis has to fit both (never a dual axis).
    const share = (values, total) => values.map(v => (total ? v / total : 0));
    showChart('sizeHistogramChart');
    groupedBar('sizeHistogramChart', dist.buckets.map(b => b.label), [
        { name: 'Changesets', values: share(dist.buckets.map(b => b.changesets), dist.total_changesets) },
        { name: 'Objects changed', values: share(dist.buckets.map(b => b.objects), dist.total_objects) },
    ], percent);
}, { eager: true }).then(loaded => {
    if (loaded) return;
    ['totalObjects', 'avgObjects', 'medianObjects', 'whaleShare'].forEach(id => {
        document.getElementById(id).textContent = 'Error';
    });
});

loadWidget('objectsChart', apiUrl('/api/changesets/timeseries/', { metric: 'objects' }), volume => {
    showChart('objectsChart');
    lineChart('objectsChart', volume.dates, volume.series[0].counts, 'Objects changed');
});

loadWidget('sizeOverTimeChart', apiUrl('/api/changesets/distribution/breakdown/', { by: 'day' }), sizes => {
    showChart('sizeOverTimeChart');
    multiLineChart('sizeOverTimeChart', sizes.groups.map(g => g.name), [
        { name: 'Median (p50)', values: sizes.groups.map(g => g.p50) },
        { name: 'p90', values: sizes.groups.map(g => g.p90) },
    ], true);
});

loadWidget('sizeByEditorChart', apiUrl('/api/changesets/distribution/breakdown/', { by: 'editor' }), sizes => {
    showChart('sizeByEditorChart');
    quartileChart('sizeByEditorChart', sizes.groups, 'editor');
});

loadWidget('sizeByExperienceChart', apiUrl('/api/changesets/distribution/breakdown/', { by: 'experience' }), sizes => {
    showChart('sizeByExperienceChart');
    quartileChart('sizeByExperienceChart', sizes.groups);
});

// ── Largest changesets tables ────────────────────────────────────────────────
// Built with DOM nodes, not innerHTML: usernames and comments are user input.
const LARGEST_COLUMNS = [
    { label: 'Changeset', cell: r => {
        const a = document.createElement('a');
        a.href = `https://www.openstreetmap.org/changeset/${r.changeset_id}`;
        a.target = '_blank';
        a.rel = 'noopener';
        a.className = 'text-blue-600 hover:underline';
        a.textContent = r.changeset_id;
        return a;
    } },
    { label: 'Created', cell: r => r.created_at.slice(0, 16).replace('T', ' ') },
    { label: 'Contributor', cell: r => r.user || '–' },
    { label: 'Editor', cell: r => r.editor || '–' },
    { label: 'Country', cell: r => r.country || '–' },
    { label: 'Objects', numeric: true, cell: r => r.changes_count.toLocaleString('en-US') },
    { label: 'Area (km²)', numeric: true, cell: r => (r.area_km2 === null ? '–' : r.area_km2.toLocaleString('en-US')) },
    { label: 'Comment', wide: true, cell: r => r.comment || '' },
];

function renderLargestTable(tableId, results) {
    const table = document.getElementById(tableId);
    const head = table.createTHead().insertRow();
    head.className = 'text-left text-xs text-gray-500 uppercase tracking-wide border-b border-gray-200';
    LARGEST_COLUMNS.forEach(col => {
        const th = document.createElement('th');
        th.className = `py-2 pr-4 font-medium${col.numeric ? ' text-right' : ''}`;
        th.textContent = col.label;
        head.appendChild(th);
    });
    const body = table.createTBody();
    results.forEach(r => {
        const row = body.insertRow();
        row.className = 'border-b border-gray-100 align-top';
        LARGEST_COLUMNS.forEach(col => {
            const td = row.insertCell();
            td.className = `py-2 pr-4${col.numeric ? ' text-right tabular-nums' : ''}${col.wide ? ' text-gray-500 max-w-md truncate' : ' whitespace-nowrap'}`;
            const value = col.cell(r);
            if (value instanceof Node) td.appendChild(value);
            else td.textContent = value;
            if (col.wide) td.title = value;
        });
    });
    if (!results.length) {
        const td = body.insertRow().insertCell();
        td.colSpan = LARGEST_COLUMNS.length;
        td.className = 'py-4 text-gray-400';
        td.textContent = 'No changesets in this range';
    }
    table.hidden = false;
}

loadWidget('largestByObjectsTable', apiUrl('/api/changesets/largest/', { by: 'objects' }), largest => {
    renderLargestTable('largestByObjectsTable', largest.results);
});
loadWidget('largestByAreaTable', apiUrl('/api/changesets/largest/', { by: 'area' }), largest => {
    renderLargestTable('largestByAreaTable', largest.results);
});

// ── Rankings by objects (moved from the Overview page) ──────────────────────
loadWidget('topContributorsByObjectsChart', apiUrl('/api/changesets/toplist/', { dimension: 'contributor', metric: 'objects' }), top => {
    showChart('topContributorsByObjectsChart');
    horizontalBar('topContributorsByObjectsChart', top.results.map(r => r.name), top.results.map(r => r.value), 'Objects changed', 'contributor');
});
loadWidget('topEditorsByObjectsChart', apiUrl('/api/changesets/toplist/', { dimension: 'editor', metric: 'objects' }), top => {
    showChart('topEditorsByObjectsChart');
    horizontalBar('topEditorsByObjectsChart', top.results.map(r => r.name), top.results.map(r => r.value), 'Objects changed', 'editor');
});
// No click-to-filter: hashtag isn't a filter (yet), only a ranking dimension.
loadWidget('topHashtagsByObjectsChart', apiUrl('/api/changesets/toplist/', { dimension: 'hashtag', metric: 'objects' }), top => {
    showChart('topHashtagsByObjectsChart');
    horizontalBar('topHashtagsByObjectsChart', top.results.map(r => `#${r.name}`), top.results.map(r => r.value), 'Objects changed');
});

wireSuggestions('contributor', 'contributor-suggestions', 'contributor');
wireSuggestions('editor',      'editor-suggestions',      'editor');
wireSuggestions('imagery',     'imagery-suggestions',     'imagery');
wireSuggestions('country',     'country-suggestions',     'country');
