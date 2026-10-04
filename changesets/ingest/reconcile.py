"""Changesets the replication feed left open, re-fetched from the OSM API.

OSM's changeset replication feed sometimes never publishes a changeset's
closing update: found 2026-10-03, 61,111 changesets since September were
stored as open (about 1,600 a day), a few with a changes_count smaller than
the real one. OSM closes every changeset within 24 h, so one still open 25 h
after creation is stale. This fetches those from the API, 100 per request,
and writes them through import_changeset_batch like any replication record
(locate, every writer; the writers replace an open copy with a closed one).
See docs/decisions/stale-open-changesets.md.
"""
import logging
import time
import xml.etree.ElementTree as ET

import requests

from ..analytics.clickhouse.client import get_client
from ..osm_fetcher import import_changeset_batch

logger = logging.getLogger(__name__)

API_URL = 'https://api.openstreetmap.org/api/0.6/changesets'
BATCH = 100              # the API's limit for ?changesets=
PAUSE_SECONDS = 1.0      # between API requests
# The API answers 429/503 under load (seen 2026-10-04 after ~170 requests in
# a row): wait and retry rather than give up the rest of the run.
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_WAITS = (30, 60, 120)
USER_AGENT = 'osm-monitor (https://github.com/johanmorganti/osm-monitor)'


def stale_open_changesets(days, limit=None):
    """Ids of changesets created in the last `days` days, more than 25 h ago,
    still stored as open (ClickHouse, the primary store)."""
    rows = get_client().query(
        f"""SELECT changeset_id FROM changesets FINAL
            WHERE created_at >= now() - INTERVAL {{days:UInt32}} DAY
              AND created_at < now() - INTERVAL 25 HOUR
              AND (closed_at IS NULL OR open)
            ORDER BY changeset_id {f'LIMIT {int(limit)}' if limit else ''}""",
        parameters={'days': int(days)}).result_rows
    return [r[0] for r in rows]


def _get(session, chunk):
    for wait in (*RETRY_WAITS, None):
        response = session.get(API_URL, params={'changesets': ','.join(map(str, chunk))}, timeout=60)
        if response.status_code not in RETRY_STATUSES or wait is None:
            response.raise_for_status()
            return response
        logger.warning("OSM API busy, retrying", extra={
            'osm.reconcile.status': response.status_code, 'osm.reconcile.wait_seconds': wait})
        time.sleep(wait)


def reconcile(changeset_ids, session=None):
    """Re-fetch `changeset_ids` from the OSM API and write them. Returns
    (fetched, still_open)."""
    session = session or requests.Session()
    session.headers['User-Agent'] = USER_AGENT
    fetched = still_open = 0
    for start in range(0, len(changeset_ids), BATCH):
        chunk = changeset_ids[start:start + BATCH]
        response = _get(session, chunk)
        elements = ET.fromstring(response.content).findall('changeset')
        log_extra = {'osm.reconcile.first_id': chunk[0], 'osm.reconcile.requested': len(chunk)}
        if elements:
            import_changeset_batch(elements, log_extra)
        fetched += len(elements)
        still_open += sum(1 for e in elements if e.get('open') == 'true')
        if start + BATCH < len(changeset_ids):
            time.sleep(PAUSE_SECONDS)
    logger.info("Open changesets reconciled", extra={
        'osm.reconcile.requested': len(changeset_ids), 'osm.reconcile.fetched': fetched,
        'osm.reconcile.still_open': still_open,
    })
    return fetched, still_open
