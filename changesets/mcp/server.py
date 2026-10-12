"""MCP (Model Context Protocol) server: the public API's endpoints as tools for
AI assistants, over the Streamable HTTP transport, at /mcp.

Stateless and JSON-only: every POST carries one JSON-RPC message (or a batch)
and gets its answer as application/json; no sessions, no server-sent events
(GET answers 405, as the transport allows). That is all a read-only server
with no notifications needs, and it runs in the existing gunicorn workers
like any other request. See docs/decisions/mcp-server.md."""
import json
import logging

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .tools import ToolError, call_tool, public_tools

logger = logging.getLogger(__name__)

# Newest first; a client asking for another version gets the newest.
PROTOCOL_VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26')

SERVER_INFO = {'name': 'osm-monitor', 'title': 'OSM Monitor', 'version': '1.0.0'}

INSTRUCTIONS = """\
OSM Monitor's data: every OpenStreetMap changeset since 2005 (from planet.osm.org's replication \
and changeset dumps), plus the objects each one changed (from the replication diffs, recent months \
only). The same numbers as the dashboard and the REST API; each tool is one REST endpoint.

- Dates: start_date/end_date are YYYY-MM-DD, end inclusive, in UTC; omitted, the range is the last \
7 days. Long ranges are fine for summary/toplist/timeseries; ClickHouse cancels a query after 30 s \
(the tool then reports an error: narrow the range or add a filter).
- Filters (contributor, editor, imagery, language, country) are exact names, case-insensitive; use \
autocomplete to find the exact spelling (e.g. an editor family "StreetComplete", a country ISO code \
"FR"). Editor is the editor family (iD, JOSM, StreetComplete, ...); toplist dimension=editor_version \
breaks one family down by version.
- "(none)" in a ranking or series is the changesets without that value (no imagery recorded, no \
hashtag, no country found for its location): real volume, not an error. Passing "(none)" as a \
filter selects those changesets.
- metric=count counts changesets; metric=objects counts objects changed (nodes, ways, relations; \
changes_count). Object dimensions (action, object_type, feature) come from the replication diffs, \
which start at the response's `objects_since`: earlier days count nothing for them.
- most_edited_objects is always the last 7 days. list_changesets and map_cell_changesets return \
individual changesets (link one as https://www.openstreetmap.org/changeset/<id>).
- For "which country" questions use toplist dimension=country rather than map_cells, which returns \
every grid cell of the world.
"""

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@csrf_exempt
def mcp_endpoint(request):
    if request.method == 'OPTIONS':
        return _cors(HttpResponse(status=204))
    if request.method != 'POST':
        response = HttpResponse(status=405)
        response['Allow'] = 'POST, OPTIONS'
        return _cors(response)

    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return _cors(JsonResponse(_error(None, JSONRPC_PARSE_ERROR, 'Parse error')))

    if isinstance(payload, list):
        replies = [r for r in (_handle(request, m) for m in payload) if r is not None]
        return _cors(JsonResponse(replies, safe=False) if replies else HttpResponse(status=202))
    reply = _handle(request, payload)
    return _cors(JsonResponse(reply) if reply is not None else HttpResponse(status=202))


def _handle(request, message):
    """The reply to one JSON-RPC message, or None for a notification (and for
    a client's response, which this server never asks for)."""
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        return _error(None, JSONRPC_INVALID_REQUEST, 'Invalid Request')
    if 'method' not in message:
        return None
    msg_id = message.get('id')
    is_notification = 'id' not in message
    try:
        result = _dispatch(request, message['method'], message.get('params') or {})
    except RpcError as e:
        return None if is_notification else _error(msg_id, e.code, e.message)
    return None if is_notification else {'jsonrpc': '2.0', 'id': msg_id, 'result': result}


def _dispatch(request, method, params):
    if method == 'initialize':
        requested = params.get('protocolVersion')
        return {
            'protocolVersion': requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': SERVER_INFO,
            'instructions': INSTRUCTIONS,
        }
    if method == 'ping' or method.startswith('notifications/'):
        return {}
    if method == 'tools/list':
        return {'tools': public_tools()}
    if method == 'tools/call':
        name = params.get('name')
        arguments = params.get('arguments') or {}
        if not isinstance(arguments, dict):
            raise RpcError(JSONRPC_INVALID_PARAMS, 'arguments must be an object')
        try:
            body = call_tool(request, name, arguments)
        except KeyError:
            raise RpcError(JSONRPC_INVALID_PARAMS, f'Unknown tool: {name}')
        except ToolError as e:
            return _tool_result(str(e), is_error=True)
        except Exception:
            logger.exception('MCP tool %s failed', name)
            return _tool_result('Internal error: the query failed. Try again, or narrow the range.', is_error=True)
        return _tool_result(json.dumps(body, separators=(',', ':'), ensure_ascii=False, default=str))
    raise RpcError(JSONRPC_METHOD_NOT_FOUND, f'Method not found: {method}')


def _tool_result(text, is_error=False):
    return {'content': [{'type': 'text', 'text': text}], 'isError': is_error}


def _error(msg_id, code, message):
    return {'jsonrpc': '2.0', 'id': msg_id, 'error': {'code': code, 'message': message}}


def _cors(response):
    """Public, credential-free data: any origin may call it (browser-based
    MCP clients and inspectors included)."""
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
    response['Access-Control-Allow-Headers'] = 'Content-Type, Accept, Mcp-Protocol-Version, Mcp-Session-Id'
    return response
