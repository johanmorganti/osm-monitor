# Dashboard Specifications

All data on every dashboard reflects the selected timeframe (date range filter).

---

## Page 1 — Changeset Overview

### Part 1 · Changeset activity

| Widget | Details |
|---|---|
| **Total changesets** | Count of changesets in timeframe |
| **Changesets over time** | Time series (line chart) |
| **Editor breakdown** | Top 20 `created_by_family` by changeset count + stacked time series |
| **Imagery family breakdown** | Top 20 `imagery_family` by changeset count + stacked time series |
| **Language breakdown** | Top 20 `locale` by changeset count + stacked time series |

### Part 2 · Objects changed

Each changeset carries a `changes_count` (number of OSM objects affected).

| Widget | Details |
|---|---|
| **Total objects changed** | Sum of `changes_count` across all changesets in timeframe |
| **Avg objects per changeset** | Mean `changes_count` |
| **Top 20 contributors** | By sum of `changes_count` |
| **Top 20 editors** | By sum of `changes_count`, grouped by `created_by_family` |

---

## API filter params (`/api/changesets/`)

| Param | Filters on | Use case |
|---|---|---|
| `imagery_raw` | `imagery_used` array contains value | Detailed imagery dashboard |
| `imagery_family` | `imagery_family` exact match | Overview dashboard |

## Notes

- **locale_family**: normalised from `locale` by stripping region suffix (`fr-FR` → `fr`,
  `fr_FR` → `fr`). Stored in `locale_family` field, backfilled via `backfill_locale_family`.
- **Second dashboard** (detailed imagery): spec TBD.
