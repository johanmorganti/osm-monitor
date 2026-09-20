# Filter dropdown pre-population reported not working

A prior pass (2026-09-11) wired the contributor/editor/imagery datalists to pre-fill from each
ranking chart's already-fetched toplist response (`setDatalistOptions` in `dashboard.js`, called
from the `loadWidget` success callbacks) instead of staying empty until the user types. Verified
only via served-JS/HTML inspection at the time (no browser tool available) — user reports it's
not actually working in a real browser. Not yet root-caused; set aside until revisited.

Worth checking when picked back up: whether `<datalist>` options are actually reaching the DOM
(browser devtools/inspect, not just curl'd HTML/JS), and whether native datalist UI (no visible
dropdown arrow, appears only once the input is focused/clicked) is being mistaken for "empty" when
it's actually populated but just not visually obvious.
