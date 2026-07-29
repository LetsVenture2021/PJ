"""Connector registration, policy completeness, and transport-neutral lookup."""

from __future__ import annotations

from .models import ConnectorManifest


class RegistryError(ValueError):
    pass


class ConnectorRegistry:
    def __init__(self) -> None:
        self._manifests: dict[str, ConnectorManifest] = {}

    def register(self, manifest: ConnectorManifest) -> None:
        if manifest.schema_version != "1.0":
            raise RegistryError("Unsupported connector manifest version")
        if manifest.connector_id in self._manifests:
            raise RegistryError("Connector already registered")
        self._manifests[manifest.connector_id] = manifest

    def get(self, connector_id: str) -> ConnectorManifest:
        return self._manifests[connector_id]

    def all(self) -> tuple[ConnectorManifest, ...]:
        return tuple(self._manifests.values())

    def validate_policy(self, policy: dict[str, str]) -> None:
        missing = [
            f"connector.{m.connector_id}.{a.name}"
            for m in self.all()
            for a in m.actions
            if f"connector.{m.connector_id}.{a.name}" not in policy
        ]
        if missing:
            raise RegistryError("Missing connector action policies: " + ", ".join(missing))
