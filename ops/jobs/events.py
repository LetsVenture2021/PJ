"""Fail-closed connector event verification and replay protection."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Callable


class EventRejected(ValueError):
    """An event failed authentication or validation."""


@dataclass(frozen=True)
class Connector:
    name: str
    secret: bytes
    schema_validator: Callable[[Any], bool]
    max_body_bytes: int = 256_000
    replay_window_seconds: int = 300


class EventVerifier:
    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], int] = {}

    def verify(
        self,
        connector: Connector,
        body: bytes,
        *,
        event_id: str,
        timestamp: int,
        signature: str,
        now: int | None = None,
    ) -> Any:
        current = int(time.time()) if now is None else now
        if not event_id or len(body) > connector.max_body_bytes:
            raise EventRejected("missing event id or body too large")
        if abs(current - timestamp) > connector.replay_window_seconds:
            raise EventRejected("event outside replay window")
        expected = hmac.new(
            connector.secret, f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise EventRejected("invalid signature")
        key = (connector.name, event_id)
        self._seen = {k: expires for k, expires in self._seen.items() if expires >= current}
        if key in self._seen:
            raise EventRejected("duplicate event")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EventRejected("invalid JSON") from exc
        if not connector.schema_validator(payload):
            raise EventRejected("connector schema rejected event")
        self._seen[key] = current + connector.replay_window_seconds
        return payload
