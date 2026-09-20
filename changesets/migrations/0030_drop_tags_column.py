from django.db import migrations


# tags duplicated every other column on Changeset: osm_fetcher.py wrote every
# tag into it unconditionally, before the promotion chain that also splits
# each tag into a dedicated column (created_by, comment, locale, ...) or,
# failing that, remaining_tags. Verified against a live 3,947-row sample
# (2026-09-18) before dropping: remaining_tags was always a subset of tags,
# never contained a promoted key, and zero keys existed in tags that weren't
# already in one of the other two — i.e. tags == dedicated columns UNION
# remaining_tags, with no unique information of its own. At ~239 bytes/row
# average width and 0% null, it was also the single largest column on the
# table (~4.2 GB of the then-18 GB heap).
#
# ALTER TABLE ... DROP COLUMN on a hypertable is metadata-only and
# propagates to every chunk's catalog entry — same reasoning as 0018/0019's
# atomic=False, and fast for the same reason. It does NOT shrink any chunk
# on disk: the column's bytes stay in each page as dead space until that
# page is rewritten (VACUUM FULL, or — the intended path here — the
# still-pending compression backlog pass from TODO.md, which rewrites every
# chunk anyway). Deliberately not running VACUUM FULL as part of this
# migration: it locks and rewrites the whole 18 GB heap, the same kind of
# single-big-operation I/O spike that already forced the compression policy
# to be paused (see TODO.md's "Compression backlog" entry) — the disk space
# comes back for free once that pass actually runs.
#
# Reverse just re-adds an empty column, matching 0018/0019's stance that
# reversing a hypertable DDL migration is only meant for a fresh/empty
# database, not for restoring dropped data.
_DROP_COLUMN_SQL = "ALTER TABLE changesets_changeset DROP COLUMN IF EXISTS tags;"
_ADD_COLUMN_SQL = "ALTER TABLE changesets_changeset ADD COLUMN tags jsonb;"


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('changesets', '0029_changeset_centroid'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name='changeset',
                    name='tags',
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=_DROP_COLUMN_SQL,
                    reverse_sql=_ADD_COLUMN_SQL,
                ),
            ],
        ),
    ]
