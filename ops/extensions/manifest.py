"""Manifest loading, schema validation, compatibility, and permission analysis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, RefResolver
from packaging.version import Version

from pj_contract import PROTOCOL_VERSION
from ops.extensions.models import ExtensionError

CONTRACTS = Path(__file__).with_name("contracts")
KNOWN_PERMISSIONS = {
    "local_read",
    "approval_action",
    "connector",
    "artifact_write",
    "event_subscribe",
    "execute_code",
}


def _validator() -> Draft202012Validator:
    schema = json.loads((CONTRACTS / "manifest-v1.schema.json").read_text())
    return Draft202012Validator(schema, resolver=RefResolver(CONTRACTS.as_uri() + "/", schema))


def parse_manifest(raw: bytes | str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ExtensionError("invalid manifest JSON") from exc
    errors = sorted(_validator().iter_errors(value), key=lambda item: list(item.path))
    if errors:
        raise ExtensionError(f"invalid manifest: {errors[0].message}")
    if value["minimum_pj_protocol"] > PROTOCOL_VERSION:
        raise ExtensionError("extension requires an incompatible PJ protocol")
    Version(value["version"])
    if any("://" in source for source in value.get("dependencies", {}).values()):
        raise ExtensionError("dependencies must resolve from the signed local package")
    declared = set(value["permissions"])
    if not declared <= KNOWN_PERMISSIONS:
        raise ExtensionError("unknown permission")
    if value.get("code") and "execute_code" not in declared:
        raise ExtensionError("code-bearing package omitted execute_code permission")
    if value["network_domains"] and "connector" not in declared:
        raise ExtensionError("network domains require connector permission")
    for tool in value["tools"]:
        if tool["approval"] == "required" and "approval_action" not in declared:
            raise ExtensionError("approval-sensitive tool omitted approval_action permission")
    return value


def permission_diff(
    previous: dict[str, Any] | None, candidate: dict[str, Any]
) -> dict[str, list[str]]:
    fields = ("permissions", "network_domains", "filesystem_scope", "secrets", "approval_modes")
    old = previous or {}
    return {field: sorted(set(candidate[field]) - set(old.get(field, []))) for field in fields}


def policy_entries(
    manifest: dict[str, Any], mapping: dict[str, str] | None = None
) -> dict[str, str]:
    mapping = mapping or {}
    result = {}
    for tool in manifest["tools"]:
        decision = mapping.get(tool["name"])
        if decision not in {"allow", "approval", "deny"}:
            decision = "approval" if tool["approval"] == "required" else "deny"
        result[tool["name"]] = decision
    return result


def realtime_tools(manifest: dict[str, Any], policy: dict[str, str]) -> list[str]:
    """Return only non-approval tools explicitly allowed by policy."""
    return [
        tool["name"]
        for tool in manifest["tools"]
        if tool["approval"] == "none" and policy.get(tool["name"], "deny") == "allow"
    ]
