"""Reads the .env file for runs outside Docker (in Docker, compose passes it as the environment
and the file isn't in the image). Replaces python-dotenv: same results on its format's subset
below, checked against dotenv_values on env.example, a real .env and edge cases (2026-10-28)."""
import os
import re

_LINE = re.compile(r'''^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.*?)\s*$''')
_VAR = re.compile(r'\$\{([^}:]+)(?::-([^}]*))?\}')
_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\', "'": "'"}


def read_env_file(path):
    """KEY=VALUE pairs of a .env file, the subset of python-dotenv's format these files use:
    `export ` prefixes, `#` comments (inline ones after whitespace, on unquoted values), single
    quotes (literal) and double quotes (backslash escapes), ${VAR} and ${VAR:-default} expanded
    from earlier lines and the environment."""
    values = {}
    try:
        lines = open(path, encoding='utf-8').read().splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        match = _LINE.match(line)
        if not match or line.lstrip().startswith('#'):
            continue
        key, raw = match.groups()
        quote = raw[:1] if raw[:1] in ('"', "'") else None
        end = re.search(r'(?<!\\)' + quote, raw[1:]) if quote else None
        if end:
            body = raw[1:end.start() + 1]
            if quote == "'":
                value = re.sub(r"\\([\\'])", r'\1', body)
            else:
                value = re.sub(r'\\(.)', lambda m: _ESCAPES.get(m.group(1), m.group(0)), body)
        else:
            value = re.sub(r'\s+#.*$', '', raw)
        value = _VAR.sub(lambda m: values.get(m.group(1), os.environ.get(m.group(1))) or m.group(2) or '', value)
        values[key] = value
    return values
