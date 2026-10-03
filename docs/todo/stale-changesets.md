# Some changesets miss their final update

Found 2026-10-03 while checking the object counts from the replication diffs against
`changes_count`: 2 changesets out of ~2M (Aug 12 - Sep 30) are stored as still open
(`closed_at` NULL) with `changes_count` 1, while the OSM API and the diffs agree they closed with
5 and 3 changes:

- 189345666 (created 2026-09-21 12:27, closed 13:10, 5 changes)
- 189623541 (created 2026-09-27 10:45, closed 11:19, 3 changes)

So the changeset poller (`poll_sequences`) stored an early version and never received, or never
applied, the closing one. Not investigated yet: whether the changeset replication feed skipped
them, or the writers' "replace only if changes_count grew" rule dropped an update.

The diffs give a detector for free: a changeset whose object count in `object_changes` differs
from its `changes_count` once both are settled (closed more than a day ago) is stale, and could
be re-fetched from the OSM API (`/api/0.6/changeset/<id>`).
