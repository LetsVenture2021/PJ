"""Allowlist-based shared representations; private state is never inherited."""

from __future__ import annotations

from typing import Any, Mapping

_SHARED_FIELDS = frozenset(
    {"id", "type", "title", "summary", "content", "created_at", "updated_at"}
)


def sanitize_shared_resource(resource: Mapping[str, Any]) -> dict[str, Any]:
    """Build a new representation rather than attempting to redact unknown secrets."""
    shared = {key: resource[key] for key in _SHARED_FIELDS if key in resource}
    shared["shared"] = True
    return shared
