# Datadog: an ingestion-drop monitor that watches the current poller

The Datadog monitor "Low number of changeset coming" (log alert, created 2022) still queries
`service:osm-to-datadog`, a service that no longer sends logs, so it is permanently in Alert and
says nothing about the current pipeline. Found 2026-10-15.

To do, in the Datadog UI (the Datadog MCP connection has no monitor write access):

- Repoint it at the changeset poller, which logs `Processed sequence` about once a minute:
  `logs("service:osm-monitor-poller \"Processed sequence\"").index("*").rollup("count").last("15m") < 1`.
  15 minutes rather than 5 so that a short network drop (several on 2026-10-09 and 2026-10-13,
  recovered on their own) doesn't page.
- Optionally the same for the diff poller (`service:osm-monitor-diff-poller`), once its
  per-file log line is checked.
- Or delete it, if the synthetics uptime check is enough.
