"""Request parsing and the API-level rules shared by every analytics backend."""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone
from rest_framework.exceptions import APIException

from ..analytics import OBJECT_DIMENSIONS, Filters
from ..analytics.base import OBJECT_VALUES

TARGET_POINTS = 300
MAX_EXPLICIT_POINTS = 5000  # only constrains an explicit interval=hour override, see pick_interval


class BadParameter(APIException):
    """A request parameter the API rejects: answered 400 with
    {"error": message}, like the views' own checks
    (changesets/exceptions.py)."""
    status_code = 400


def resolve_filters(request, objects=False):
    """The range (start_date/end_date) and the five dimension filters, shared
    by timeseries/summary/toplist/geo: same params, same "defaults to the last
    7 days" behavior everywhere. Each bound is a day (YYYY-MM-DD; the end day
    included) or a timestamp (an exact instant; as the end, excluded), so a
    range can be any time window, e.g. one hourly point. `objects`: also the
    object filters (action, object_type, feature), for the endpoints that
    count objects from the diffs. Raises BadParameter on an invalid timestamp
    or object filter."""
    params = request.query_params
    start_date = params.get('start_date')
    end_date = params.get('end_date')
    start_time = end_time = None
    if not start_date or not end_date:
        today = timezone.now().date()
        end_date = today.strftime('%Y-%m-%d')
        start_date = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    elif len(start_date) > 10 or len(end_date) > 10:
        start_time = parse_bound(start_date, 'start_date', end=False)
        end_time = parse_bound(end_date, 'end_date', end=True)
        if end_time <= start_time:
            raise BadParameter('end_date must be after start_date')
        start_date = start_time.date().isoformat()
        end_date = (end_time - timedelta(microseconds=1)).date().isoformat()

    object_filters = {}
    if objects:
        for name in OBJECT_DIMENSIONS:
            value = params.get(name, '')
            if value and name in OBJECT_VALUES and value not in OBJECT_VALUES[name]:
                raise BadParameter(f'{name} must be one of: {", ".join(OBJECT_VALUES[name])}')
            object_filters[name] = value

    return Filters(
        start_date=start_date,
        end_date=end_date,
        contributor=params.get('contributor', ''),
        editor=params.get('editor', ''),
        imagery=params.get('imagery', ''),
        language=params.get('language', ''),
        country=params.get('country', ''),
        start_time=start_time,
        end_time=end_time,
        **object_filters,
    )


def parse_bound(raw, name, end):
    """One bound of a range, as a naive UTC datetime.
    A day (YYYY-MM-DD) is its midnight, or the next one for the end (the day
    is included); a timestamp is ISO 8601, with Z or an offset (converted to
    UTC) or without (taken as UTC)."""
    try:
        if len(raw) == 10:
            day = datetime.strptime(raw, '%Y-%m-%d')
            return day + timedelta(days=1) if end else day
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise BadParameter(f'{name} must be YYYY-MM-DD or an ISO 8601 timestamp, e.g. 2026-10-09T11:00:00Z')
    if value.tzinfo is not None:
        value = value.astimezone(dt_timezone.utc).replace(tzinfo=None)
    return value


def pick_interval(start_date, end_date, interval_param):
    """'hour' or 'day' for a timeseries. interval_param is the already
    validated interval= param (None, 'hour' or 'day').

    Auto-pick: hourly if the range's hour count fits within TARGET_POINTS
    (~300, i.e. ranges up to ~12.5 days), else daily. A weekly/monthly tier is
    the natural next step once multi-year ranges are common (see CLAUDE.md's
    "Design for full history").

    An explicit override is trusted, except interval=hour on a range wide
    enough to blow past MAX_EXPLICIT_POINTS: that raises ValueError (the view
    turns it into a 400) rather than silently truncating, which is the bug
    this function replaced."""
    range_days = (datetime.strptime(end_date, '%Y-%m-%d').date()
                  - datetime.strptime(start_date, '%Y-%m-%d').date()).days + 1
    hour_count = range_days * 24
    if interval_param == 'hour' and hour_count > MAX_EXPLICIT_POINTS:
        raise ValueError(
            f'interval=hour over this {range_days}-day range would return {hour_count} points '
            f'(max {MAX_EXPLICIT_POINTS}) — use interval=day or narrow the range'
        )
    if interval_param is not None:
        return interval_param
    return 'hour' if hour_count <= TARGET_POINTS else 'day'


def parse_bbox(raw):
    """(min_lon, min_lat, max_lon, max_lat) from "min_lon,min_lat,max_lon,max_lat";
    raises ValueError/TypeError on malformed input."""
    min_lon, min_lat, max_lon, max_lat = [float(v) for v in raw.split(',')]
    return min_lon, min_lat, max_lon, max_lat
