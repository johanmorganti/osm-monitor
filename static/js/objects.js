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
        { name: 'p75', values: sizes.groups.map(g => g.p75) },
        { name: 'p90', values: sizes.groups.map(g => g.p90) },
        { name: 'p95', values: sizes.groups.map(g => g.p95) },
    ], true);
});

loadWidget('sizeByEditorChart', apiUrl('/api/changesets/distribution/breakdown/', { by: 'editor' }), sizes => {
    showChart('sizeByEditorChart');
    quartileChart('sizeByEditorChart', sizes.groups, 'editor');
});

// ── What was changed (replication diffs) ─────────────────────────────────────
// group_by/dimension = action | object_type | feature with metric=objects.
// Colors follow the name, not its rank: series are put in a fixed order
// before charting. Each response carries objects_since (the first day the
// diffs cover); earlier days count nothing, so the page says so.
const ACTION_ORDER = ['create', 'modify', 'delete'];
const ACTION_LABELS = { create: 'Created', modify: 'Modified', delete: 'Deleted' };
const TYPE_ORDER = ['node', 'way', 'relation'];
const TYPE_LABELS = { node: 'Nodes', way: 'Ways', relation: 'Relations' };
// Not features: way geometry nodes (~70% of objects) and deletes (the diffs
// carry no tags for them). Charted, they would flatten every real feature.
const NOT_FEATURES = ['untagged', 'unknown'];
const TOP_FEATURE_SERIES = 6;

function showObjectsSince(data) {
    if (!data.objects_since) return;
    const since = data.objects_since;
    const el = document.getElementById('objectsSince');
    el.textContent = since > data.filters.start_date
        ? `Counted from ${since} only: the diffs this comes from don't go back further, so earlier days of the range count nothing.`
        : `Counted from OpenStreetMap's replication diffs, available since ${since}.`;
}

function fixedOrderSeries(data, order, labels) {
    return order.map(name => {
        const s = data.series.find(x => x.name === name);
        return { name: labels[name], values: s ? s.counts : data.dates.map(() => 0) };
    });
}

loadWidget('objectsByActionChart', apiUrl('/api/changesets/timeseries/', { metric: 'objects', group_by: 'action' }), data => {
    showObjectsSince(data);
    showChart('objectsByActionChart');
    multiLineChart('objectsByActionChart', data.dates, fixedOrderSeries(data, ACTION_ORDER, ACTION_LABELS));
});

loadWidget('objectsByTypeChart', apiUrl('/api/changesets/timeseries/', { metric: 'objects', group_by: 'object_type' }), data => {
    showChart('objectsByTypeChart');
    multiLineChart('objectsByTypeChart', data.dates, fixedOrderSeries(data, TYPE_ORDER, TYPE_LABELS));
});

loadWidget('topFeaturesChart', apiUrl('/api/changesets/toplist/', { dimension: 'feature', metric: 'objects', limit: 30 }), top => {
    const left = top.results.filter(r => NOT_FEATURES.includes(r.name));
    const shown = top.results.filter(r => !NOT_FEATURES.includes(r.name)).slice(0, 15);
    const count = name => (left.find(r => r.name === name) || { value: 0 }).value;
    document.getElementById('topFeaturesNote').textContent =
        `Objects created or modified, by their main tag. Not shown: ${compactNumber.format(count('untagged'))} untagged objects (mostly nodes shaping ways) and ${compactNumber.format(count('unknown'))} deletes (the diffs don't say what they were)`;
    showChart('topFeaturesChart');
    horizontalBar('topFeaturesChart', shown.map(r => r.name), shown.map(r => r.value), 'Objects changed');
});

loadWidget('featuresOverTimeChart', apiUrl('/api/changesets/timeseries/', { metric: 'objects', group_by: 'feature' }), data => {
    const series = data.series.filter(s => !NOT_FEATURES.includes(s.name)).slice(0, TOP_FEATURE_SERIES);
    showChart('featuresOverTimeChart');
    multiLineChart('featuresOverTimeChart', data.dates, series.map(s => ({ name: s.name, values: s.counts })));
});

// Most edited objects over the last 7 days (a fixed window: the date range
// above doesn't apply, the other filters do). Built with DOM nodes, not
// innerHTML: names are user input.
const TYPE_SINGULAR = { node: 'Node', way: 'Way', relation: 'Relation' };
const MOST_EDITED_COLUMNS = [
    { label: 'Object', cell: r => {
        const a = document.createElement('a');
        a.href = `https://www.openstreetmap.org/${r.type}/${r.id}/history`;
        a.target = '_blank';
        a.rel = 'noopener';
        a.className = 'text-blue-600 hover:underline';
        a.textContent = `${TYPE_SINGULAR[r.type]} ${r.id}`;
        return a;
    } },
    { label: 'Name', wide: true, cell: r => r.name || '' },
    { label: 'Edits', numeric: true, cell: r => r.edits.toLocaleString('en-US') },
    { label: 'Contributors', numeric: true, cell: r => r.contributors.toLocaleString('en-US') },
    { label: 'Changesets', numeric: true, cell: r => r.changesets.toLocaleString('en-US') },
    { label: 'Version', numeric: true, cell: r => r.version.toLocaleString('en-US') },
    { label: 'Last edit', cell: r => r.last_edit.slice(0, 16).replace('T', ' ') },
];

loadWidget('mostEditedTable', apiUrl('/api/objects/most-edited/'), top => {
    renderTable('mostEditedTable', MOST_EDITED_COLUMNS, top.results, 'No edits in the last 7 days');
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

// A table from column specs: {label, cell(row) -> text or Node, numeric?, wide?}.
function renderTable(tableId, columns, results, emptyText) {
    const table = document.getElementById(tableId);
    const head = table.createTHead().insertRow();
    head.className = 'text-left text-xs text-gray-500 uppercase tracking-wide border-b border-gray-200';
    columns.forEach(col => {
        const th = document.createElement('th');
        th.className = `py-2 pr-4 font-medium${col.numeric ? ' text-right' : ''}`;
        th.textContent = col.label;
        head.appendChild(th);
    });
    const body = table.createTBody();
    results.forEach(r => {
        const row = body.insertRow();
        row.className = 'border-b border-gray-100 align-top';
        columns.forEach(col => {
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
        td.colSpan = columns.length;
        td.className = 'py-4 text-gray-400';
        td.textContent = emptyText;
    }
    table.hidden = false;
}

loadWidget('largestByObjectsTable', apiUrl('/api/changesets/largest/', { by: 'objects' }), largest => {
    renderTable('largestByObjectsTable', LARGEST_COLUMNS, largest.results, 'No changesets in this range');
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
