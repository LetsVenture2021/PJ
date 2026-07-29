"""Stable boundary between PJ domains and mail/calendar providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Sequence


class ConnectorError(RuntimeError):
    """A normalized connector failure with no provider details exposed."""


@dataclass(frozen=True)
class ConnectorRecord:
    connector: str
    record_id: str
    kind: str
    source_timestamp: datetime
    payload: dict[str, Any]
    etag: str | None = None


class Connector(Protocol):
    """Read-only normalized connector API."""

    def records(
        self, *, kinds: set[str], since: datetime, until: datetime
    ) -> Sequence[ConnectorRecord]: ...

    def get(self, record_id: str) -> ConnectorRecord: ...


class DraftExecutor(Protocol):
    """Optional mutation interface, invoked only after productivity approval."""

    def get(self, record_id: str) -> ConnectorRecord: ...

    def execute(
        self, operation: str, payload: dict[str, Any], *, idempotency_key: str
    ) -> ConnectorRecord: ...
