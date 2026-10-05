"""Which continuous aggregate (or raw column) answers which question.

Routing rules of the TimescaleDB backend (see backend.py): unfiltered queries
and single-filter queries read a per-dimension CAgg, one filter crossed with
another dimension reads that pair's CAgg, everything else falls back to the
raw hypertable.
"""
from ...models import (
    CaggVolumeHourly, CaggVolumeDaily,
    CaggEditorDaily, CaggImageryDaily, CaggLocaleDaily, CaggContributorDaily, CaggCountryDaily,
    CaggEditorHourly, CaggImageryHourly, CaggLocaleHourly, CaggContributorHourly, CaggCountryHourly,
    CaggEditorImageryDaily, CaggEditorLocaleDaily, CaggImageryLocaleDaily,
    CaggContributorEditorDaily, CaggContributorImageryDaily, CaggContributorLocaleDaily,
    CaggContributorCountryDaily, CaggCountryEditorDaily, CaggCountryImageryDaily,
)

# Public dimension name -> raw Changeset column. See CLAUDE.md's
# dimension-naming table: 'language' stays a valid API dimension for external
# callers even though the dashboard no longer exposes it.
DIMENSION_FIELDS = {
    'contributor': 'user',
    'editor': 'created_by_family',
    'imagery': 'imagery_family',
    'language': 'locale_family',
    'country': 'country_code',
}

# Toplist-only dimensions, read from the raw table unless a dedicated CAgg
# covers them (editor_version: cagg_editor_version_daily, migration 0050, when
# editor is the only filter). created_by is unbounded across every editor
# ever seen, so the API requires a companion editor filter.
RAW_ONLY_DIMENSIONS = {
    'editor_version': 'created_by',
}

# One continuous aggregate per dimension (migrations 0020/0045), daily and
# hourly. Editor/contributor exclude NULL names; imagery/language/country
# bucket them under NONE_BUCKET.
CAGG_MODELS = {
    'contributor': CaggContributorDaily,
    'editor': CaggEditorDaily,
    'imagery': CaggImageryDaily,
    'language': CaggLocaleDaily,
    'country': CaggCountryDaily,
}
CAGG_MODELS_HOURLY = {
    'contributor': CaggContributorHourly,
    'editor': CaggEditorHourly,
    'imagery': CaggImageryHourly,
    'language': CaggLocaleHourly,
    'country': CaggCountryHourly,
}

# Ungrouped volume, by grain. CaggVolumeDaily (migration 0023) keeps wide
# ranges from silently truncating to the oldest days of hourly data.
CAGG_VOLUME_MODELS = {'hour': CaggVolumeHourly, 'day': CaggVolumeDaily}

# Nine cross-dimension CAggs: "filter by one dimension, broken out by
# another", the shape toplist(dimension, filter) and timeseries(group_by,
# filter) both need. Keyed by frozenset({dim_a, dim_b}) so a lookup works
# whichever side is the filter. Editor/imagery/language pairs first (migration
# 0041); contributor pairs (0043) once the contributor-grouped toplist was
# confirmed to be the remaining slow path (344K distinct values, so every
# contributor CAgg adds real refresh cost); country pairs (0048) when country
# replaced language on the dashboard, hence no country x language pair. Daily
# only. See docs/todo/timescale-deprecation.md.
PAIR_CAGGS = {
    frozenset({'editor', 'imagery'}): CaggEditorImageryDaily,
    frozenset({'editor', 'language'}): CaggEditorLocaleDaily,
    frozenset({'imagery', 'language'}): CaggImageryLocaleDaily,
    frozenset({'contributor', 'editor'}): CaggContributorEditorDaily,
    frozenset({'contributor', 'imagery'}): CaggContributorImageryDaily,
    frozenset({'contributor', 'language'}): CaggContributorLocaleDaily,
    frozenset({'contributor', 'country'}): CaggContributorCountryDaily,
    frozenset({'country', 'editor'}): CaggCountryEditorDaily,
    frozenset({'country', 'imagery'}): CaggCountryImageryDaily,
}

# Public dimension name -> the pair CAgg's column for it. Only 'language'
# differs ("locale", matching cagg_locale_daily's DB-side naming).
PAIR_CAGG_COLUMN = {
    'contributor': 'contributor', 'editor': 'editor', 'imagery': 'imagery',
    'language': 'locale', 'country': 'country',
}


def pair_cagg_lookup(dim_a, dim_b):
    """(model, column_for_dim_a, column_for_dim_b) if a pair CAgg covers these
    two (distinct) dimensions, else None."""
    if dim_a == dim_b:
        return None
    model = PAIR_CAGGS.get(frozenset({dim_a, dim_b}))
    if model is None:
        return None
    return model, PAIR_CAGG_COLUMN[dim_a], PAIR_CAGG_COLUMN[dim_b]
