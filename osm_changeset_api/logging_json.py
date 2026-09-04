import json
import logging

# Fields present on every stdlib LogRecord — anything else on the record is
# either something we passed via `extra={...}` or an attribute ddtrace injects
# (dd.trace_id, dd.span_id, dd.service, dd.env, dd.version) when
# DD_LOGS_INJECTION=true, both of which we want to surface as top-level JSON
# fields for Datadog Logs<->APM correlation and log-based filtering.
_RESERVED_FIELDS = set(vars(logging.LogRecord('', 0, '', 0, '', (), None))) | {
    'message', 'asctime',
}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            'timestamp': self.formatTime(record, '%Y-%m-%dT%H:%M:%S%z'),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }

        if record.exc_info:
            payload['error.kind'] = record.exc_info[0].__name__
            payload['error.message'] = str(record.exc_info[1])
            payload['error.stack'] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _RESERVED_FIELDS:
                payload[key] = value

        return json.dumps(payload, default=str)
