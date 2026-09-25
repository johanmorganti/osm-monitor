import bz2
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

from changesets.cagg_maintenance import refresh_caggs_over_range
from changesets.osm_fetcher import import_changeset_batch
from changesets.rollups import refresh_filter_values_over_range

logger = logging.getLogger(__name__)


class _MultiStreamBz2Reader:
    """File-like adapter that transparently decompresses a sequence of
    independent, concatenated bz2 streams into one continuous byte stream.

    The OSM changesets planet dump is written as thousands of separate bz2
    streams back-to-back (rather than one continuous stream), which
    Python's low-level bz2.BZ2Decompressor does not follow past the first
    one on its own — it needs a fresh BZ2Decompressor() per stream, fed
    with any unused_data left over from the previous one."""

    def __init__(self, fileobj, read_size=4 * 1024 * 1024):
        self._fileobj = fileobj
        self._read_size = read_size
        self._decompressor = bz2.BZ2Decompressor()
        self._buffer = bytearray()
        self._exhausted = False

    def _fill(self, target_size):
        while len(self._buffer) < target_size and not self._exhausted:
            raw_chunk = self._fileobj.read(self._read_size)
            if not raw_chunk:
                self._exhausted = True
                break
            pending = raw_chunk
            while pending:
                self._buffer.extend(self._decompressor.decompress(pending))
                if self._decompressor.eof:
                    pending = self._decompressor.unused_data
                    if not pending:
                        break
                    self._decompressor = bz2.BZ2Decompressor()
                else:
                    pending = b''

    def read(self, size=-1):
        if size is None or size < 0:
            while not self._exhausted:
                self._fill(len(self._buffer) + self._read_size)
            result = bytes(self._buffer)
            self._buffer.clear()
            return result
        self._fill(size)
        result = bytes(self._buffer[:size])
        del self._buffer[:size]
        return result


# Every top-level element in the dump starts on its own line with exactly one
# space of indent; nested <tag>/<discussion> children are indented deeper, so
# this marker only ever matches a real top-level <changeset> boundary.
_CHANGESET_LINE_MARKER = b'\n <changeset '


def _find_marker(fileobj, offset, file_size, chunk_size=1024 * 1024):
    """Offset of the first _CHANGESET_LINE_MARKER at or after `offset`, or
    file_size if there is none."""
    pos = offset
    overlap = len(_CHANGESET_LINE_MARKER) - 1
    while pos < file_size:
        fileobj.seek(pos)
        chunk = fileobj.read(chunk_size + overlap)
        i = chunk.find(_CHANGESET_LINE_MARKER)
        if i != -1:
            return pos + i
        pos += chunk_size
    return file_size


class _ByteRangeReader:
    """File-like view of one slice of a decompressed dump, re-wrapped as a
    standalone <osm> document: every top-level <changeset> whose line starts
    inside [start, end) — snapped forward to real changeset boundaries at both
    ends, so adjacent ranges neither overlap nor drop an element. Lets several
    import_from_dump processes split one dump between them (--byte-range)."""

    def __init__(self, fileobj, start, end):
        fileobj.seek(0, 2)
        file_size = fileobj.tell()
        body_start = _find_marker(fileobj, start, file_size) + 1  # skip the '\n'
        body_end = _find_marker(fileobj, min(end, file_size), file_size)
        if body_end == file_size:
            # Last range: stop before the dump's own closing tag, re-added below.
            fileobj.seek(max(0, file_size - 4096))
            tail = fileobj.read()
            closing = tail.rfind(b'</osm>')
            if closing != -1:
                body_end = file_size - len(tail) + closing
        self._fileobj = fileobj
        self._pos = body_start
        self._end = max(body_start, body_end)
        self._pending = bytearray(b'<osm>\n')
        self._closed_root = False
        fileobj.seek(body_start)

    def read(self, size=-1):
        if size is None or size < 0:
            size = 1 << 62
        while len(self._pending) < size:
            if self._pos < self._end:
                n = min(size - len(self._pending), self._end - self._pos, 4 * 1024 * 1024)
                data = self._fileobj.read(n)
                if not data:
                    self._pos = self._end
                    continue
                self._pos += len(data)
                self._pending.extend(data)
            elif not self._closed_root:
                self._pending.extend(b'\n</osm>\n')
                self._closed_root = True
            else:
                break
        result = bytes(self._pending[:size])
        del self._pending[:size]
        return result


class Command(BaseCommand):
    help = (
        'Bulk-import changesets from the full OSM changesets planet dump '
        '(changesets-latest.osm.bz2 — the whole history since 2005, not the '
        'minutely replication diffs poll_sequences uses). Streams and parses '
        'the dump without loading it into memory, importing in batches via '
        'the same batched existence-check path as poll_sequences, so rows '
        'already present (e.g. from the 1-year backfill) are skipped cheaply. '
        'Automatically refreshes every CAgg (stats + geo) and FilterValue over '
        'the imported date range afterward, since this data lands far outside '
        'every CAgg policy\'s normal refresh window — see --skip-cagg-refresh.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'dump_path', type=str,
            help='Path to the dump file — either the downloaded .bz2 (decompressed on the fly) '
                 'or an already-decompressed plain .osm file (faster to resume from repeatedly, '
                 'since it skips paying the bz2 decompression cost again on every run)'
        )
        parser.add_argument(
            '--batch-size', type=int, default=500,
            help='Changesets per DB batch (default: 500)'
        )
        parser.add_argument(
            '--skip', type=int, default=0,
            help='Number of changesets to fast-forward past before importing (for resuming a previous run)'
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Stop after importing this many changesets (for a quick test run)'
        )
        parser.add_argument(
            '--progress-interval', type=int, default=30,
            help='Seconds between progress log lines (default: 30)'
        )
        parser.add_argument(
            '--byte-range', type=str, default=None, metavar='START:END',
            help='Only import changesets whose line starts in this byte range of a decompressed '
                 '.osm dump (snapped to changeset boundaries), so several processes can split one '
                 'dump between them. Combine with --skip-cagg-refresh and refresh once afterwards. '
                 'Not supported on .bz2 input.'
        )
        parser.add_argument(
            '--skip-cagg-refresh', action='store_true',
            help='Skip the CAgg + FilterValue refresh pass at the end (e.g. when running several '
                 'import passes back to back and only wanting to refresh once, after the last one).'
        )

    def handle(self, *args, **options):
        # The app role defaults to a bounded statement_timeout (see
        # db/init/02-role-statement-timeout.sh) — a full-history dump import
        # legitimately runs long batches, so opt out.
        connection.cursor().execute("SET statement_timeout = 0")
        # Keep this session's statements out of pg_stat_statements: each
        # batch's INSERT/existence check carries thousands of parameters, so a
        # full-history import grew its query-text file past 1GB — which every
        # pg_stat_statements reader (e.g. Datadog DBM, every 60s) then re-reads
        # while holding the extension's lock. Superuser-only setting; skipped
        # if the role can't set it.
        try:
            with transaction.atomic():
                connection.cursor().execute("SET pg_stat_statements.track = 'none'")
        except DatabaseError:
            logger.warning("Could not disable pg_stat_statements tracking for this import")

        dump_path = options['dump_path']
        batch_size = options['batch_size']
        skip = options['skip']
        limit = options['limit']
        progress_interval = options['progress_interval']

        logger.info(
            "Starting full-dump import",
            extra={'osm.dump_path': dump_path, 'osm.batch_size': batch_size, 'osm.skip': skip, 'osm.limit': limit, 'osm.dump.byte_range': options['byte_range']},
        )

        total_seen = 0
        total_created = 0
        total_skipped = 0
        total_updated = 0
        batch = []
        start_time = time.monotonic()
        last_log_time = start_time
        seen_at_last_log = 0
        last_created_at = None
        # Actual min/max created_at across every changeset imported this
        # run (not just the batch currently being processed) — the range a
        # bulk import needs its CAggs explicitly refreshed over at the end,
        # since it lands far outside every CAgg policy's 7-day start_offset
        # window (see CLAUDE.md's "Old-dated rows..." section). Parsed
        # once per element here rather than reusing import_changeset_batch's
        # own per-batch parse, to avoid changing that function's return
        # shape just for this.
        min_created_at = None
        max_created_at = None

        with open(dump_path, 'rb') as raw_file:
            if options['byte_range']:
                if dump_path.endswith('.bz2'):
                    raise CommandError('--byte-range needs a decompressed .osm dump, not .bz2')
                range_start, range_end = (int(x) for x in options['byte_range'].split(':'))
                reader = _ByteRangeReader(raw_file, range_start, range_end)
            elif dump_path.endswith('.bz2'):
                reader = _MultiStreamBz2Reader(raw_file)
            else:
                reader = raw_file
            context = ET.iterparse(reader, events=('start', 'end'))
            _, root = next(context)  # first "start" event is the <osm> root

            for event, elem in context:
                if event != 'end' or elem.tag != 'changeset':
                    continue

                total_seen += 1

                if total_seen <= skip:
                    if total_seen % batch_size == 0:
                        root.clear()
                        now = time.monotonic()
                        if now - last_log_time >= progress_interval:
                            logger.info(
                                "Full-dump import: still skipping",
                                extra={
                                    'osm.dump.skip_target': skip,
                                    'osm.dump.seen': total_seen,
                                    'osm.dump.last_created_at': elem.attrib.get('created_at'),
                                    'osm.dump.elapsed_seconds': round(now - start_time, 1),
                                },
                            )
                            last_log_time = now
                    continue

                last_created_at = elem.attrib.get('created_at', last_created_at)
                elem_created_at = datetime.strptime(last_created_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt_timezone.utc)
                if min_created_at is None or elem_created_at < min_created_at:
                    min_created_at = elem_created_at
                if max_created_at is None or elem_created_at > max_created_at:
                    max_created_at = elem_created_at
                batch.append(elem)

                if len(batch) >= batch_size:
                    created, skipped, updated = import_changeset_batch(
                        batch, {'osm.import_source': 'full_dump'}
                    )
                    total_created += created
                    total_skipped += skipped
                    total_updated += updated
                    for e in batch:
                        e.clear()
                    root.clear()
                    batch = []

                    now = time.monotonic()
                    if now - last_log_time >= progress_interval:
                        elapsed = now - start_time
                        interval_elapsed = now - last_log_time
                        logger.info(
                            "Full-dump import progress",
                            extra={
                                'osm.dump.seen': total_seen,
                                'osm.dump.created': total_created,
                                'osm.dump.skipped': total_skipped,
                                'osm.dump.updated': total_updated,
                                'osm.dump.last_created_at': last_created_at,
                                'osm.dump.rate_per_sec': round((total_seen - seen_at_last_log) / interval_elapsed, 1),
                                'osm.dump.elapsed_seconds': round(elapsed, 1),
                            },
                        )
                        last_log_time = now
                        seen_at_last_log = total_seen

                if limit is not None and total_seen - skip >= limit:
                    break

            if batch:
                created, skipped, updated = import_changeset_batch(
                    batch, {'osm.import_source': 'full_dump'}
                )
                total_created += created
                total_skipped += skipped
                total_updated += updated

        elapsed = time.monotonic() - start_time
        logger.info(
            "Full-dump import finished",
            extra={
                'osm.dump.seen': total_seen,
                'osm.dump.created': total_created,
                'osm.dump.skipped': total_skipped,
                'osm.dump.updated': total_updated,
                'osm.dump.elapsed_seconds': round(elapsed, 1),
            },
        )

        # Every CAgg (stats + geo), not just the "stats" ones, plus
        # FilterValue (autocomplete) — this run's rows land far outside
        # every CAgg policy's 7-day start_offset window (see CLAUDE.md's
        # "Old-dated rows..." section) and, unlike poll_sequences.py, this
        # command never otherwise touches FilterValue at all (it has no
        # equivalent of the poller's own periodic refresh_filter_values_
        # incremental() call). `import_changeset_batch`'s existence check
        # means a *skipped* (already-present) row's aggregates were already
        # correct from whenever it was first imported — so none of this
        # needs to run when nothing was actually created or updated.
        if options['skip_cagg_refresh']:
            self.stdout.write('Skipping CAgg/FilterValue refresh (--skip-cagg-refresh).')
        elif (total_created or total_updated) and min_created_at is not None:
            refresh_end = max_created_at + timedelta(days=1)
            self.stdout.write(f'Refreshing all CAggs over {min_created_at.date()}..{refresh_end.date()}...')
            logger.info(
                "Full-dump import: starting CAgg refresh",
                extra={'osm.cagg_refresh.range_start': str(min_created_at.date()), 'osm.cagg_refresh.range_end': str(refresh_end.date())},
            )
            refresh_caggs_over_range(min_created_at, refresh_end, stdout=self.stdout)
            self.stdout.write(self.style.SUCCESS('CAgg refresh complete.'))

            # Range-scoped, not refresh_filter_values_incremental()'s
            # watermarked version: that watermark tracks the live poller's
            # forward progress, and this command's whole purpose is
            # historical (often *older*) data — `created_at >= watermark`
            # would silently skip it. See rollups.py's
            # refresh_filter_values_over_range() docstring.
            self.stdout.write('Refreshing FilterValue over the same range...')
            refresh_filter_values_over_range(min_created_at, refresh_end)
            self.stdout.write(self.style.SUCCESS('FilterValue refresh complete.'))
        else:
            self.stdout.write('Nothing created or updated — skipping CAgg/FilterValue refresh.')
