// ── Shared dashboard/editors-page helpers ───────────────────────────────────
// Extracted from dashboard.js (pure extraction, no behavior change) so the
// Editors page can reuse the same chart factories, filter/URL helpers, and
// geo map without duplicating ~140 lines of renderGeoMap plus every chart
// factory into a second file. Loaded before dashboard.js/editors.js — no
// bundler in this project, so script-tag order is what enforces this
// dependency (both page-specific files reference these as plain globals).

// ── Design tokens ────────────────────────────────────────────────────────────
// Validated categorical palette (fixed hue order, never cycled — see the
// dataviz skill's references/palette.md). Light mode only; these pages have
// no dark theme.

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
const NONE_BUCKET = '(none)';   // cagg_imagery_daily/cagg_country_daily's bucket for changesets with no tag (see views.py) — gets a normal categorical color, not OTHER_COLOR: it's real, often-dominant volume (not a leftover-tail catch-all like "Other"), so graying it out would hide exactly the spikes it exists to reveal
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
// every filter (start_date/end_date/contributor/editor/imagery/country) already
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
// already set (contributor/editor/imagery/country) stays in place.
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

// Single-series line chart — extracted from dashboard.js's renderVolumeChart
// so any page can draw a plain volume-over-time trend for an arbitrary
// canvas id/label, not just the main dashboard's hardcoded #changesetChart.
function lineChart(canvasId, dates, counts, label) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !dates.length) return;
    new Chart(ctx.getContext('2d'), {
        type: 'line',
        data: {
            labels: dates,
            datasets: [{
                label,
                data: counts,
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
                x: dateAxis(dates),
                y: { beginAtZero: true, grid: { color: GRID_HAIRLINE } },
            },
        },
    });
}

// ── Fetch/widget-loading helpers ─────────────────────────────────────────────
// Each widget fetches and renders independently (not gated behind
// Promise.all) so a slow or failed one only affects its own spinner/canvas,
// not the rest of the page.

function apiUrl(path, extraParams) {
    const params = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(extraParams || {})) {
        params.set(key, value);
    }
    return `${path}?${params.toString()}`;
}

function fetchJson(url, options) {
    return fetch(url, options).then(r => {
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

// ── Geo map ──────────────────────────────────────────────────────────────────
// Grid cells with an unreliable bbox are already excluded server-side (see
// GeoView) — geo.cells is safe to plot as-is.
//
// Drawn as actual rectangles (one per cell, exact bounds from
// lat_size_degrees/lon_size_degrees), not a Leaflet.heat blurred/
// interpolated blob layer — a heat blob only approximates a smooth surface
// *between* cell centers, which reads as "imprecise" and gives zooming in
// no real extra detail past the cell resolution. Rectangles show the true
// (geohash-derived, not square — see drawGeoCells) cell boundary and color,
// and make it visually honest that this is the actual resolution of the
// underlying data (see changesets/geo.py's GEOHASH_PREFIX_LENGTH if that
// resolution itself needs to change — that's a CAgg-key change, not a
// rendering choice).
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
// Coarse (global, cell size adapts to viewport) loads once at page load;
// fine (viewport-scoped, cell size also adapts — see
// geohash_precision_for_bbox in geo.py) is fetched on demand once the user
// zooms in past GEO_FINE_ZOOM_THRESHOLD. Only one resolution is ever shown
// at a time — switching between already-fetched coarse/fine data is
// instant (no refetch). The metric toggle (count vs. objects) never
// refetches either — both values are already in every cell, so it just
// restyles whichever layer is currently on screen.
//
// Panning/zooming while in fine mode does NOT fetch on every settle — see
// scheduleFineFetch's own comments for why a naive "fetch on every
// moveend" was found to be sending far more DB queries than normal map
// browsing needs.
const GEO_FINE_ZOOM_THRESHOLD = 7;
const GEO_FINE_FETCH_DEBOUNCE_MS = 600;
// Fetch this much extra area beyond the visible viewport (as a fraction of
// its own size, per Leaflet's LatLngBounds.pad) so a small pan/zoom-out
// within territory already covered needs no new request at all — reused
// straight from `fineCells` instead. 0.5 = fetch 50% extra on every side,
// i.e. a viewport-sized area of slack in every direction before the next
// pan forces a real fetch.
const GEO_FINE_PREFETCH_PAD = 0.5;
const GEO_METRICS = { count: 'Changesets', objects: 'Objects changed' };

function geoColorScale(cells, metric) {
    const logMax = Math.log1p(Math.max(...cells.map(c => c[metric]), 0)) || 1;
    return cell => GEO_COLOR_RAMP[Math.min(GEO_COLOR_RAMP.length - 1, Math.floor((Math.log1p(cell[metric]) / logMax) * GEO_COLOR_RAMP.length))];
}

// latSizeDegrees/lonSizeDegrees, not one square size: geohash cells aren't
// square (a 6-char cell is ~1.2km wide x ~0.6km tall everywhere on Earth,
// unlike the old fixed-degree grid which was square by construction) — see
// GeoView/changesets/geo.py's geohash_cell_size_degrees. Drawing a square
// here would misrepresent the true cell shape.
function drawGeoCells(layerGroup, cells, latSizeDegrees, lonSizeDegrees, metric) {
    layerGroup.clearLayers();
    const halfLat = latSizeDegrees / 2;
    const halfLon = lonSizeDegrees / 2;
    const colorFor = geoColorScale(cells, metric);
    cells.forEach(c => {
        L.rectangle([[c.lat - halfLat, c.lon - halfLon], [c.lat + halfLat, c.lon + halfLon]], {
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
    const coarseLatSize = initialGeo.lat_size_degrees;
    const coarseLonSize = initialGeo.lon_size_degrees;
    let fineCells = [];
    let fineLatSize = null;
    let fineLonSize = null;
    let metric = 'count';
    let showingFine = false;
    // The (padded) bounds fineCells actually covers, the zoom level it was
    // fetched at, and the in-flight request for it if any — see
    // scheduleFineFetch. The zoom level matters as much as the bounds:
    // cell size adapts to the viewport (geohash_precision_for_bbox in
    // geo.py), so a cached fetch only remains valid while zoomed in *no
    // further* than when it was made — otherwise "still geographically
    // contained" alone would keep reusing the same coarser cells forever
    // as the user zooms in deeper, since zooming in stays within the old
    // (larger, padded) area without ever leaving it.
    let fineFetchedBounds = null;
    let fineFetchedZoom = null;
    let fineFetchAbort = null;

    let legendDiv;
    const legend = L.control({ position: 'bottomright' });
    legend.onAdd = () => {
        legendDiv = L.DomUtil.create('div', 'bg-white rounded-md shadow-lg px-3 py-2 text-xs leading-relaxed');
        return legendDiv;
    };
    legend.addTo(map);

    function redraw() {
        const cells = showingFine ? fineCells : coarseCells;
        const latSize = showingFine ? fineLatSize : coarseLatSize;
        const lonSize = showingFine ? fineLonSize : coarseLonSize;
        drawGeoCells(showingFine ? fineLayer : coarseLayer, cells, latSize, lonSize, metric);
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

    // Naive "fetch the exact viewport on every moveend/zoomend, debounced
    // 400ms" was found (2026-09-19) to send a real DB query for every
    // single pan/zoom settle while browsing, so normal map exploration
    // alone put real, avoidable load on the DB. Two changes fix that without changing what's drawn:
    //
    // 1. Fetch a PADDED area (GEO_FINE_PREFETCH_PAD extra on every side),
    //    not just the exact visible viewport, and skip the fetch entirely
    //    whenever the current viewport is still inside the padded area the
    //    last fetch already covered (`fineFetchedBounds.contains(...)`).
    //    Panning around within already-explored territory then costs zero
    //    requests — only a pan/zoom that actually leaves covered ground
    //    triggers a real fetch, same principle as how tile layers only
    //    fetch tiles that scroll into view.
    // 2. Cancel a still-in-flight fetch (AbortController) when a newer one
    //    is needed, instead of letting both run — otherwise fast browsing
    //    (faster than a request round-trip) can pile up multiple concurrent
    //    DB queries whose results arrive out of order and get thrown away
    //    anyway once a newer one lands.
    let fineFetchTimer = null;
    function scheduleFineFetch() {
        clearTimeout(fineFetchTimer);
        fineFetchTimer = setTimeout(() => {
            const viewBounds = map.getBounds();
            const currentZoom = map.getZoom();
            // Reuse the cache only when not zoomed in past where it was
            // fetched — zooming in further always needs a real fetch, even
            // if the new (smaller) viewport is still geographically inside
            // the old padded area, since finer cells are owed at a deeper
            // zoom and "still contained" alone can't tell the difference.
            if (fineFetchedBounds && fineFetchedBounds.contains(viewBounds) && currentZoom <= fineFetchedZoom) return;

            const fetchBounds = viewBounds.pad(GEO_FINE_PREFETCH_PAD);
            const bbox = `${fetchBounds.getWest()},${fetchBounds.getSouth()},${fetchBounds.getEast()},${fetchBounds.getNorth()}`;

            if (fineFetchAbort) fineFetchAbort.abort();
            fineFetchAbort = new AbortController();

            fetchJson(apiUrl('/api/changesets/geo/', { resolution: 'fine', bbox }), { signal: fineFetchAbort.signal })
                .then(geo => {
                    fineCells = geo.cells;
                    fineLatSize = geo.lat_size_degrees;
                    fineLonSize = geo.lon_size_degrees;
                    fineFetchedBounds = fetchBounds;
                    fineFetchedZoom = currentZoom;
                    if (showingFine) redraw();
                })
                .catch(err => {
                    if (err.name !== 'AbortError') console.error('Failed to load fine-resolution geo data', err);
                });
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

// ── Filter suggestions ───────────────────────────────────────────────────────
// A small custom dropdown, not native <input list>/<datalist> — datalist's
// on-focus-show-all and option-ordering behavior both vary enough across
// browsers that "show 3 suggestions on an empty click" and "show top-20
// matches before /api/autocomplete/ results, in that order" aren't
// reliably controllable through it. This gives exact control over both.

const SUGGEST_ON_FOCUS_COUNT = 3;
const SUGGEST_MAX_RESULTS = 10; // matches AutocompleteView's own [:10] cap

// Each field's current top-~20 names (set by the loadWidget callbacks that
// already fetch each dimension's ranking chart, reusing that response — no
// extra request) — read live by wireSuggestions' handlers below, not
// captured at wire-time, since ranking data usually hasn't arrived yet
// when wireSuggestions itself runs at page load.
const topValueCache = {};
function cacheTopValues(field, names) {
    topValueCache[field] = names;
}

function renderSuggestions(dropdownId, inputId, values) {
    const dropdown = document.getElementById(dropdownId);
    if (!dropdown) return;
    if (!values.length) {
        dropdown.classList.add('hidden');
        dropdown.innerHTML = '';
        return;
    }
    dropdown.innerHTML = values.map(v =>
        `<button type="button" class="block w-full text-left px-3 py-1.5 text-sm hover:bg-gray-100" data-value="${v.replace(/"/g, '&quot;')}">${v}</button>`
    ).join('');
    dropdown.classList.remove('hidden');
    dropdown.querySelectorAll('button').forEach(btn => {
        // mousedown, not click: fires before the input's blur handler would
        // otherwise hide this dropdown first and swallow the click.
        btn.addEventListener('mousedown', e => {
            e.preventDefault();
            document.getElementById(inputId).value = btn.dataset.value;
            dropdown.classList.add('hidden');
        });
    });
}

// Empty input (on focus, or cleared while typing): just the top 3 — a
// small unobtrusive preview, not the full ranking. Non-empty input: top-20
// matches first (client-side substring filter over the cached ranking,
// instant, no request), then /api/autocomplete/ results appended for
// anything beyond the top 20, deduped, capped at SUGGEST_MAX_RESULTS —
// exactly the "suggest top 20 first, before autocomplete" ordering asked
// for, since a plain <datalist> can't guarantee that ordering reliably.
function wireSuggestions(inputId, dropdownId, field) {
    const input = document.getElementById(inputId);
    if (!input) return;

    function showTopSlice() {
        renderSuggestions(dropdownId, inputId, (topValueCache[field] || []).slice(0, SUGGEST_ON_FOCUS_COUNT));
    }

    input.addEventListener('focus', () => {
        if (!input.value) showTopSlice();
    });

    input.addEventListener('input', () => {
        const q = input.value;
        if (!q) {
            showTopSlice();
            return;
        }
        const qLower = q.toLowerCase();
        const topMatches = (topValueCache[field] || []).filter(v => v.toLowerCase().includes(qLower));
        fetch(`/api/autocomplete/?field=${field}&q=${encodeURIComponent(q)}`)
            .then(r => r.json())
            .then(serverValues => {
                if (input.value !== q) return; // a later keystroke already superseded this response
                const seen = new Set(topMatches.map(v => v.toLowerCase()));
                const merged = [...topMatches, ...serverValues.filter(v => !seen.has(v.toLowerCase()))].slice(0, SUGGEST_MAX_RESULTS);
                renderSuggestions(dropdownId, inputId, merged);
            })
            .catch(err => console.error(`Failed to load autocomplete for ${field}`, err));
    });

    input.addEventListener('blur', () => {
        document.getElementById(dropdownId).classList.add('hidden');
    });
    input.addEventListener('keydown', e => {
        if (e.key === 'Escape') document.getElementById(dropdownId).classList.add('hidden');
    });
}
