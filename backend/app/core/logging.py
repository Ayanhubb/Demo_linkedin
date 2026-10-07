"""JSON logs. Secret values are redacted before a record is emitted."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import traceback
from typing import Any

_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_SECRET_PARAM = re.compile(
    r"(?i)\b(access_token|refresh_token|client_secret|token_encryption_key|authorization|code)"
    r"(\s*[:=]\s*)([^\"'\s&,}]+)"
)
_FERNET_TOKEN = re.compile(r"gAAAAA[A-Za-z0-9_\-]+=*")

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_configured = False


def redact_text(value: str) -> str:
    """Remove OAuth secrets and Fernet tokens from a log line."""
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _SECRET_PARAM.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)
    return _FERNET_TOKEN.sub("[REDACTED]", value)


class RedactSecretsFilter(logging.Filter):
    """Logging filter that never lets OAuth tokens or client secrets through."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            return True
        redacted = redact_text(rendered)
        record.msg = redacted
        record.args = ()
        record.message = redacted
        if record.exc_info:
            trace = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = redact_text(trace)
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Event payloads stay a single object."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: dict[str, Any]
        if message.startswith("{") and message.endswith("}"):
            try:
                parsed = json.loads(message)
            except json.JSONDecodeError:
                parsed = None
            payload = parsed if isinstance(parsed, dict) else {"message": message}
        else:
            payload = {"message": message}
        payload.setdefault("level", record.levelname)
        payload.setdefault("logger", record.name)
        payload["time"] = self.formatTime(record, "%Y-%m-%dT%H:%M:%S")
        if record.exc_text:
            payload["trace"] = record.exc_text
        return redact_text(json.dumps(payload, default=str))


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    post_id: object = None,
    user_id: object = None,
    status: object = None,
    attempt_count: int | None = None,
    duration: float | None = None,
) -> None:
    """Write one structured event. Null fields are kept so every line has the same keys."""
    payload = {
        "event": event,
        "request_id": request_id_var.get(),
        "post_id": None if post_id is None else str(post_id),
        "user_id": None if user_id is None else str(user_id),
        "status": None if status is None else str(status),
        "attempt_count": attempt_count,
        "attempt": attempt_count,
        "duration": None if duration is None else round(duration, 3),
    }
    logger.log(level, "%s", json.dumps(payload))


def configure_logging() -> None:
    """Attach secret redaction and JSON formatting. Safe to call more than once."""
    global _configured
    root = logging.getLogger()
    if not any(isinstance(item, RedactSecretsFilter) for item in root.filters):
        root.addFilter(RedactSecretsFilter())
    formatter = JsonFormatter()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler.addFilter(RedactSecretsFilter())
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    else:
        for handler in root.handlers:
            handler.setFormatter(formatter)
            if not any(isinstance(item, RedactSecretsFilter) for item in handler.filters):
                handler.addFilter(RedactSecretsFilter())
    _configured = True
