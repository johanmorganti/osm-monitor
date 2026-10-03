# Widest changesets: find a list worth showing

Paused 2026-10-03. The Objects page's "Widest Changesets" table (20 changesets of the range with
the largest bounding box, `/api/changesets/largest/?by=area`) was removed from the page. The
endpoint stays, since it's public API.

## Why the table went

Ranked by area, the top of the list is always continent-sized boxes (20-70 million km² over a
week). Most are ordinary edits, like a StreetComplete user answering quests in two places on the
same day, or two shops edited far apart. Everything in it is "very wide", so the rank says
nothing. Real cases do show up (an SEO-spam edit and its revert were both in a week's list), but
they're buried.

Measured on the last 7 days: 963 changesets (0.3%) had a bounding box too wide to place on the map,
338 of them with 10 objects or fewer. By editor: iD 336, JOSM 292, osm-phone-report 82,
StreetComplete 78.

## What was tried, and the trade-offs

**A list of the changesets too wide for the map, under the Overview map, fewest objects first.**
"Too wide" is `BBOX_DIAG_THRESHOLD_KM` (200 km geodesic diagonal, `changesets/geo.py`), applied at
ingest by `changesets/ingest/locate.py`: such a changeset has a bounding box but no geohash. It's
built in the map's side panel (`/geo/cell/?cell=wide&order=fewest_objects`, a `wide` count in
`/geo/`), then reverted. Two problems:
- **Speed:** on the raw table (ordered by time), finding them reads the geohash of every row. Over
  full history the count takes ~21 s, the fewest-objects list ~32 s (over the 30 s cap), and ~13 s
  for its ids alone. One year takes 1.2-1.7 s, 7 days are instant. A per-changeset rollup of the
  ~1.1M too-wide changesets (63 MB, with the filter columns) made every range fast and matched the
  raw table exactly, but it is a second table to read to understand "too wide", which was judged
  too opaque.
- **A threshold list can be empty:** filtered on a contributor whose changesets are all reasonable,
  it shows nothing. The expectation is that a "widest" list always shows that scope's biggest ones.

**Next idea, not built: a ranking, not a threshold.** Under the map (or on Objects), a "Widest
changesets" list for the current filters ranked by area, with a second sort by **area per object**
(km² / changes_count). That surfaces the suspicious shape, a huge box with very few objects (a
node dragged across a continent), and is never empty while the scope has bounding boxes. Plain
"fewest objects" doesn't work without a threshold: it lists tiny 1-object edits. No extra table,
raw table only. Measured: 2.4 s by area and 2.8 s by area per object over a year, 27 s by area
over full history (near the cap), so a long unfiltered range would need either a rollup after all
or a "range too long" message.
