"""The contract between ingestion (poller, dump importer) and a storage backend.

Ingestion parses changesets once (osm_fetcher + locate) and hands the same
records to every configured writer (settings.INGEST_BACKENDS). Each writer
stores them its own way, with these semantics:

- Upsert keyed by changeset_id: a changeset not stored yet is inserted; one
  already stored is replaced only if its changes_count grew (a changeset can
  keep growing while open, up to 24h), and left alone otherwise (e.g. it
  resurfaced in the replication feed because someone commented on it).
- Idempotent: writing the same records twice leaves the same data, so a
  batch that failed on one writer can simply be retried on all of them.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WriteResult:
    created: int   # rows written, including replaced ones
    skipped: int   # already stored with an equal or larger changes_count
    updated: int   # replaced because changes_count grew (also counted in created)


class ChangesetWriter(Protocol):
    name: str

    def write(self, records: list, log_extra: dict) -> WriteResult:
        """Store parsed changeset records (dicts with Changeset field names,
        locations already set by ingest.locate)."""

    def after_backfill(self, start, end, stdout=None) -> None:
        """Maintenance after a bulk write over [start, end) outside the normal
        live window (e.g. a dump import). No-op if the backend needs none."""
