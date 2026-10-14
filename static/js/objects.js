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
    const counts = volume.series[0].counts;
    lineChart('objectsChart', volume.dates, counts, 'Objects changed', i => openDrilldown({
        bucket: volume.dates[i], value: counts[i], what: 'Objects changed', objectFilter: {},
    }));
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
    const series = fixedOrderSeries(data, ACTION_ORDER, ACTION_LABELS);
    multiLineChart('objectsByActionChart', data.dates, series, false, (i, s) => openDrilldown({
        bucket: data.dates[i], value: series[s].values[i], what: `${series[s].name} objects`,
        objectFilter: { action: ACTION_ORDER[s] },
    }));
});

loadWidget('objectsByTypeChart', apiUrl('/api/changesets/timeseries/', { metric: 'objects', group_by: 'object_type' }), data => {
    showChart('objectsByTypeChart');
    const series = fixedOrderSeries(data, TYPE_ORDER, TYPE_LABELS);
    multiLineChart('objectsByTypeChart', data.dates, series, false, (i, s) => openDrilldown({
        bucket: data.dates[i], value: series[s].values[i], what: series[s].name,
        objectFilter: { object_type: TYPE_ORDER[s] },
    }));
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
    multiLineChart('featuresOverTimeChart', data.dates, series.map(s => ({ name: s.name, values: s.counts })), false, (i, s) => openDrilldown({
        bucket: data.dates[i], value: series[s].counts[i], what: `${series[s].name} objects`,
        objectFilter: { feature: series[s].name },
    }));
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

// ── Top contributors over time ───────────────────────────────────────────────
// group_by=contributor, metric=objects; the buttons narrow it to created,
// modified or deleted objects (action=, from the diffs). Clicking a point
// opens the drill-down for that contributor and bucket.
const contributorsChart = {
    action: '',
    buttons: document.querySelectorAll('#contributorsOverTime-actions button'),
    url() {
        const extra = { group_by: 'contributor', metric: 'objects' };
        if (this.action) extra.action = this.action;
        return apiUrl('/api/changesets/timeseries/', extra);
    },
    render(data) {
        const action = this.action;
        const what = action ? `${ACTION_LABELS[action]} objects` : 'Objects changed';
        const series = data.series.slice(0, MAX_SERIES).map(s => ({ name: s.name, values: s.counts }));
        const note = document.getElementById('contributorsOverTimeNote');
        note.textContent = series.length
            ? `The ${series.length} contributors with the most ${action ? `${ACTION_LABELS[action].toLowerCase()} objects` : 'objects changed'}. Click a point to see that contributor's changesets`
            : 'No objects in this range';
        if (action && data.objects_since && data.objects_since > data.filters.start_date) {
            note.textContent += ` (counted from ${data.objects_since}, the first day of the replication diffs)`;
        }
        showChart('contributorsOverTimeChart');
        multiLineChart('contributorsOverTimeChart', data.dates, series, false, (i, s) => openDrilldown({
            bucket: data.dates[i], value: series[s].values[i], what,
            objectFilter: action ? { action } : {}, extraFilter: { contributor: series[s].name },
        }));
    },
    select(action) {
        this.action = action;
        this.buttons.forEach(b => {
            const on = b.dataset.action === action;
            b.classList.toggle('bg-blue-50', on);
            b.classList.toggle('border-blue-400', on);
            b.classList.toggle('text-blue-700', on);
            b.setAttribute('aria-pressed', on);
        });
    },
};
contributorsChart.select('');
loadWidget('contributorsOverTimeChart', contributorsChart.url(), data => contributorsChart.render(data));
contributorsChart.buttons.forEach(button => button.addEventListener('click', () => {
    if (button.dataset.action === contributorsChart.action) return;
    contributorsChart.select(button.dataset.action);
    const canvas = document.getElementById('contributorsOverTimeChart');
    canvas.style.opacity = 0.4;
    const requested = contributorsChart.action;
    fetchJson(contributorsChart.url())
        .then(data => { if (requested === contributorsChart.action) contributorsChart.render(data); })
        .catch(err => {
            console.error('Failed to load contributors over time', err);
            document.getElementById('contributorsOverTimeNote').textContent = 'Failed to load';
        })
        .finally(() => { canvas.style.opacity = 1; });
}));

// ── Drill-down panel ─────────────────────────────────────────────────────────
// Who and which changesets are behind one point of an objects-over-time
// chart: the same filters as the page, narrowed to that point's hour or day
// (hour=) and to its series (action / object_type / feature, or a
// contributor). Each section is one API call, fetched in parallel.
const drilldown = (() => {
    const panel = document.getElementById('drilldownPanel');
    const body = document.getElementById('drilldownPanel-body');
    let abort = null;

    function close() {
        if (abort) abort.abort();
        abort = null;
        panel.style.display = 'none';
    }
    document.getElementById('drilldownPanel-close').addEventListener('click', close);
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && abort) close(); });

    // "2026-10-09 11:00" (hourly point) or "2026-10-09" (daily point).
    function bucketParams(bucket) {
        const [day, time] = bucket.split(' ');
        const params = { start_date: day, end_date: day };
        if (time) params.hour = parseInt(time, 10);
        return params;
    }
    function bucketLabel(bucket) {
        const [day, time] = bucket.split(' ');
        const date = `${shortDate(day)}, ${day.slice(0, 4)}`;
        if (!time) return `${date} (UTC day)`;
        const next = String((parseInt(time, 10) + 1) % 24).padStart(2, '0');
        return `${date}, ${time}–${next}:00 UTC`;
    }

    function section(title) {
        const wrap = document.createElement('section');
        const h = document.createElement('h5');
        h.className = 'text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2';
        h.textContent = title;
        const content = document.createElement('div');
        content.className = 'text-xs text-gray-400';
        content.textContent = 'Loading…';
        wrap.append(h, content);
        body.appendChild(wrap);
        return content;
    }

    // Ranked names with a bar showing each one's share of the clicked total.
    // Built with DOM nodes, not innerHTML: names are user input.
    function rankedList(content, results, total, filterField) {
        content.textContent = '';
        content.className = '';
        if (!results.length) {
            content.className = 'text-xs text-gray-400';
            content.textContent = 'Nothing in this bucket';
            return;
        }
        const list = document.createElement('ol');
        list.className = 'space-y-1';
        results.forEach(r => {
            const li = document.createElement('li');
            const line = document.createElement('div');
            line.className = 'flex items-baseline justify-between gap-2';
            let name;
            if (filterField) {
                name = document.createElement('button');
                name.type = 'button';
                name.className = 'text-left text-blue-600 hover:underline truncate';
                name.title = `Filter the page by this ${filterField}`;
                name.addEventListener('click', () => applyFilter(filterField, r.name));
            } else {
                name = document.createElement('span');
                name.className = 'text-gray-800 truncate';
            }
            name.textContent = r.name;
            const value = document.createElement('span');
            value.className = 'tabular-nums text-gray-600 whitespace-nowrap';
            const share = total ? r.value / total : 0;
            value.textContent = `${r.value.toLocaleString('en-US')}${total ? ` · ${(share * 100).toFixed(share < 0.1 ? 1 : 0)}%` : ''}`;
            line.append(name, value);
            const bar = document.createElement('div');
            bar.className = 'h-1 rounded bg-gray-100';
            const fill = document.createElement('div');
            fill.className = 'h-1 rounded';
            fill.style.background = RANKING_COLOR;
            fill.style.width = `${Math.min(share, 1) * 100}%`;
            bar.appendChild(fill);
            li.append(line, bar);
            list.appendChild(li);
        });
        content.appendChild(list);
    }

    function changesetList(content, results, filtered) {
        content.textContent = '';
        content.className = '';
        if (!results.length) {
            content.className = 'text-xs text-gray-400';
            content.textContent = 'No changesets in this bucket';
            return;
        }
        const list = document.createElement('ol');
        list.className = 'divide-y divide-gray-100';
        results.forEach(r => {
            const li = document.createElement('li');
            li.className = 'py-2';
            const top = document.createElement('div');
            top.className = 'flex items-baseline justify-between gap-2';
            const links = document.createElement('span');
            const osm = document.createElement('a');
            osm.href = `https://www.openstreetmap.org/changeset/${r.changeset_id}`;
            osm.target = '_blank';
            osm.rel = 'noopener';
            osm.className = 'font-medium text-blue-600 hover:underline';
            osm.textContent = r.changeset_id;
            const osmcha = document.createElement('a');
            osmcha.href = `https://osmcha.org/changesets/${r.changeset_id}`;
            osmcha.target = '_blank';
            osmcha.rel = 'noopener';
            osmcha.className = 'ml-2 text-xs text-gray-400 hover:text-blue-600 hover:underline';
            osmcha.textContent = 'OSMCha';
            links.append(osm, osmcha);
            const when = document.createElement('span');
            when.className = 'text-xs text-gray-400 whitespace-nowrap';
            when.textContent = r.created_at.slice(0, 16).replace('T', ' ');
            top.append(links, when);
            const meta = document.createElement('div');
            meta.className = 'text-xs text-gray-600';
            const objects = filtered
                ? `${r.objects.toLocaleString('en-US')} of ${r.changes_count.toLocaleString('en-US')} objects`
                : `${r.changes_count.toLocaleString('en-US')} objects`;
            meta.textContent = [r.user, r.editor, r.country, objects].filter(Boolean).join(' · ');
            li.append(top, meta);
            if (r.comment) {
                const comment = document.createElement('div');
                comment.className = 'text-xs text-gray-400 truncate';
                comment.textContent = r.comment;
                comment.title = r.comment;
                li.append(comment);
            }
            list.appendChild(li);
        });
        content.appendChild(list);
    }

    function fill(content, url, signal, render) {
        fetchJson(url, { signal })
            .then(render)
            .catch(err => {
                if (err.name === 'AbortError') return;
                console.error(`Failed to load ${url}`, err);
                content.className = 'text-xs text-red-600';
                content.textContent = 'Failed to load';
            });
    }

    // ctx: { bucket, value, what, objectFilter: {action|object_type|feature}, extraFilter: {contributor}? }
    return function open(ctx) {
        if (abort) abort.abort();
        abort = new AbortController();
        const { signal } = abort;
        const scope = { ...bucketParams(ctx.bucket), ...ctx.objectFilter, ...(ctx.extraFilter || {}) };
        const filtered = Object.keys(ctx.objectFilter).length > 0;
        const objects = { metric: 'objects', ...scope };

        const who = ctx.extraFilter && ctx.extraFilter.contributor;
        document.getElementById('drilldownPanel-title').textContent =
            `${ctx.what}${who ? ` by ${who}` : ''} · ${ctx.value.toLocaleString('en-US')}`;
        document.getElementById('drilldownPanel-subtitle').textContent =
            `${bucketLabel(ctx.bucket)} · with the page's filters`;
        body.textContent = '';
        panel.style.display = 'flex';

        // Changesets first: they're what answers "what happened here".
        const changesets = section('Largest changesets');
        if (!who) {
            const contributors = section('Contributors');
            fill(contributors, apiUrl('/api/changesets/toplist/', { ...objects, dimension: 'contributor', limit: 10 }), signal,
                 data => rankedList(contributors, data.results, ctx.value, 'contributor'));
        }
        const editors = section('Editors');
        fill(editors, apiUrl('/api/changesets/toplist/', { ...objects, dimension: 'editor', limit: 5 }), signal,
             data => rankedList(editors, data.results, ctx.value, 'editor'));
        const byAction = Boolean(ctx.objectFilter.feature);
        const what = section(byAction ? 'Created, modified, deleted' : 'Features');
        fill(what, apiUrl('/api/changesets/toplist/', { ...objects, dimension: byAction ? 'action' : 'feature', limit: 8 }), signal,
             data => rankedList(what, data.results, ctx.value));
        fill(changesets, apiUrl('/api/changesets/largest/', { by: 'objects', limit: 10, ...scope }), signal,
             data => changesetList(changesets, data.results, filtered));
    };
})();

function openDrilldown(ctx) {
    drilldown(ctx);
}

wireSuggestions('contributor', 'contributor-suggestions', 'contributor');
wireSuggestions('editor',      'editor-suggestions',      'editor');
wireSuggestions('imagery',     'imagery-suggestions',     'imagery');
wireSuggestions('country',     'country-suggestions',     'country');
