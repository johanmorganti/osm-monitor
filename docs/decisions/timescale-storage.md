# TimescaleDB storage (deprecated backend)

> **2026-10-05:** TimescaleDB is gone; this describes the removed storage, kept as history. See [timescale-removal.md](timescale-removal.md).

## TimescaleDB hypertable (deprecated backend)

`changesets_changeset` is a TimescaleDB hypertable (monthly chunks on `created_at`) — see
`docs/ARCHITECTURE.md`'s "Why a hypertable" section for the full reasoning and the PK/unique-
constraint trade-off it required (`id` is no longer DB-enforced-unique; real duplicate protection
is the composite `UNIQUE(changeset_id, created_at)` added in migration
`0018_timescale_hypertable`). Django ORM code is otherwise unaffected.

## Compression: editor segmentby + bloom sparse indexes, so filter by equality (2026-09-26)

`changesets_changeset` compresses chunks older than 30 days (`policy_compression`), with
`segmentby = created_by_family` and `orderby = created_at DESC`. Every other queryable column
(`user`, `imagery_family`, `locale_family`, `country_code`, `geohash`, `changeset_id`) is covered by
a per-batch **bloom-filter sparse index**, which TimescaleDB (2.19+, `auto_sparse_indexes`) builds
automatically at compression time for each column that has a plain btree index on the hypertable.
So a new filterable column needs a plain btree index, or its compressed chunks can't be skipped.
Why not segment by every dimension: segments are per distinct combination per chunk, and
contributor alone has ~25K distinct values in a single month, so segments of a handful of rows
would mean little compression and huge overhead. Measured on one 1.2M-row monthly chunk: 468MB →
62MB compressed.

**Bloom filters only help equality/IN predicates.** `UPPER(col) = UPPER(x)` (Django `__iexact`)
can't use them and decompresses every batch in range: ~990ms per monthly chunk, against ~23ms for
the same lookup by equality. So `filtered_changesets` (Timescale backend) resolves case-insensitive input to its exact
stored spelling(s) via `FilterValue` (`canonical_values`, indexed on `(field, UPPER(value))`)
and filters with `IN`. Any new raw-table filter must follow the same pattern, not `__iexact`/
`__icontains`.

**Compressing a large backlog** (e.g. after a bulk import): pause the policy during the import, so
it doesn't compress chunks that are still being written to. Then compress chunk by chunk, oldest
first, rather than letting the policy take the whole backlog in one pass. Its first activation
here did that, ran 18+ minutes on one chunk while starving other queries, and was killed. See
`docs/DEPLOYMENT.md`.
