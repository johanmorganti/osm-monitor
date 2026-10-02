"""Changeset-size rules shared by the distribution endpoints: histogram
buckets, percentile definition, experience bucket labels. API-level, so every
backend only reports exact counts and the shapes stay identical."""
import math

from ..analytics import EXPERIENCE_BOUNDS

# Lower bound of each histogram bucket (objects changed per changeset), the
# last one open-ended. Roughly logarithmic: sizes run from 0 (empty
# changesets) to OSM's 10,000-change cap, and most changesets are tiny.
SIZE_BUCKET_BOUNDS = (0, 1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)
PERCENTILES = (0.5, 0.9, 0.99)
WHALE_FRACTION = 0.01  # "the largest 1% of changesets"


def _range_label(low, high):
    if high is None:
        return f'{low:,}+'
    return f'{low:,}' if low == high else f'{low:,}–{high:,}'


def bucket_ranges(bounds):
    """[(low, high or None)] for consecutive lower bounds."""
    return [(low, bounds[i + 1] - 1 if i + 1 < len(bounds) else None) for i, low in enumerate(bounds)]


def experience_label(index):
    low, high = bucket_ranges(EXPERIENCE_BOUNDS)[index]
    return 'First changeset' if low == high == 1 else f'{_range_label(low, high)} changesets'


def quantile(sorted_counts, total, level):
    """Smallest size whose cumulative count reaches level × total (the same
    definition as ClickHouse's quantileExactWeighted, so the overall and
    per-group figures agree)."""
    threshold = level * total
    cumulative = 0
    for size, n in sorted_counts:
        cumulative += n
        if cumulative >= threshold:
            return size
    return None


def distribution(size_counts):
    """The /distribution/ payload from [(size, changesets)] sorted by size."""
    total = sum(n for _, n in size_counts)
    objects = sum(size * n for size, n in size_counts)

    buckets = []
    for low, high in bucket_ranges(SIZE_BUCKET_BOUNDS):
        rows = [(size, n) for size, n in size_counts if size >= low and (high is None or size <= high)]
        buckets.append({
            'label': _range_label(low, high), 'min': low, 'max': high,
            'changesets': sum(n for _, n in rows), 'objects': sum(size * n for size, n in rows),
        })

    # Objects share of the largest WHALE_FRACTION of changesets (whole
    # changesets, taken from the largest size down).
    remaining = math.ceil(WHALE_FRACTION * total)
    whale_objects = 0
    for size, n in reversed(size_counts):
        if remaining <= 0:
            break
        taken = min(n, remaining)
        whale_objects += size * taken
        remaining -= taken

    return {
        'total_changesets': total,
        'total_objects': objects,
        'avg_objects': round(objects / total, 1) if total else 0,
        'percentiles': {
            **{f'p{round(level * 100)}': quantile(size_counts, total, level) for level in PERCENTILES},
            'max': size_counts[-1][0] if size_counts else None,
        },
        'top_1pct_objects_share': round(whale_objects / objects, 4) if objects else None,
        'buckets': buckets,
    }
