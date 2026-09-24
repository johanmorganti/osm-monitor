// ── Editors page widgets ─────────────────────────────────────────────────────
// One column per named editor family, plus a trailing "Other editor
// families" column — mirrors the original Datadog "OpenStreetMap Editors
// focus" dashboard's per-family layout. Each column has its own
// version-drill-down toplist, its own volume-over-time graph, and a small
// changesets/objects/ratio stats line.
//
// TOP_FAMILIES is a fixed, hand-picked list (the top 10 by changeset count
// plus OsmAnd, as of 2026-09 — see the initial dynamic version of this page
// this replaced), not computed from a ranking API call — that call was
// itself the reason the page took a long time to show anything, since
// every card was gated behind it resolving first. Hardcoding trades "the
// list self-updates as rankings shift" for "every card starts loading the
// instant the script runs" — worth it since the top of this ranking is
// stable over any reasonable timescale (see the "Other editor families"
// column for whatever's actually near the boundary).
const TOP_FAMILIES = ['iD', 'StreetComplete', 'JOSM', 'Rapid', 'Vespucci', 'Go Map!!', 'Organic Maps', 'Every Door', 'DeFlock', 'CoMaps', 'OsmAnd'];
// Realistic editor-family cardinality is in the hundreds (long real-world
// tail of one-off/rare created_by strings — confirmed empirically at
// ~800-900 over a year), comfortably under ToplistView's own MAX_LIMIT
// (1000) — one call at this limit gets essentially every family that has
// ever appeared in the range, enough to compute the Others column's
// toplist (everything not in TOP_FAMILIES) with no per-name requests.
const RANKING_LIMIT = 1000;
// Others' own toplist card is still capped for legibility, same "Top 20"
// convention used everywhere else in this app — this does not affect the
// Others graph/stats, which are exact total-minus-named computations, not
// a sum of only these displayed rows.
const OTHERS_DISPLAY_LIMIT = 20;

// This page defaults to the last *year*, not the app-wide 7-day default
// (see _resolve_range_and_filters in views.py) — a meaningful window for
// "top editor families" needs more history than a week. Set client-side,
// before any fetch, via history.replaceState (no reload) so every apiUrl()
// call below (which reads window.location.search) already sees it — same
// "URL is the single source of truth" model as every other filter/date
// control in this app, just with a different default for this one page.
(function ensureDefaultRange() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('start_date') && params.get('end_date')) return;
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 365);
    const iso = d => d.toISOString().slice(0, 10);
    params.set('start_date', params.get('start_date') || iso(start));
    params.set('end_date', params.get('end_date') || iso(end));
    history.replaceState(null, '', `${window.location.pathname}?${params.toString()}`);
})();

// Same compact-number-with-hover-detail treatment as the page-level KPIs
// (setKpiNumber, common.js: textContent = compact form, the paired
// `-tooltip` element = full toLocaleString value, revealed by the
// `group`/`group-hover` wrapper below) — reused as-is, just against these
// per-family element ids instead of totalChangesets/totalObjects. The
// ratio has no tooltip: avg_objects is already the full-precision (1
// decimal) value, nothing more to reveal on hover.
function setFamilyStats(idx, summary) {
    setKpiNumber(`family-${idx}-changesets`, summary.total_changesets);
    setKpiNumber(`family-${idx}-objects`, summary.total_objects);
    document.getElementById(`family-${idx}-ratio`).textContent = `${summary.avg_objects} objects/changeset`;
}

function familyCardHtml(idx, title, subtitle) {
    return `
        <div class="bg-white rounded-lg shadow-lg p-6">
            <h3 class="text-lg font-semibold mb-1">${title}</h3>
            <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500 mb-1">
                <span class="relative inline-block group cursor-help">
                    <span class="border-b border-dotted border-gray-400"><span id="family-${idx}-changesets">–</span> changesets</span>
                    <span id="family-${idx}-changesets-tooltip" class="absolute left-0 bottom-full mb-1 hidden group-hover:block whitespace-nowrap bg-gray-800 text-white text-xs rounded px-2 py-1 z-10"></span>
                </span>
                <span class="relative inline-block group cursor-help">
                    <span class="border-b border-dotted border-gray-400"><span id="family-${idx}-objects">–</span> objects</span>
                    <span id="family-${idx}-objects-tooltip" class="absolute left-0 bottom-full mb-1 hidden group-hover:block whitespace-nowrap bg-gray-800 text-white text-xs rounded px-2 py-1 z-10"></span>
                </span>
                <span id="family-${idx}-ratio">–</span>
            </div>
            <p class="text-xs text-gray-400 mb-3">${subtitle}</p>
            <div class="relative" style="height: 300px">
                <div id="family-${idx}-versions-spinner" class="absolute inset-0 flex items-center justify-center">
                    <div class="animate-spin rounded-full h-6 w-6 border-2 border-blue-500 border-t-transparent"></div>
                </div>
                <canvas id="family-${idx}-versions" hidden></canvas>
            </div>
            <h4 class="text-sm font-medium text-gray-500 mt-6 mb-2">Over Time</h4>
            <div class="relative" style="height: 160px">
                <div id="family-${idx}-graph-spinner" class="absolute inset-0 flex items-center justify-center">
                    <div class="animate-spin rounded-full h-6 w-6 border-2 border-blue-500 border-t-transparent"></div>
                </div>
                <canvas id="family-${idx}-graph" hidden></canvas>
            </div>
        </div>`;
}

const container = document.getElementById('editorFamilyColumns');
const othersIdx = TOP_FAMILIES.length;

// One card per named family, built immediately (no network round-trip
// gates this) — each wires its own stats line (SummaryView), its own
// version-drill-down toplist (dimension=editor_version, backed by
// cagg_editor_version_daily), and its own plain volume-over-time graph
// (editor=<family>, no group_by — the same single-filter fast path every
// other per-name graph in this app already uses). On failure, each
// individually degrades (inline error / 0 fallback) rather than one bad
// request taking down the page or the Others computation below, which
// reuses these same per-family summary/series results.
TOP_FAMILIES.forEach((name, i) => {
    container.insertAdjacentHTML('beforeend', familyCardHtml(i, name, 'Top versions by changeset count'));
    loadWidget(`family-${i}-versions`, apiUrl('/api/changesets/toplist/', { dimension: 'editor_version', editor: name, metric: 'count' }), versions => {
        showChart(`family-${i}-versions`);
        horizontalBar(`family-${i}-versions`, versions.results.map(r => r.name), versions.results.map(r => r.value), 'Changesets');
    });
});

const topSummaryPromises = TOP_FAMILIES.map((name, i) =>
    fetchJson(apiUrl('/api/changesets/summary/', { editor: name }))
        .then(summary => {
            setFamilyStats(i, summary);
            return summary;
        })
        .catch(err => {
            console.error(`Failed to load summary for ${name}`, err);
            document.getElementById(`family-${i}-ratio`).textContent = 'Stats unavailable';
            return { total_changesets: 0, total_objects: 0 };
        })
);

const topSeriesPromises = TOP_FAMILIES.map((name, i) =>
    fetchJson(apiUrl('/api/changesets/timeseries/', { editor: name }))
        .then(volume => {
            document.getElementById(`family-${i}-graph-spinner`).style.display = 'none';
            showChart(`family-${i}-graph`);
            lineChart(`family-${i}-graph`, volume.dates, volume.series[0].counts, name);
            return volume;
        })
        .catch(err => {
            console.error(`Failed to load timeseries for ${name}`, err);
            document.getElementById(`family-${i}-graph-spinner`).innerHTML =
                '<span class="text-sm text-red-600">Failed to load</span>';
            return { dates: [], series: [{ counts: [] }] };
        })
);

// Others: a single trailing card for every family not in TOP_FAMILIES.
container.insertAdjacentHTML('beforeend', familyCardHtml(othersIdx, 'Other editor families', 'Every editor family not shown above'));

// Others' toplist: one high-limit unfiltered toplist call, named families
// filtered out client-side — cheaper than N "not this one" requests, and
// this app's toplist endpoint has no exclude-list filter to begin with.
fetchJson(apiUrl('/api/changesets/toplist/', { dimension: 'editor', metric: 'count', limit: RANKING_LIMIT }))
    .then(ranking => {
        const named = new Set(TOP_FAMILIES.map(n => n.toLowerCase()));
        const rest = ranking.results.filter(r => !named.has(r.name.toLowerCase()));
        document.getElementById(`family-${othersIdx}-versions-spinner`).style.display = 'none';
        showChart(`family-${othersIdx}-versions`);
        const othersDisplay = rest.slice(0, OTHERS_DISPLAY_LIMIT);
        horizontalBar(`family-${othersIdx}-versions`, othersDisplay.map(r => r.name), othersDisplay.map(r => r.value), 'Changesets');
    })
    .catch(err => {
        console.error('Failed to load Others toplist', err);
        document.getElementById(`family-${othersIdx}-versions-spinner`).innerHTML =
            '<span class="text-sm text-red-600">Failed to load</span>';
    });

// Others' graph + stats: exact total-minus-named-11 computation, reusing
// the per-family series/summaries already being fetched above for their
// own cards (plus one shared unfiltered total/summary pair, also reused
// for the page-level KPIs below) rather than firing more requests.
const pageSummaryPromise = fetchJson(apiUrl('/api/changesets/summary/'));

Promise.all([fetchJson(apiUrl('/api/changesets/timeseries/')), pageSummaryPromise, ...topSeriesPromises, ...topSummaryPromises])
    .then(([total, totalSummary, ...rest]) => {
        const topSeries = rest.slice(0, TOP_FAMILIES.length);
        const topSummaries = rest.slice(TOP_FAMILIES.length);

        const othersCounts = total.dates.map((_, i) =>
            total.series[0].counts[i] - topSeries.reduce((sum, s) => sum + (s.series[0].counts[i] || 0), 0)
        );
        document.getElementById(`family-${othersIdx}-graph-spinner`).style.display = 'none';
        showChart(`family-${othersIdx}-graph`);
        lineChart(`family-${othersIdx}-graph`, total.dates, othersCounts, 'Other editor families');

        const othersChangesets = totalSummary.total_changesets - topSummaries.reduce((sum, s) => sum + s.total_changesets, 0);
        const othersObjects = totalSummary.total_objects - topSummaries.reduce((sum, s) => sum + s.total_objects, 0);
        const othersAvg = othersChangesets ? Math.round((othersObjects / othersChangesets) * 10) / 10 : 0;
        setFamilyStats(othersIdx, { total_changesets: othersChangesets, total_objects: othersObjects, avg_objects: othersAvg });
    })
    .catch(err => {
        console.error('Failed to compute Others over-time series/stats', err);
        document.getElementById(`family-${othersIdx}-graph-spinner`).innerHTML =
            '<span class="text-sm text-red-600">Failed to load</span>';
    });

// Page-level KPIs (Total Changesets / Total Objects Changed / Avg Objects
// per Changeset, unfiltered — same 3-number pattern as the main
// dashboard's own KPI rows) + Time Range input seeding.
pageSummaryPromise
    .then(summary => {
        document.getElementById('start_date').value = summary.filters.start_date;
        document.getElementById('end_date').value = summary.filters.end_date;
        setKpiNumber('totalChangesets', summary.total_changesets);
        setKpiNumber('totalObjects', summary.total_objects);
        document.getElementById('avgObjects').textContent = summary.avg_objects;
    })
    .catch(err => {
        console.error('Failed to load summary', err);
        document.getElementById('totalChangesets').textContent = 'Error';
        document.getElementById('totalObjects').textContent = 'Error';
        document.getElementById('avgObjects').textContent = 'Error';
    });
