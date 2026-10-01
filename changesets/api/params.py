"""Request parsing and the API-level rules shared by every analytics backend."""
from datetime import datetime, timedelta

from django.utils import timezone

from ..analytics import Filters

TARGET_POINTS = 300
MAX_EXPLICIT_POINTS = 5000  # only constrains an explicit interval=hour override, see pick_interval


def resolve_filters(request):
    """start_date/end_date and the five dimension filters, shared by
    timeseries/summary/toplist/geo: same params, same "defaults to the last 7
    days" behavior everywhere."""
    start_date = request.query_params.get('start_date')
    end_date = request.query_params.get('end_date')
    if not start_date or not end_date:
        today = timezone.now().date()
        end_date = today.strftime('%Y-%m-%d')
        start_date = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    return Filters(
        start_date=start_date,
        end_date=end_date,
        contributor=request.query_params.get('contributor', ''),
        editor=request.query_params.get('editor', ''),
        imagery=request.query_params.get('imagery', ''),
        language=request.query_params.get('language', ''),
        country=request.query_params.get('country', ''),
    )


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
