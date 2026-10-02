// ── Main dashboard widgets ───────────────────────────────────────────────────
// Generic chart factories / apiUrl/fetchJson/loadWidget / renderGeoMap /
// autocomplete helpers now live in common.js (loaded before this file) —
// this file only wires this page's specific DOM ids to specific API calls.
// See CLAUDE.md's "Template/JS separation" architecture note.

function renderVolumeChart(volume) {
    lineChart('changesetChart', volume.dates, volume.series[0].counts, 'Changesets');
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
    stackedBar('topEditorsTimeChart', editorsTime.dates, editorsTime.series);
});

loadWidget('topImageriesChart', apiUrl('/api/changesets/toplist/', { dimension: 'imagery', metric: 'count' }), topImageries => {
    showChart('topImageriesChart');
    horizontalBar('topImageriesChart', topImageries.results.map(r => r.name), topImageries.results.map(r => r.value), 'Changesets', 'imagery');
    cacheTopValues('imagery', topImageries.results.map(r => r.name));
});
loadWidget('topImageriesTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'imagery' }), imageriesTime => {
    showChart('topImageriesTimeChart');
    stackedBar('topImageriesTimeChart', imageriesTime.dates, imageriesTime.series);
});

loadWidget('topCountriesChart', apiUrl('/api/changesets/toplist/', { dimension: 'country', metric: 'count' }), topCountries => {
    showChart('topCountriesChart');
    horizontalBar('topCountriesChart', topCountries.results.map(r => r.name), topCountries.results.map(r => r.value), 'Changesets', 'country');
    cacheTopValues('country', topCountries.results.map(r => r.name));
});
loadWidget('topCountriesTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'country' }), countriesTime => {
    showChart('topCountriesTimeChart');
    stackedBar('topCountriesTimeChart', countriesTime.dates, countriesTime.series);
});

loadWidget('topContributorsChart', apiUrl('/api/changesets/toplist/', { dimension: 'contributor', metric: 'count' }), topContributors => {
    showChart('topContributorsChart');
    horizontalBar('topContributorsChart', topContributors.results.map(r => r.name), topContributors.results.map(r => r.value), 'Changesets', 'contributor');
    cacheTopValues('contributor', topContributors.results.map(r => r.name));
});
loadWidget('topContributorsTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'contributor' }), contributorsTime => {
    showChart('topContributorsTimeChart');
    stackedBar('topContributorsTimeChart', contributorsTime.dates, contributorsTime.series);
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
