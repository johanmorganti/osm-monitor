# MCP server

Expose OSM Monitor's data to AI assistants through a Model Context Protocol (MCP) server, so a
user can ask "which editors created the most buildings in France last month?" or "what's the most
edited object this week?" and the assistant answers from this app's data.

Added to the TODO 2026-10-05; nothing designed yet. Starting points:

- **Tools mirror the public API's shapes**, not new queries: summary, timeseries, toplist
  (including the object dimensions), distribution, largest changesets, map cells, most edited
  objects, raw changeset search, autocomplete for filter values. Each tool calls the same backend
  contract (`changesets/analytics/base.py`) as the API, so answers match the dashboard and every
  query keeps the API's limits (ranges, row caps, ClickHouse's 30 s cap).
- **Where it runs:** a streamable-HTTP endpoint served by the Django app (one more path, no new
  service), or a small separate service using the Python MCP SDK. To decide.
- **Access:** the data is public, like the API; decide whether it's open, behind a token, or
  local-only at first.
- **Tool descriptions** carry what the API docs say today (dimensions, `(none)` bucket, objects
  since `objects_since`, the 7-day window of most edited objects), so the assistant asks
  questions the data can answer.
