from django.db import migrations


class Migration(migrations.Migration):
    # Compression setup + policy registration touch Timescale's background
    # job catalog — kept out of one transaction for the same reason as
    # 0018's hypertable conversion.
    atomic = False

    dependencies = [
        ('changesets', '0018_timescale_hypertable'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                # created_by_family (editor, ~800 distinct values) is the
                # segmentby column — low-enough cardinality for good
                # compression ratio and segment exclusion on editor-filtered
                # queries, unlike `user` (300K+ distinct contributors, too
                # high). Chunks compress ~30 days after their range closes,
                # by which point they're effectively write-only for the rare
                # long-lived-changeset stragglers (see the 2011-2025 sparse
                # chunks this host already has) — Timescale decompresses a
                # chunk transparently on insert into it, so those strays
                # still work, just slower.
                "ALTER TABLE changesets_changeset SET ("
                "  timescaledb.compress,"
                "  timescaledb.compress_segmentby = 'created_by_family',"
                "  timescaledb.compress_orderby = 'created_at DESC'"
                ");",
                "SELECT add_compression_policy('changesets_changeset', INTERVAL '30 days');",
            ],
            reverse_sql=[
                "SELECT remove_compression_policy('changesets_changeset');",
                "ALTER TABLE changesets_changeset SET (timescaledb.compress = false);",
                # Not decompressing already-compressed chunks — like 0018,
                # this reverse path is only meant for a fresh/empty database.
            ],
        ),
    ]
