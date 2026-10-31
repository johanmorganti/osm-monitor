import bz2
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand

from changesets.ingest.writers import get_writers
from changesets.osm_fetcher import import_changeset_batch

logger = logging.getLogger(__name__)


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


# Start of a bz2 stream: the "BZh" + block-size header, then the first block's
# magic. The dump's streams are byte-aligned and back-to-back (~100 KB each).
_BZ2_STREAM_START = re.compile(rb'BZh[1-9]1AY&SY')


def _find_bz2_stream(fileobj, offset, file_size, chunk_size=1024 * 1024):
    """Offset of the first bz2 stream start at or after `offset`, or
    file_size if there is none."""
    pos = offset
    overlap = 9  # len(magic) - 1
    while pos < file_size:
        fileobj.seek(pos)
        match = _BZ2_STREAM_START.search(fileobj.read(chunk_size + overlap))
        if match:
            return pos + match.start()
        pos += chunk_size
    return file_size


def _decompress_streams(fileobj, start, end, read_size=4 * 1024 * 1024):
    """Decompressed bytes of the bz2 streams in [start, end) of fileobj, both
    stream boundaries, as a sequence of chunks."""
    fileobj.seek(start)
    pos = start
    decompressor = bz2.BZ2Decompressor()
    while pos < end:
        raw_chunk = fileobj.read(min(read_size, end - pos))
        if not raw_chunk:
            break
        pos += len(raw_chunk)
        pending = raw_chunk
        while pending:
            data = decompressor.decompress(pending)
            if data:
                yield data
            if decompressor.eof:
                pending = decompressor.unused_data
                decompressor = bz2.BZ2Decompressor()
            else:
                pending = b''


class _Bz2ByteRangeReader:
    """Like _ByteRangeReader, but over a slice of the compressed .bz2 dump, so
    several processes can split it without decompressing it to disk.

    The range is snapped forward to bz2 stream starts at both ends, and only
    those streams are decompressed ("own" output), plus whatever follows them
    until the next changeset boundary. A changeset belongs to the range whose
    own output holds the '\\n' of its _CHANGESET_LINE_MARKER: the same rule as
    _ByteRangeReader's, so adjacent ranges neither overlap nor drop one, even
    when a changeset (or its marker) spans two streams."""

    def __init__(self, fileobj, start, end):
        fileobj.seek(0, 2)
        file_size = fileobj.tell()
        self._fileobj = fileobj
        self._file_size = file_size
        self._own_start = _find_bz2_stream(fileobj, start, file_size)
        self._own_end = max(self._own_start, _find_bz2_stream(fileobj, min(end, file_size), file_size))
        self._body = self._body_chunks()
        self._pending = bytearray(b'<osm>\n')
        self._closed_root = False

    def _chunks(self):
        """(decompressed chunk, is own output) pairs; the empty chunk marks
        the end of the own output."""
        for data in _decompress_streams(self._fileobj, self._own_start, self._own_end):
            yield data, True
        yield b'', False
        for data in _decompress_streams(self._fileobj, self._own_end, self._file_size):
            yield data, False

    def _body_chunks(self):
        if self._own_start >= self._own_end:
            return
        keep = len(_CHANGESET_LINE_MARKER) - 1  # a marker may straddle chunks
        buf = bytearray()
        buf_offset = 0  # decompressed offset of buf[0]
        total = 0
        own_size = None  # decompressed size of the own output, once known
        started = False
        for data, own in self._chunks():
            if not own and own_size is None:
                own_size = total
            buf.extend(data)
            total += len(data)
            if not started:
                i = buf.find(_CHANGESET_LINE_MARKER)
                if i != -1 and own_size is not None and buf_offset + i >= own_size:
                    return  # no changeset starts in this range
                if i == -1:
                    if own_size is not None and total - keep >= own_size:
                        return
                    drop = max(0, len(buf) - keep)
                    del buf[:drop]
                    buf_offset += drop
                    continue
                del buf[:i + 1]  # skip the '\n': the body starts at ' <changeset'
                buf_offset += i + 1
                started = True
            if own_size is not None:
                j = buf.find(_CHANGESET_LINE_MARKER, max(0, own_size - buf_offset))
                if j != -1:
                    yield bytes(buf[:j])
                    return
            emit = max(0, len(buf) - keep)
            if emit:
                yield bytes(buf[:emit])
                del buf[:emit]
                buf_offset += emit
        if started:
            # Last range: stop before the dump's own closing tag, re-added in read().
            closing = buf.rfind(b'</osm>')
            yield bytes(buf[:closing] if closing != -1 else buf)

    def read(self, size=-1):
        if size is None or size < 0:
            size = 1 << 62
        while len(self._pending) < size:
            data = next(self._body, None)
            if data is not None:
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
        'the dump without loading it into memory, writing in batches through '
        'the same writers as poll_sequences, so rows already present (e.g. '
        'from the backfill) collapse into one. ClickHouse\'s rollups pick the '
        'imported range up at their next daily refresh.'
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
            help='Only import changesets whose line starts in this byte range of the dump '
                 '(snapped to changeset boundaries), so several processes can split one dump '
                 'between them. On .bz2 input the range is of the compressed file (snapped to its '
                 'bz2 streams), so the dump needs no decompressing to disk.'
        )
        parser.add_argument(
            '--skip-cagg-refresh', action='store_true',
            help="Skip each writer's post-import maintenance at the end (none for ClickHouse, whose "
                 'rollups rebuild from the deduplicated table daily).'
        )

    def handle(self, *args, **options):
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
        # run (not just the batch currently being processed) — the range
        # handed to each writer's after_backfill at the end. Parsed
        # once per element here rather than reusing import_changeset_batch's
        # own per-batch parse, to avoid changing that function's return
        # shape just for this.
        min_created_at = None
        max_created_at = None

        with open(dump_path, 'rb') as raw_file:
            if options['byte_range']:
                range_start, range_end = (int(x) for x in options['byte_range'].split(':'))
                if dump_path.endswith('.bz2'):
                    reader = _Bz2ByteRangeReader(raw_file, range_start, range_end)
                else:
                    reader = _ByteRangeReader(raw_file, range_start, range_end)
            elif dump_path.endswith('.bz2'):
                reader = bz2.open(raw_file)  # reads the dump's concatenated streams as one
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

        # Bulk writes land far outside the live window, so each writer gets its
        # post-backfill maintenance over the imported range (a no-op for
        # ClickHouse, see ClickHouseWriter.after_backfill).
        # Skipped (already present) rows were already accounted for when first
        # imported, so nothing to do when nothing was created or updated.
        if options['skip_cagg_refresh']:
            self.stdout.write('Skipping post-import maintenance (--skip-cagg-refresh).')
        elif (total_created or total_updated) and min_created_at is not None:
            refresh_end = max_created_at + timedelta(days=1)
            logger.info(
                "Full-dump import: starting post-import maintenance",
                extra={'osm.cagg_refresh.range_start': str(min_created_at.date()), 'osm.cagg_refresh.range_end': str(refresh_end.date())},
            )
            for writer in get_writers():
                writer.after_backfill(min_created_at, refresh_end, stdout=self.stdout)
            self.stdout.write(self.style.SUCCESS('Post-import maintenance complete.'))
        else:
            self.stdout.write('Nothing created or updated — skipping post-import maintenance.')
