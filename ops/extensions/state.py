"""Durable lifecycle, evaluation, rollback, revocation, and tombstone state."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops.extensions.models import ExtensionError
from ops.shared.io import read_json, write_json_atomic


class ExtensionState:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"packages": {}, "evaluations": [], "revoked_publishers": []}
        value = read_json(self.path)
        return value

    def _write(self, state: dict[str, Any]) -> None:
        write_json_atomic(self.path, state)

    def record_install(
        self, manifest: dict[str, Any], digest: str, *, approved_broadening: bool
    ) -> None:
        state = self._read()
        identifier = manifest["identifier"]
        current = state["packages"].get(identifier)
        if current and current.get("permission_diff") and not approved_broadening:
            raise ExtensionError("broader upgrade permissions require fresh approval")
        versions = list(current.get("versions", [])) if current else []
        versions.append({"version": manifest["version"], "digest": digest, "manifest": manifest})
        state["packages"][identifier] = {
            "versions": versions,
            "staged": manifest["version"],
            "active": current.get("active") if current else None,
            "disabled": True,
            "revoked": False,
            "tombstone": False,
            "permission_diff": {},
        }
        self._write(state)

    def activate(self, identifier: str) -> None:
        state = self._read()
        item = state["packages"].get(identifier)
        if not item or item["revoked"] or item["tombstone"]:
            raise ExtensionError("extension cannot be activated")
        item["active"] = item["staged"]
        item["disabled"] = False
        self._write(state)

    def rollback(self, identifier: str) -> None:
        state = self._read()
        item = state["packages"].get(identifier)
        versions = item["versions"] if item else []
        if len(versions) < 2:
            raise ExtensionError("no rollback version")
        item["active"] = versions[-2]["version"]
        item["disabled"] = False
        self._write(state)

    def disable(self, identifier: str) -> None:
        state = self._read()
        state["packages"][identifier]["disabled"] = True
        self._write(state)

    def revoke(self, identifier: str) -> None:
        state = self._read()
        item = state["packages"][identifier]
        item["revoked"] = item["disabled"] = True
        self._write(state)

    def uninstall(self, identifier: str) -> None:
        state = self._read()
        item = state["packages"][identifier]
        item.update(
            {"versions": [], "active": None, "staged": None, "disabled": True, "tombstone": True}
        )
        self._write(state)

    def evaluate(self, identifier: str, outcome: str, metadata: dict[str, Any]) -> None:
        allowed = {"duration_ms", "status", "test_id", "contract"}
        if set(metadata) - allowed:
            raise ExtensionError("evaluation records must be metadata-only")
        state = self._read()
        state["evaluations"].append(
            {
                "identifier": identifier,
                "outcome": outcome,
                "at": datetime.now(timezone.utc).isoformat(),
                **metadata,
            }
        )
        self._write(state)
