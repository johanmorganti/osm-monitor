# MCP server: the REST endpoints as tools, served by Django

`/mcp` is a Model Context Protocol server (Streamable HTTP transport) for AI assistants, documented
on the API / MCP page (`/api/docs/`). Built 2026-10-11.

## Each tool calls its REST endpoint

A tool is one public endpoint (`TOOL_ENDPOINTS` in `changesets/mcp/tools.py`): `tools/call` runs
that endpoint's view in-process with the arguments as its query string and returns its JSON body
unchanged. The tool's description and argument schema are the endpoint's OpenAPI operation
(drf-spectacular, built once per process).

- **Answers can't diverge** from the API or the dashboard: same parsing, defaults (last 7 days),
  limits, validation errors, `(none)` bucket and 30 s ClickHouse cap. Calling the backend contract
  directly would have duplicated the views' parameter handling, which is public-API behavior and
  lives in `changesets/api/` only.
- **Documentation can't drift**: changing an endpoint's `@extend_schema` changes its tool. That's
  also why the parameters gained `enum`s (`group_by`, `dimension`, `metric`, `interval`,
  `resolution`, `by`, `field`): a typed choice helps a model more than "one of: …" in prose, and
  the REST reference shows them too.
- An endpoint's 4xx/5xx becomes a tool result with `isError: true` and the API's error message,
  so the model can correct the call; an unknown tool is a JSON-RPC error.
- Not exposed: `batch-progress` (poller state, not data).
- The server's `instructions` (`changesets/mcp/server.py`) carry what spans tools: date
  conventions, exact-name filters and `autocomplete`, the `(none)` bucket, `objects_since`, the
  30 s cap.

## Transport: stateless JSON over POST, in the existing web service

No new service and no MCP SDK: the official Python SDK's HTTP transport is ASGI (Starlette), and
the app runs under gunicorn's WSGI workers. A read-only server with no notifications needs only
the transport's stateless subset: each POST holds one JSON-RPC message (or a batch) and gets
`application/json` back; notifications get 202; GET answers 405 (the spec's "no SSE stream"); no
`Mcp-Session-Id`. Protocol versions 2025-03-26 to 2025-11-25 are accepted (a client asking for
another gets the newest). Checked with the official Python SDK client (`mcp` 2.3.0): initialize,
tools/list, tools/call.

`/mcp` and `/mcp/` both answer: `APPEND_SLASH` would redirect, and a redirected POST is lost by
most clients.

## Access: open, like the API

The data is public and the API has no key, so neither does the MCP server; CORS allows any
origin (browser-based clients, the MCP Inspector). The cost of a call is the cost of the API call
it makes, already bounded by the endpoints' limits and ClickHouse's 30 s cap. If abuse shows up,
the lever is the same for both (rate limiting at the proxy), not a token on MCP alone.

A batch holds at most 10 messages (`MAX_BATCH`; an empty or larger one gets one -32600 error). Before
this cap (2026-10-30) one POST could run any number of tool calls, each up to 30 s, in a single
web thread, and the proxy's rate limit counts the POST once.
