"""Structured logging, correlation context, and sensitive-value redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping, TextIO
from uuid import uuid4

REDACTED = "[REDACTED]"

_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "credentials",
    "password",
    "proxy_authorization",
    "set_cookie",
}
_SENSITIVE_SUFFIXES = (
    "_api_key",
    "_password",
    "_secret",
    "_token",
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(['\"]?\b("
    r"authorization|api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|"
    r"password|secret|cookie"
    r")\b['\"]?\s*[:=]\s*)"
    r"(?:['\"]?bearer\s+)?"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s,;}\]'\"\]]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_LOG_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "pj_log_context",
    default={},
)
_STANDARD_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
}


def _is_sensitive_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES)


def _redact_string(value: str) -> str:
    value = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{REDACTED}",
        value,
    )
    value = _BEARER_TOKEN.sub(REDACTED, value)
    return _OPENAI_KEY.sub(REDACTED, value)


def redact_sensitive(value: Any, *, key: object | None = None) -> Any:
    """Return a JSON-safe copy with secret-bearing keys and strings redacted."""
    if key is not None and _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_sensitive(item_value, key=item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_string(str(value))


def new_correlation_id(candidate: object | None = None) -> str:
    """Return a bounded caller-supplied correlation ID or generate a UUID."""
    value = str(candidate or "").strip()
    return value if _CORRELATION_ID.fullmatch(value) else str(uuid4())


def current_log_context() -> dict[str, Any]:
    return dict(_LOG_CONTEXT.get())


def set_log_context(**fields: Any) -> contextvars.Token:
    """Replace the current logging context and return a reset token."""
    return _LOG_CONTEXT.set(
        {key: value for key, value in fields.items() if value not in (None, "")}
    )


def bind_log_context(**fields: Any) -> contextvars.Token:
    """Add or replace fields in the current logging context."""
    context = current_log_context()
    for key, value in fields.items():
        if value in (None, ""):
            context.pop(key, None)
        else:
            context[key] = value
    return _LOG_CONTEXT.set(context)


def clear_log_context() -> None:
    _LOG_CONTEXT.set({})


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Temporarily bind correlation fields to all PJ logs in this context."""
    token = bind_log_context(**fields)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


class JsonFormatter(logging.Formatter):
    """Format standard logging records as one redacted JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            )
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact_sensitive(record.getMessage()),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_")
        }
        payload.update(redact_sensitive(extras))
        payload.update(redact_sensitive(current_log_context()))
        if record.exc_info:
            payload["exception"] = redact_sensitive(self.formatException(record.exc_info))
        return json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


def configure_logging(
    *, level: str | int | None = None, stream: TextIO | None = None, force: bool = False
) -> logging.Logger:
    """Configure the ``pj`` logger hierarchy for redacted JSON output."""
    logger = logging.getLogger("pj")
    configured_level: str | int = level if level is not None else os.getenv("PJ_LOG_LEVEL", "INFO")
    if isinstance(configured_level, str):
        configured_level = configured_level.upper()
    logger.setLevel(configured_level)
    logger.propagate = False

    structured_handlers = [
        handler for handler in logger.handlers if getattr(handler, "_pj_structured_handler", False)
    ]
    if force:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        structured_handlers = []
    if not structured_handlers:
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(JsonFormatter())
        setattr(handler, "_pj_structured_handler", True)
        logger.addHandler(handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"pj.ops.{name}")
