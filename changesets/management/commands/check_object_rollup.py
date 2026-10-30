"""Check object_daily_rollup against the changesets' own object counts.

For every stored day, the rollup's total objects must equal daily_rollup's
(the sum of the changesets' changes_count): the diffs add up to it on every
normal day. A day that doesn't was built from incomplete inputs (2026-10-18
held 127 objects instead of 4,004,637). Also names the day the hourly append
is waiting on, and why when its completeness gate (schema/0008) is holding it
back. Exits 1 when a stored day is off.

    python manage.py check_object_rollup

Repair a bad day by deleting its rows; the append then rebuilds it:

    DELETE FROM object_daily_rollup WHERE day = '2026-10-18'
"""
from django.core.management.base import BaseCommand, CommandError

from changesets.analytics.clickhouse.client import get_client

TOLERANCE = 0.001  # the gate's, schema/0008


class Command(BaseCommand):
    help = 'Compare object_daily_rollup day totals with the changesets\' changes_count; name the day the append waits on.'

    def handle(self, *args, **options):
        client = get_client()
        # daily_rollup is rebuilt once a day and stops at yesterday-ish: compare only days it surely covers.
        off = client.query(
            """SELECT r.day, r.objects, d.objects FROM
               (SELECT day, sum(objects) AS objects FROM object_daily_rollup WHERE dimension = 'volume' GROUP BY day) AS r
               INNER JOIN
               (SELECT day, sum(objects) AS objects FROM daily_rollup WHERE dimension = 'volume' GROUP BY day) AS d
               USING day
               WHERE r.day < today() - 2 AND abs(toInt64(r.objects) - toInt64(d.objects)) > d.objects * {tol:Float64}
               ORDER BY r.day""", parameters={'tol': TOLERANCE}).result_rows
        for day, rollup, expected in off:
            self.stdout.write(self.style.ERROR(f'{day}: rollup {rollup:,} objects, changesets say {expected:,}'))

        (first_day, stored_first, stored_last, stored_days), = client.query(
            """SELECT (SELECT toDate(min(edit_time)) + 1 FROM object_changes),
                      min(day), max(day), uniqExact(day)
               FROM object_daily_rollup WHERE dimension = 'volume'""").result_rows
        waiting = client.query(
            """SELECT min(d) FROM
               (SELECT arrayJoin(arrayMap(i -> {first:Date} + i, range(toUInt32(greatest(today() - 1, {first:Date}) - {first:Date})))) AS d)
               WHERE d NOT IN (SELECT DISTINCT day FROM object_daily_rollup WHERE dimension = 'volume')""",
            parameters={'first': first_day}).result_rows[0][0]
        self.stdout.write(f'{stored_days} days stored, {stored_first} to {stored_last}; coverage starts {first_day}')
        if waiting.year > 1970:
            (expected, got), = client.query(
                """SELECT (SELECT sum(changes_count) FROM changesets FINAL WHERE created_at >= toDateTime({d:Date}) AND created_at < toDateTime({d:Date} + 1)),
                          (SELECT sum(count) FROM object_change_features FINAL WHERE changeset_id IN
                             (SELECT changeset_id FROM changesets WHERE created_at >= toDateTime({d:Date}) AND created_at < toDateTime({d:Date} + 1)))""",
                parameters={'d': waiting}).result_rows
            ratio = got / expected if expected else 0
            state = 'ready' if expected and ratio >= 1 - TOLERANCE else 'HELD by the completeness gate'
            self.stdout.write(f'next to append: {waiting}: {got:,} of {expected:,} objects in the diffs ({ratio:.1%}), {state}')
        else:
            self.stdout.write('next to append: none (caught up)')
        if off:
            raise CommandError(f'{len(off)} stored day(s) disagree with the changesets')
