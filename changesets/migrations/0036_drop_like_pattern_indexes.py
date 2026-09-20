from django.db import migrations


# Django auto-creates a companion "_like" index (varchar_pattern_ops) for
# every CharField with db_index=True on Postgres — meant to support
# anchored LIKE 'prefix%' queries. No query in this codebase issues that
# shape against Changeset: ChangesetQueryView filters with exact `=`,
# _filtered_changesets (views.py) with `__iexact` (a leading-and-trailing
# wildcard under the hood, which a pattern-ops index can't serve either
# way). Confirmed by measurement, not just code inspection: the one shape
# these indexes *could* serve (an anchored prefix scan) took 90 seconds via
# a "SkipScan" plan across all 141 chunks when tested directly, and the
# codebase's actual `icontains`-style autocomplete already goes through the
# tiny, separately-maintained FilterValue table instead (15ms) — see the
# data-architecture teardown (2026-09-17/18) for both measurements.
#
# DROP only — deliberately not touching Changeset.user/created_by_family/
# imagery_family/locale_family's `db_index=True` in models.py, and not
# recreating the plain (non-pattern-ops) index under a new name. Postgres's
# `_like` companion is a side effect of the schema editor's DDL for a
# db_index=True CharField, not a separately-tracked migration-state object
# — Django has nothing to "notice missing" and re-add here, so this is a
# pure win with no model change needed. (Converging further — e.g. to just
# the UPPER() expression index per column, dropping the plain one too —
# is real future work, deliberately deferred: it needs actual usage
# counters, and doing it means another CREATE INDEX pass, the expensive
# operation this migration avoids. See docs/todo/unusable-like-indexes.md.)
#
# Note for later: if any of these four fields' db_index value is ever
# touched again by a future AlterField, Django's schema editor will
# regenerate a _like companion as a side effect of that unrelated change —
# harmless, but worth knowing before being surprised by it reappearing.
_DROP_SQL = [
    "DROP INDEX IF EXISTS changesets_changeset_created_by_family_a78517b7_like;",
    "DROP INDEX IF EXISTS changesets_changeset_imagery_family_3245dad9_like;",
    "DROP INDEX IF EXISTS changesets_changeset_locale_family_55c644e0_like;",
    "DROP INDEX IF EXISTS changesets_changeset_user_a41f6ec1_like;",
]

_RECREATE_SQL = [
    "CREATE INDEX changesets_changeset_created_by_family_a78517b7_like ON changesets_changeset USING btree (created_by_family varchar_pattern_ops);",
    "CREATE INDEX changesets_changeset_imagery_family_3245dad9_like ON changesets_changeset USING btree (imagery_family varchar_pattern_ops);",
    "CREATE INDEX changesets_changeset_locale_family_55c644e0_like ON changesets_changeset USING btree (locale_family varchar_pattern_ops);",
    'CREATE INDEX changesets_changeset_user_a41f6ec1_like ON changesets_changeset USING btree ("user" varchar_pattern_ops);',
]


class Migration(migrations.Migration):
    # DROP INDEX on a hypertable is a catalog operation across every
    # chunk — same reasoning as every other RunSQL migration touching
    # changesets_changeset — though unlike CREATE INDEX, DROP needs no
    # heap scan, so this one is cheap regardless.
    atomic = False

    dependencies = [
        ('changesets', '0035_rollupstate_last_created_at'),
    ]

    operations = [
        migrations.RunSQL(sql=_DROP_SQL, reverse_sql=_RECREATE_SQL),
    ]
