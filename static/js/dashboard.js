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
const NONE_BUCKET = '(none)';   // cagg_imagery_daily/cagg_locale_daily's bucket for changesets with no tag (see views.py) — gets a normal categorical color, not OTHER_COLOR: it's real, often-dominant volume (not a leftover-tail catch-all like "Other"), so graying it out would hide exactly the spikes it exists to reveal
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

// KPI numbers (total changesets/objects) can run into the hundreds of
// millions at full history scale — Intl's built-in compact notation ("14M",
// "126K") instead of hand-rolled suffix logic. Full precision is still
// available on hover via a small custom tooltip (elementId + '-tooltip' in
// the template) rather than the native `title` attribute — title's hover
// delay and complete absence on touch devices made it easy to miss.
const compactNumber = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });
function setKpiNumber(elementId, value) {
    document.getElementById(elementId).textContent = compactNumber.format(value);
    document.getElementById(`${elementId}-tooltip`).textContent = value.toLocaleString('en-US');
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
            maintainAspectRatio: false,
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
// every filter (start_date/end_date/contributor/editor/imagery/language) already
// lives in the URL and every fetch reads from it, so this needs no client-
// side re-fetch orchestration, just a normal navigation to the new query.
// Additive: only the clicked field changes, any other filter already set
// stays in place.
function applyFilter(field, value) {
    const params = new URLSearchParams(window.location.search);
    params.set(field, value);
    window.location.search = params.toString();
}

// Same "URL is the single source of truth" navigation as applyFilter, but
// for the Time Range presets — those set start_date *and* end_date together,
// so they can't reuse applyFilter's single-field form. Any other filter
// already set (contributor/editor/imagery/language) stays in place.
function applyDateRangePreset(days) {
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - days);
    const iso = d => d.toISOString().slice(0, 10);
    const params = new URLSearchParams(window.location.search);
    params.set('start_date', iso(start));
    params.set('end_date', iso(end));
    window.location.search = params.toString();
}
document.querySelectorAll('.date-preset-btn').forEach(btn => {
    btn.addEventListener('click', () => applyDateRangePreset(parseInt(btn.dataset.days, 10)));
});

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

// Grid cells with an unreliable bbox are already excluded server-side (see
// GeoView) — geo.cells is safe to plot as-is.
//
// Drawn as actual rectangles (one per cell, exact bounds from
// grid_size_degrees), not a Leaflet.heat blurred/interpolated blob layer —
// a heat blob only approximates a smooth surface *between* cell centers,
// which reads as "imprecise" and gives zooming in no real extra detail
// past the cell resolution. Rectangles show the true cell boundary and
// color, and make it visually honest that 0.5° is the actual resolution
// of the underlying data (see TODO.md if that resolution itself needs to
// change — that's a CAgg rebuild, not a rendering choice).
//
// Sequential blue ramp, light→dark, one hue not a rainbow (dataviz skill's
// palette.md) — quantized into GEO_COLOR_RAMP.length bins on a log scale:
// raw counts are heavily skewed (a few dense urban cells vs. many sparse
// ones), so equal-width bins on the raw count would put nearly every cell
// in the lightest bucket. `preferCanvas` (set on the map, below) renders
// the (potentially thousands of) rectangles on canvas instead of one SVG
// element each, which matters once cell count climbs into the thousands.
const GEO_COLOR_RAMP = ['#cde2fb', '#9ec5f4', '#5598e7', '#2a78d6', '#1c5cab', '#0d366b'];

// Two resolutions, one metric choice — see GeoView/changesets/geo.py.
// Coarse (0.5°, global) loads once at page load; fine (~0.05°, scoped to
// the current viewport) is fetched on demand once the user zooms in past
// GEO_FINE_ZOOM_THRESHOLD, debounced so panning/zooming rapidly doesn't
// fire a request per frame. Only one resolution is ever shown at a time —
// switching between already-fetched coarse/fine data is instant (no
// refetch); only a *new* viewport while already zoomed in triggers one.
// The metric toggle (count vs. objects) never refetches either — both
// values are already in every cell, so it just restyles whichever
// layer is currently on screen.
const GEO_FINE_ZOOM_THRESHOLD = 7;
const GEO_FINE_FETCH_DEBOUNCE_MS = 400;
const GEO_METRICS = { count: 'Changesets', objects: 'Objects changed' };

function geoColorScale(cells, metric) {
    const logMax = Math.log1p(Math.max(...cells.map(c => c[metric]), 0)) || 1;
    return cell => GEO_COLOR_RAMP[Math.min(GEO_COLOR_RAMP.length - 1, Math.floor((Math.log1p(cell[metric]) / logMax) * GEO_COLOR_RAMP.length))];
}

function drawGeoCells(layerGroup, cells, gridSizeDegrees, metric) {
    layerGroup.clearLayers();
    const half = gridSizeDegrees / 2;
    const colorFor = geoColorScale(cells, metric);
    cells.forEach(c => {
        L.rectangle([[c.lat - half, c.lon - half], [c.lat + half, c.lon + half]], {
            stroke: false,
            fillColor: colorFor(c),
            fillOpacity: 0.85,
        })
            .bindTooltip(`${c.count.toLocaleString()} changesets<br>${c.objects.toLocaleString()} objects`, { sticky: true })
            .addTo(layerGroup);
    });
}

// Bins are log-scale, so a bare color swatch would be uninterpretable —
// label each with the real value (relative to this view's own max, same
// as the color scale itself) it tops out at.
function updateGeoLegend(legendDiv, cells, metric) {
    const logMax = Math.log1p(Math.max(...cells.map(c => c[metric]), 0)) || 1;
    legendDiv.innerHTML = `<div class="font-medium text-gray-700 mb-1">${GEO_METRICS[metric]}</div>` + GEO_COLOR_RAMP.map((color, i) => {
        const upper = Math.round(Math.expm1((i + 1) / GEO_COLOR_RAMP.length * logMax));
        return `<div class="flex items-center gap-1.5"><span style="display:inline-block;width:12px;height:12px;background:${color}"></span>up to ${upper.toLocaleString()}</div>`;
    }).join('');
}

function renderGeoMap(initialGeo) {
    const map = L.map('geoMap', { preferCanvas: true }).setView([20, 0], 2);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
        maxZoom: 19,
        detectRetina: true,
    }).addTo(map);

    const coarseLayer = L.layerGroup().addTo(map);
    const fineLayer = L.layerGroup();
    const coarseCells = initialGeo.cells;
    const coarseGridSize = initialGeo.grid_size_degrees;
    let fineCells = [];
    let fineGridSize = null;
    let metric = 'count';
    let showingFine = false;

    let legendDiv;
    const legend = L.control({ position: 'bottomright' });
    legend.onAdd = () => {
        legendDiv = L.DomUtil.create('div', 'bg-white rounded-md shadow-lg px-3 py-2 text-xs leading-relaxed');
        return legendDiv;
    };
    legend.addTo(map);

    function redraw() {
        const cells = showingFine ? fineCells : coarseCells;
        const gridSize = showingFine ? fineGridSize : coarseGridSize;
        drawGeoCells(showingFine ? fineLayer : coarseLayer, cells, gridSize, metric);
        updateGeoLegend(legendDiv, cells, metric);
    }

    // Segmented control, top-right: switch which value drives cell color.
    // disableClickPropagation so a click/scroll on the control doesn't
    // also pan/zoom the map underneath it.
    const metricControl = L.control({ position: 'topright' });
    metricControl.onAdd = () => {
        const div = L.DomUtil.create('div', 'bg-white rounded-md shadow-lg text-xs overflow-hidden flex');
        L.DomEvent.disableClickPropagation(div);
        div.innerHTML = Object.entries(GEO_METRICS).map(([key, label]) =>
            `<button type="button" data-metric="${key}" class="geo-metric-btn px-2 py-1.5 ${key === metric ? 'bg-blue-500 text-white' : 'text-gray-700 hover:bg-gray-100'}">${label}</button>`
        ).join('');
        div.querySelectorAll('.geo-metric-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                metric = btn.dataset.metric;
                div.querySelectorAll('.geo-metric-btn').forEach(b => {
                    const active = b.dataset.metric === metric;
                    b.classList.toggle('bg-blue-500', active);
                    b.classList.toggle('text-white', active);
                    b.classList.toggle('text-gray-700', !active);
                });
                redraw();
            });
        });
        return div;
    };
    metricControl.addTo(map);

    redraw();

    let fineFetchTimer = null;
    function scheduleFineFetch() {
        clearTimeout(fineFetchTimer);
        fineFetchTimer = setTimeout(() => {
            const b = map.getBounds();
            const bbox = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
            fetchJson(apiUrl('/api/changesets/geo/', { resolution: 'fine', bbox }))
                .then(geo => {
                    fineCells = geo.cells;
                    fineGridSize = geo.grid_size_degrees;
                    if (showingFine) redraw();
                })
                .catch(err => console.error('Failed to load fine-resolution geo data', err));
        }, GEO_FINE_FETCH_DEBOUNCE_MS);
    }

    map.on('zoomend moveend', () => {
        const isFine = map.getZoom() >= GEO_FINE_ZOOM_THRESHOLD;
        if (isFine !== showingFine) {
            showingFine = isFine;
            if (showingFine) { coarseLayer.remove(); fineLayer.addTo(map); }
            else { fineLayer.remove(); coarseLayer.addTo(map); }
            redraw();
        }
        if (showingFine) scheduleFineFetch();
    });

    map.invalidateSize(); // container was `hidden` (zero-size) at construction time
}
loadWidget('geoMap', apiUrl('/api/changesets/geo/'), geo => {
    showChart('geoMap');
    renderGeoMap(geo);
});

loadWidget('topEditorsChart', apiUrl('/api/changesets/toplist/', { dimension: 'editor', metric: 'count' }), topEditors => {
    showChart('topEditorsChart');
    horizontalBar('topEditorsChart', topEditors.results.map(r => r.name), topEditors.results.map(r => r.value), 'Changesets', 'editor');
    setDatalistOptions('editor-list', topEditors.results.map(r => r.name));
});
loadWidget('topEditorsTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'editor' }), editorsTime => {
    showChart('topEditorsTimeChart');
    stackedBar('topEditorsTimeChart', editorsTime.dates, editorsTime.series);
});

loadWidget('topImageriesChart', apiUrl('/api/changesets/toplist/', { dimension: 'imagery', metric: 'count' }), topImageries => {
    showChart('topImageriesChart');
    horizontalBar('topImageriesChart', topImageries.results.map(r => r.name), topImageries.results.map(r => r.value), 'Changesets', 'imagery');
    setDatalistOptions('imagery-list', topImageries.results.map(r => r.name));
});
loadWidget('topImageriesTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'imagery' }), imageriesTime => {
    showChart('topImageriesTimeChart');
    stackedBar('topImageriesTimeChart', imageriesTime.dates, imageriesTime.series);
});

loadWidget('topLocalesChart', apiUrl('/api/changesets/toplist/', { dimension: 'language', metric: 'count' }), topLocales => {
    showChart('topLocalesChart');
    horizontalBar('topLocalesChart', topLocales.results.map(r => r.name), topLocales.results.map(r => r.value), 'Changesets', 'language');
});
loadWidget('topLocalesTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'language' }), localesTime => {
    showChart('topLocalesTimeChart');
    stackedBar('topLocalesTimeChart', localesTime.dates, localesTime.series);
});

loadWidget('topContributorsChart', apiUrl('/api/changesets/toplist/', { dimension: 'contributor', metric: 'count' }), topContributors => {
    showChart('topContributorsChart');
    horizontalBar('topContributorsChart', topContributors.results.map(r => r.name), topContributors.results.map(r => r.value), 'Changesets', 'contributor');
    setDatalistOptions('contributor-list', topContributors.results.map(r => r.name));
});
loadWidget('topContributorsTimeChart', apiUrl('/api/changesets/timeseries/', { group_by: 'contributor' }), contributorsTime => {
    showChart('topContributorsTimeChart');
    stackedBar('topContributorsTimeChart', contributorsTime.dates, contributorsTime.series);
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
        document.getElementById('language').value = summary.filters.language;

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

// ── Filter autocomplete ─────────────────────────────────────────────────────

// Shared by the on-load seeding above (top names from the ranking widgets'
// own toplist responses) and wireAutocomplete below (narrowed /api/autocomplete/
// matches once the user starts typing) — same <option> rendering either way.
function setDatalistOptions(datalistId, names) {
    document.getElementById(datalistId).innerHTML = names.map(v => `<option value="${v}"></option>`).join('');
}

// Datalists start pre-filled with each dimension's top ~20 names (set by the
// loadWidget callbacks above, reusing the toplist responses already fetched
// for the ranking charts — no extra request). This only replaces those
// options once the user actually types something (q.length < 1 guard), so
// the pre-filled list stays in place, unnarrowed, until then.
function wireAutocomplete(inputId, datalistId, field) {
    document.getElementById(inputId).addEventListener('input', function () {
        const q = this.value;
        if (q.length < 1) return;
        fetch(`/api/autocomplete/?field=${field}&q=${encodeURIComponent(q)}`)
            .then(r => r.json())
            .then(values => setDatalistOptions(datalistId, values));
    });
}
wireAutocomplete('contributor', 'contributor-list', 'contributor');
wireAutocomplete('editor',      'editor-list',      'editor');
wireAutocomplete('imagery',     'imagery-list',     'imagery');
