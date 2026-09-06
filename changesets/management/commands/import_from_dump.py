import bz2
import logging
import time
import xml.etree.ElementTree as ET

from django.core.management.base import BaseCommand

from changesets.osm_fetcher import import_changeset_batch

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


class Command(BaseCommand):
    help = (
        'Bulk-import changesets from the full OSM changesets planet dump '
        '(changesets-latest.osm.bz2 — the whole history since 2005, not the '
        'minutely replication diffs poll_sequences uses). Streams and parses '
        'the dump without loading it into memory, importing in batches via '
        'the same batched existence-check path as poll_sequences, so rows '
        'already present (e.g. from the 1-year backfill) are skipped cheaply.'
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

    def handle(self, *args, **options):
        dump_path = options['dump_path']
        batch_size = options['batch_size']
        skip = options['skip']
        limit = options['limit']
        progress_interval = options['progress_interval']

        logger.info(
            "Starting full-dump import",
            extra={'osm.dump_path': dump_path, 'osm.batch_size': batch_size, 'osm.skip': skip, 'osm.limit': limit},
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

        with open(dump_path, 'rb') as raw_file:
            reader = _MultiStreamBz2Reader(raw_file) if dump_path.endswith('.bz2') else raw_file
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
