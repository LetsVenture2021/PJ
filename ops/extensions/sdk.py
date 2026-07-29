"""Small local SDK and deterministic extension conformance harness."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from jsonschema import Draft202012Validator


class ExtensionService(Protocol):
    def call(
        self, name: str, payload: dict[str, Any], *, idempotency_key: str, cancelled: bool = False
    ) -> dict[str, Any]: ...


@dataclass
class MockServices:
    provider: list[dict[str, Any]] = field(default_factory=list)
    connector: list[dict[str, Any]] = field(default_factory=list)
    filesystem: dict[str, bytes] = field(default_factory=dict)
    clock: float = 0.0
    approvals: dict[str, bool] = field(default_factory=dict)


class ConformanceHarness:
    def validate(
        self,
        service: ExtensionService,
        name: str,
        payload: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> None:
        key = "conformance-idempotency-key"
        first = service.call(name, payload, idempotency_key=key)
        second = service.call(name, payload, idempotency_key=key)
        if json.dumps(first, sort_keys=True) != json.dumps(second, sort_keys=True):
            raise AssertionError("extension is not idempotent")
        Draft202012Validator(output_schema).validate(first)
        cancelled = service.call(name, payload, idempotency_key="cancelled", cancelled=True)
        if cancelled.get("status") != "cancelled":
            raise AssertionError("extension ignored cancellation")
        if "artifact" in first:
            artifact = first["artifact"]
            if (
                not isinstance(artifact, dict)
                or not {"media_type", "sha256", "size"} <= artifact.keys()
            ):
                raise AssertionError("invalid artifact contract")

    @staticmethod
    def validate_log(record: dict[str, Any]) -> None:
        forbidden = {"prompt", "arguments", "result", "body", "authorization", "secret"}
        if forbidden & {key.lower() for key in record}:
            raise AssertionError("log is not metadata-only")
