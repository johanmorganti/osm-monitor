# Dashboard: new graph/section ideas

Discussed, not yet decided on a direction for the ones still open — revisit and pick one rather
than losing the list.

## Still open

- **Hashtag as a filter** — the hashtag toplist shipped; clicking a hashtag bar can't filter yet
  because `hashtag` isn't in `Filters`. Adding it means a `has(...)` condition in the ClickHouse
  `_Query`. Mining `comment` free-text for campaign mentions (~10% more coverage, noisier) stays a
  possible v2 — see `docs/hashtag-campaign-data.md`.
- **Objects page over multi-year ranges** — the size endpoints scan the raw table: measured over 1
  year, `distribution` ~0.7-1 s, `breakdown?by=editor` ~2.3 s, `largest?by=area` ~5-6 s (the sine
  per row; no longer on the page), the hashtag toplist ~2.5 s; `size_counts` over full history ~9 s. If multi-year ranges
  become common, a refreshable daily rollup keyed `(dimension, day, name, changes_count)` would
  answer the histogram and quartiles exactly (sizes have a small domain); measure first.
- **StreetComplete quest breakdown** — `streetcomplete_quest_type` is its own dedicated column,
  unused anywhere in the UI, despite StreetComplete being a large share of edit volume. Same
  ready-now shape as hashtags.
- **Discussion activity** — `comments_count` exists but is never surfaced; either a "most-discussed
  changesets" list or a discussion-volume-over-time line would show where contentious/active edits
  are happening.
- **New vs. returning contributor trend** — needs a "first ever seen per user" concept not
  currently tracked, so more of a schema addition than a pure UI change.
