# Hashtag / campaign data: what's actually there

Research note for the "Hashtags/campaign toplist" idea in `TODO.md`'s "Dashboard: new
graph/section ideas" — is the dedicated `hashtags` column enough to build a "Top campaigns"
toplist, or does real signal live elsewhere too? All numbers below are from `TABLESAMPLE SYSTEM`
queries against the live `changesets_changeset` table (~17M rows) — small percentages, but
Postgres's block-level sampling is representative enough for a coverage/shape estimate, not exact
counts.

## What's in the dedicated `hashtags` column today

`changesets/osm_fetcher.py`'s `_parse_changeset_element` (~line 227) only populates it from OSM's
own `hashtags` tag, which a client sets deliberately (`tag_value.split(';')`, stripped of `#`).
Stored as `jsonb` (a JSON array of strings — see `changesets/models.py:86` and
`\d changesets_changeset`), not a native Postgres array.

**Coverage: ~18.8%** of changesets have a non-empty `hashtags` array (27,682 / 147,011 sampled).
That's a substantial, real slice of the table — not a rounding error.

**Top values** (sampled, `jsonb_array_elements_text(hashtags)` grouped): `maproulette` (6651),
`OzonGeo` (6329), `tt_event`/`tomtom`/`TomTomCares` (TomTom's mapping program, ~4300 combined),
`fixmapafrica90`, `grabosm`, `mapathones_vulnerables`, `BANO`, `Pifometre`, `Kaart`,
`missingmaps`/`msf` (Médecins Sans Frontières' Missing Maps program), `OSMBirthday2025EAP`,
`2025_MH_MX_MapatonNacional`, `help-mm-earthquake`, `uniquemappersnetwork`. This is genuine,
diverse campaign/organization signal — humanitarian orgs, corporate mapping programs, regional
mapathons, editor-tool-driven bulk edits (MapRoulette).

## Does the raw `tags` JSONField hide more?

`changesets/osm_fetcher.py`'s dedicated-column keys (the full `elif tag_key ==` chain, confirmed
by reading the source, not assumed): `created_by`, `comment`, `locale`, `source`, `imagery_used`,
`host`, `changesets_count`, `hashtags`, `StreetComplete:quest_type`, `review_requested`. Every
other tag key lands in `remaining_tags` (also `jsonb`).

Sampled the full key space of `tags` directly (`jsonb_object_keys`, ~40 most common keys beyond
the 10 dedicated ones). **No hidden campaign signal here** — the long tail is dominated by editor/
QA-tool internals: iD's walkthrough-progress state (`ideditor:walkthrough_*`), JOSM validator
output (`resolved:*`/`warnings:*` — crossing ways, outdated tags, disconnected ways), and
MapRoulette's own per-task metadata (`maproulette:server`, `maproulette:tasks`, ~313 each — far
below the 6651 changesets that already carry `maproulette` via the `hashtags` column itself). None
of these are alternate campaign identifiers; they're editor bookkeeping. **`remaining_tags` is not
worth mining for this feature.**

## Does free-text `comment` hide more?

Yes, real signal exists here that the dedicated `hashtags` tag misses. Sampled `comment` for `#`
occurrences where `hashtags` is empty/null: **~2%** of all changesets (2,888 / 147,305 sampled) —
roughly a 10% relative increase over the dedicated column's own 18.8% coverage.

Naive `#\w+` extraction is noisy — a large fraction of matches are numeric (`#12345678`), which
are OSM note/issue references embedded in commit-style comments ("closed via #12345"), not
campaign tags. Filtering to tags starting with a letter (`#[A-Za-z][A-Za-z0-9_]*`) cleans this up
substantially. Top matches after filtering: `maproulette` (1998 — inconsistently tagged: many
MapRoulette-driven edits mention it in `comment` but never set the `hashtags` tag), `MapComplete`
(358), then a long tail of MapComplete *theme* names (`climbing`, `food`, `benches`,
`playgrounds`, `drinking_water`, `bookcases`, `cyclofix`, `etymology`) plus genuine one-off
campaigns (`aed` — AED Map's defibrillator-mapping app, `SotMLatam2025` — a State of the Map
Latam conference mapathon). Worth flagging: the MapComplete theme names are closer to "survey
theme" than "campaign" in spirit — a toplist mixing them with `missingmaps`/`TomTomCares` would
conflate two different concepts unless presented carefully (or theme names are excluded/handled
separately).

## Recommendation

**Ship the toplist on the dedicated `hashtags` column alone first.** 18.8% coverage with clean,
diverse, genuinely campaign-shaped values is enough for a useful "Top campaigns" ranking without
touching ingestion or running a backfill. This is the fast, low-risk path.

**Real design wrinkle, independent of the data-source question**: `hashtags` is a
one-to-many field (one changeset can carry multiple tags), unlike every existing `DIMENSION_FIELDS`
entry (editor/imagery/locale/contributor), which are all single-valued columns. The existing
`ToplistView`/rollup-shaped pattern (`GROUP BY <column>`) doesn't directly apply — needs an
`unnest`/`jsonb_array_elements_text`-based aggregation instead, both in the raw-path query and
(later) in a `cagg_hashtag_daily`-style continuous aggregate if this ever joins the continuous-
aggregates work in `TODO.md`. Scope this explicitly rather than assuming it's a drop-in 5th
dimension.

**Comment-text mining is a legitimate second phase, not required for a v1**: recovers ~10%
relative additional coverage, but needs (a) the numeric-reference filter above, (b) a decision on
whether to fold MapComplete theme names into the same "campaign" list or keep them separate, (c) a
one-time backfill for existing rows (regex-extract into a new field or append into `hashtags`
itself — appending risks conflating "OSM's own hashtags tag" with "text Claude inferred from
comment," worth keeping distinguishable) plus an ongoing `osm_fetcher.py` ingest-time change so
new changesets keep getting it. Rough scope: a backfill management command (same shape as
`backfill_imagery_family.py`) plus a small `_parse_changeset_element` addition — a few hours of
focused work, not a big lift, but a distinct follow-up from the v1 toplist itself.
