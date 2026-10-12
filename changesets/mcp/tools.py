"""The MCP server's tools: one per public API endpoint, described by that
endpoint's OpenAPI annotations and answered by calling the endpoint itself.

So a tool takes the same parameters as its endpoint, enforces the same
defaults, limits and errors, and returns the same JSON: the MCP server adds
no query of its own, and an `@extend_schema` change updates the tool too.
See docs/decisions/mcp-server.md."""
import io
import json
from functools import cache
from urllib.parse import urlencode

from django.core.handlers.wsgi import WSGIRequest
from django.urls import resolve, reverse
from drf_spectacular.generators import SchemaGenerator

# (tool name, API URL name). Order is the order clients list them in.
TOOL_ENDPOINTS = (
    ('summary', 'changeset-summary'),
    ('timeseries', 'changeset-timeseries'),
    ('toplist', 'changeset-toplist'),
    ('size_distribution', 'changeset-distribution'),
    ('size_by_group', 'changeset-size-breakdown'),
    ('largest_changesets', 'changeset-largest'),
    ('map_cells', 'changeset-geo'),
    ('map_cell_changesets', 'changeset-geo-cell'),
    ('most_edited_objects', 'objects-most-edited'),
    ('list_changesets', 'changeset-query'),
    ('autocomplete', 'autocomplete'),
)

# Every tool only reads public data; repeating a call changes nothing.
ANNOTATIONS = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}


class ToolError(Exception):
    """A call the endpoint rejected or failed: reported to the model as a tool
    result with isError, not as a protocol error, so it can correct itself."""


def _json_schema(param_schema):
    """OpenAPI 3.0 parameter schema -> JSON Schema for a tool argument."""
    out = {'type': param_schema.get('type', 'string')}
    for key in ('format', 'enum', 'minimum', 'maximum'):
        if key in param_schema:
            out[key] = param_schema[key]
    return out


@cache
def tools():
    """The tool definitions, built once per process from the OpenAPI schema."""
    schema = SchemaGenerator().get_schema(request=None, public=True)
    result = []
    for name, url_name in TOOL_ENDPOINTS:
        path = reverse(url_name)
        operation = schema['paths'][path]['get']
        properties, required = {}, []
        for p in operation.get('parameters', []):
            if p.get('in') != 'query':
                continue
            properties[p['name']] = {**_json_schema(p.get('schema', {})), 'description': p.get('description', '')}
            if p.get('required'):
                required.append(p['name'])
        input_schema = {'type': 'object', 'properties': properties, 'additionalProperties': False}
        if required:
            input_schema['required'] = required
        result.append({
            'name': name,
            'title': operation.get('summary', name),
            'description': f"{operation.get('description', '')}\n\nSame as the REST API's GET {path}",
            'inputSchema': input_schema,
            'annotations': ANNOTATIONS,
            '_path': path,
        })
    return result


def public_tools():
    """tools() as MCP's tools/list returns them (without internal keys)."""
    return [{k: v for k, v in t.items() if not k.startswith('_')} for t in tools()]


def call_tool(request, name, arguments):
    """Run tool `name` by calling its endpoint's view with `arguments` as the
    query string; the endpoint's JSON response body. `request` is the MCP
    request, whose headers (host, scheme, analytics backend override) the
    endpoint sees as its own. Raises KeyError for an unknown tool, ToolError
    when the endpoint answers with an error."""
    tool = next((t for t in tools() if t['name'] == name), None)
    if tool is None:
        raise KeyError(name)
    arguments = arguments or {}
    unknown = sorted(set(arguments) - set(tool['inputSchema']['properties']))
    if unknown:
        raise ToolError(f'unknown argument(s): {", ".join(unknown)}')

    query = urlencode({k: _query_value(v) for k, v in arguments.items() if v is not None})
    environ = {k: v for k, v in request.META.items() if k not in ('CONTENT_LENGTH', 'CONTENT_TYPE')}
    environ.update({
        'REQUEST_METHOD': 'GET', 'PATH_INFO': tool['_path'], 'SCRIPT_NAME': '', 'QUERY_STRING': query,
        'HTTP_ACCEPT': 'application/json', 'wsgi.input': io.BytesIO(b''),
    })
    match = resolve(tool['_path'])
    response = match.func(WSGIRequest(environ), *match.args, **match.kwargs)
    if hasattr(response, 'render'):
        response.render()
    body = json.loads(response.content) if response.content else None
    if response.status_code >= 400:
        message = (body.get('error') or body.get('detail')) if isinstance(body, dict) else None
        raise ToolError(message or f'HTTP {response.status_code}: {body}')
    return body


def _query_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)
