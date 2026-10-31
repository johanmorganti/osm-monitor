// ── Main dashboard widgets ───────────────────────────────────────────────────
// Generic chart factories / apiUrl/fetchJson/loadWidget / renderGeoMap /
// autocomplete helpers now live in common.js (loaded before this file) —
// this file only wires this page's specific DOM ids to specific API calls.
// See docs/decisions/template-js-separation.md.

// Drill-down panel (openDrilldown, common.js): what's behind a point of a
// changesets-over-time chart, counted in changesets.
const CHANGESET_SECTIONS = ['changesets', 'contributor', 'editor', 'country', 'hashtag'];
const SCOPE_LABELS = {
    contributor: name => `Changesets by ${name}`,
    editor: name => `Changesets with ${name}`,
    imagery: name => `Changesets on ${name} imagery`,
    country: name => `Changesets in ${name}`,
};

function renderVolumeChart(volume) {
    const counts = volume.series[0].counts;
    lineChart('changesetChart', volume.dates, counts, 'Changesets', i => openDrilldown({
        bucket: volume.dates[i], interval: volume.interval, value: counts[i], what: 'Changesets',
        scope: {}, metric: 'count', sections: CHANGESET_SECTIONS,
    }));
}

// A per-name stacked bar chart whose segments open the drill-down for that
// name (the folded "Other" segment isn't one name, so it doesn't).
function namedStackedBar(canvasId, data, dimension) {
    stackedBar(canvasId, data.dates, data.series, (i, name) => {
        const series = data.series.find(s => s.name === name);
        if (!series) return;
        openDrilldown({
            bucket: data.dates[i], interval: data.interval, value: series.counts[i],
            what: SCOPE_LABELS[dimension](name), scope: { [dimension]: name },
            metric: 'count', sections: CHANGESET_SECTIONS,
        });
    });
}

// ── Load ─────────────────────────────────────────────────────────────────────
// The page shell renders instantly with empty charts; these fetches (against
// the standalone /api/changesets/{timeseries,summary,toplist}/ endpoints —
// each independently public, not just for this page's own use) are what
// actually populate them. Each widget fetches and renders independently
// (not gated behind Promise.all) so a slow or failed one only affects its
// own spinner/canvas, not the rest of the page.

loadWidget('changesetChart', apiUrl('/api/changesets/timeseries/'), volume => {
    showChart('changesetChart');
    renderVolumeChart(volume);
});

loadWidget('geoMap', apiUrl('/api/changesets/geo/'), geo => {
    showChart('geoMap');
    renderGeoMap(geo);
});

loadWidget('topEditorsChart', apiUrl('/api/changesets/toplist/', { dimension: 'editor', metric: 'count' }), topEditors => {
    showChart('topEditorsChart');
    horizontalBar('topEditorsChart', topEditors.results.map(r => r.name), topEditors.results.map(r => r.value), 'Changesets', 'editor');
    cacheTopValues('editor', topEditors.results.map(r => r.name));
});
loadWidget('topEditorsTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'editor' }), editorsTime => {
    showChart('topEditorsTimeChart');
    namedStackedBar('topEditorsTimeChart', editorsTime, 'editor');
});

loadWidget('topImageriesChart', apiUrl('/api/changesets/toplist/', { dimension: 'imagery', metric: 'count' }), topImageries => {
    showChart('topImageriesChart');
    horizontalBar('topImageriesChart', topImageries.results.map(r => r.name), topImageries.results.map(r => r.value), 'Changesets', 'imagery');
    cacheTopValues('imagery', topImageries.results.map(r => r.name));
});
loadWidget('topImageriesTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'imagery' }), imageriesTime => {
    showChart('topImageriesTimeChart');
    namedStackedBar('topImageriesTimeChart', imageriesTime, 'imagery');
});

loadWidget('topCountriesChart', apiUrl('/api/changesets/toplist/', { dimension: 'country', metric: 'count' }), topCountries => {
    showChart('topCountriesChart');
    horizontalBar('topCountriesChart', topCountries.results.map(r => r.name), topCountries.results.map(r => r.value), 'Changesets', 'country');
    cacheTopValues('country', topCountries.results.map(r => r.name));
});
loadWidget('topCountriesTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'country' }), countriesTime => {
    showChart('topCountriesTimeChart');
    namedStackedBar('topCountriesTimeChart', countriesTime, 'country');
});

loadWidget('topContributorsChart', apiUrl('/api/changesets/toplist/', { dimension: 'contributor', metric: 'count' }), topContributors => {
    showChart('topContributorsChart');
    horizontalBar('topContributorsChart', topContributors.results.map(r => r.name), topContributors.results.map(r => r.value), 'Changesets', 'contributor');
    cacheTopValues('contributor', topContributors.results.map(r => r.name));
});
loadWidget('topContributorsTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'contributor' }), contributorsTime => {
    showChart('topContributorsTimeChart');
    namedStackedBar('topContributorsTimeChart', contributorsTime, 'contributor');
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
        document.getElementById('country').value = summary.filters.country;

        setKpiNumber('totalChangesets', summary.total_changesets);
    })
    .catch(err => {
        console.error('Failed to load summary', err);
        document.getElementById('totalChangesets').textContent = 'Error';
    });

// Carry the current range and filters over to the Objects page.
document.getElementById('objectsPageLink').href = `/objects/${window.location.search}`;

wireSuggestions('contributor', 'contributor-suggestions', 'contributor');
wireSuggestions('editor',      'editor-suggestions',      'editor');
wireSuggestions('imagery',     'imagery-suggestions',     'imagery');
wireSuggestions('country',     'country-suggestions',     'country');
