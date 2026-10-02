# Dimension naming: DB column vs. API param vs. UI label

Three separate names can exist for the same dimension, and they're allowed to differ — but each
layer's name must be used *consistently everywhere at that layer*, not decided ad hoc per file.
This came up concretely: the dashboard's chart title has said "Top 20 Languages" for a while, but
when the matching filter was added it was labeled "Locale" in the filter form, "locale" in the
click-to-filter hint text, and `locale` as the API query param — three different UI-facing spots
disagreeing with the one that had already shipped.

The convention, and the current mapping for every dimension:

| DB column (`Changeset` field) | API param (`DIMENSIONS` / `DIMENSION_FIELDS` key) | UI label |
|---|---|---|
| `user` | `contributor` | Contributor |
| `created_by_family` | `editor` | Editor |
| `imagery_family` | `imagery` | Imagery provider |
| `locale_family` | `language` | *(none — see below)* |
| `country_code` | `country` | Country |

`language` is the one exception to "every dimension has a UI label": it was the dashboard's 5th
dimension until 2026-09-22, when `country` replaced it there (filter, toplist chart, "over time"
chart). `language` itself was deliberately left alone at the API layer — `DIMENSION_FIELDS`, its
CAggs, `/api/docs/` — since it's a real, already-public param and removing it would be a breaking
API change per the API param rule below, not something to do as a side effect of a dashboard
change. So `language` now has an API param and no UI label at all; don't take that as license to
leave a *new* dimension's UI label out, that's specific to this one already-public, deliberately
retained case.

Rules:
- The **DB column** is internal and never exposed directly — it can stay whatever legacy/technical
  name it already has (`user`, `locale_family`, ...). Renaming it is a real migration, not a
  find-and-replace, so don't do it just to chase a display-name preference.
- The **API param** (`DIMENSION_FIELDS` key, query string name, `CAGG_MODELS` key) is the one
  stable public identifier — it's what `/api/docs/` documents and what external callers would use.
  Pick the word a human would naturally use for the concept (`contributor`, not `user`; `language`,
  not `locale`), and once it's public, treat renaming it as a breaking API change, not a quick fix.
- The **UI label** (form `<label>`, placeholder, click-to-filter hint text, chart title) should
  read naturally and may add words the API param doesn't need (e.g. "Imagery provider" for
  `imagery`), but every UI-facing string for the same dimension must agree — if the chart title
  says "Language," the filter label, placeholder, and hint text must too. When adding or changing
  a filter, grep the template and `dashboard.js` for every existing string tied to that dimension
  before picking new wording, rather than inventing a label in just the one spot being touched.
