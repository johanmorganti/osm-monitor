import random
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand

from changesets.analytics.clickhouse.client import get_client
from changesets.ingest.locate import locate

_FIELDS = ('changeset_id', 'created_at', 'min_lat', 'min_lon', 'max_lat', 'max_lon', 'geohash', 'country_code')


class Command(BaseCommand):
    help = (
        'Recompute geohash/country_code with the ingest locator (changesets/ingest/locate.py) and '
        'compare with (--check) or fix (default) the values stored in ClickHouse. --check samples '
        'random days across the range; the default mode walks every day of the range and only '
        'rewrites rows whose values differ (e.g. after changing the country boundaries). A fix '
        're-inserts the whole row: the ReplacingMergeTree keeps the last row inserted when '
        'changes_count ties. The map rollups pick it up at their next daily refresh.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--start', type=date.fromisoformat, default=date(2005, 4, 1))
        parser.add_argument('--end', type=date.fromisoformat, default=None, help='Inclusive (default: today).')
        parser.add_argument('--check', action='store_true', help='Compare only, write nothing.')
        parser.add_argument('--sample-days', type=int, default=200, help='--check: random days to sample.')
        parser.add_argument('--per-day', type=int, default=5000, help='--check: max rows per sampled day.')
        parser.add_argument('--seed', type=int, default=1)
        parser.add_argument('--show', type=int, default=15, help='--check: example differences to print.')

    def handle(self, *args, **options):
        start, end = options['start'], options['end'] or date.today()
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        if options['check']:
            rng = random.Random(options['seed'])
            days = sorted(rng.sample(days, min(options['sample_days'], len(days))))
            self._run(days, options['per_day'], write=False, show=options['show'])
        else:
            self._run(days, None, write=True, show=0)

    def _run(self, days, per_day, write, show):
        client = get_client()
        totals, examples = Counter(), []
        t0 = time.monotonic()
        for day in days:
            lo = datetime.combine(day, datetime.min.time(), tzinfo=dt_timezone.utc)
            result = client.query(
                f"""SELECT {', '.join(_FIELDS)} FROM changesets FINAL
                    WHERE created_at >= {{lo:DateTime}} AND created_at < {{hi:DateTime}}
                    ORDER BY changeset_id {f'LIMIT {int(per_day)}' if per_day else ''}""",
                parameters={'lo': lo, 'hi': lo + timedelta(days=1)})
            rows = [dict(zip(_FIELDS, r)) for r in result.result_rows]
            if not rows:
                continue
            stored = [(r['geohash'], r['country_code']) for r in rows]
            locate(rows)  # overwrites geohash/country_code in place
            changed = []
            for r, (old_gh, old_cc) in zip(rows, stored):
                totals['rows'] += 1
                gh_diff, cc_diff = r['geohash'] != old_gh, r['country_code'] != old_cc
                totals['geohash_diff'] += gh_diff
                totals['country_diff'] += cc_diff
                if gh_diff or cc_diff:
                    changed.append(r)
                    kind = ('geohash' if gh_diff else '') + ('+' if gh_diff and cc_diff else '') + ('country' if cc_diff else '')
                    totals[f'kind:{kind}'] += 1
                    if len(examples) < show:
                        examples.append((r, old_gh, old_cc))
            if write and changed:
                self._rewrite(client, lo, changed)
                totals['written'] += len(changed)

        rows = totals['rows'] or 1
        self.stdout.write(
            f"{totals['rows']} rows over {len(days)} days in {time.monotonic() - t0:.0f}s: "
            f"geohash differs on {totals['geohash_diff']} ({100 * totals['geohash_diff'] / rows:.4f}%), "
            f"country differs on {totals['country_diff']} ({100 * totals['country_diff'] / rows:.4f}%)"
            + (f", {totals['written']} rows written" if write else '')
        )
        for key in sorted(k for k in totals if k.startswith('kind:')):
            self.stdout.write(f'  {key[5:]}: {totals[key]}')
        for r, old_gh, old_cc in examples:
            self.stdout.write(
                f"  {r['changeset_id']} {r['created_at']:%Y-%m-%d} bbox=({r['min_lat']},{r['min_lon']})-({r['max_lat']},{r['max_lon']}) "
                f"stored={old_gh}/{old_cc} computed={r['geohash']}/{r['country_code']}"
            )

    @staticmethod
    def _rewrite(client, lo, changed):
        """Re-insert the changed rows whole, with the recomputed location."""
        fixes = {r['changeset_id']: (r['geohash'], r['country_code']) for r in changed}
        current = client.query(
            """SELECT * FROM changesets FINAL
               WHERE created_at >= {lo:DateTime} AND created_at < {hi:DateTime} AND changeset_id IN {ids:Array(UInt64)}""",
            parameters={'lo': lo, 'hi': lo + timedelta(days=1), 'ids': list(fixes)})
        columns = list(current.column_names)
        gh, cc, cid = columns.index('geohash'), columns.index('country_code'), columns.index('changeset_id')
        rows = []
        for row in current.result_rows:
            row = list(row)
            row[gh], row[cc] = fixes[row[cid]]
            rows.append(row)
        client.insert('changesets', rows, column_names=columns)
