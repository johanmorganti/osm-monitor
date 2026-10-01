from ...models import FilterValue


def canonical_values(field, value):
    """The exact stored spelling(s) of a case-insensitive filter value, looked
    up in FilterValue (indexed on (field, UPPER(value)), migration 0056), so
    the raw hypertable can be filtered with plain equality/IN.
    `UPPER(col) = UPPER(%s)` can't use compressed chunks' per-batch bloom
    filters and forces every batch in range to be decompressed and scanned
    (~1s per monthly chunk, measured), while equality skips non-matching
    batches (~25ms); see CLAUDE.md's "Compression" section. Falls back to the
    input as given when FilterValue has no match (e.g. a value first seen in
    the last couple of minutes, before the poller's periodic FilterValue
    refresh): an exact-case match still works then."""
    matches = list(
        FilterValue.objects.filter(field=field, value__iexact=value).values_list('value', flat=True)
    )
    return matches or [value]
