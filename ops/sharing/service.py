"""In-process collaboration lifecycle with optimistic concurrency and cleanup hooks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any

from .models import AuditEvent, ResourceVersion
from .sanitization import sanitize_shared_resource


class VersionConflict(RuntimeError):
    """Raised instead of overwriting a concurrently changed resource."""


class CollaborationStore:
    """Reference store for the local, single-process runtime.

    Production persistence can implement the same operations; this class intentionally
    provides no multi-instance coordination.
    """

    def __init__(self) -> None:
        self._resources: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._versions: dict[tuple[str, str, str], list[ResourceVersion]] = {}
        self._audit: list[AuditEvent] = []

    def save(
        self,
        *,
        organization_id: str,
        resource_type: str,
        resource_id: str,
        content: Mapping[str, Any],
        actor_principal_id: str,
        expected_version: int,
    ) -> ResourceVersion:
        key = (organization_id, resource_type, resource_id)
        versions = self._versions.setdefault(key, [])
        if expected_version != len(versions):
            raise VersionConflict(
                f"expected version {expected_version}, current version {len(versions)}"
            )
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
        version = ResourceVersion(
            resource_type=resource_type,
            resource_id=resource_id,
            version=len(versions) + 1,
            content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            author_principal_id=actor_principal_id,
            parent_version_id=versions[-1].id if versions else None,
        )
        self._resources[key] = dict(content)
        versions.append(version)
        previous = self._audit[-1].receipt_hash if self._audit else ""
        self._audit.append(
            AuditEvent.receipt(
                organization_id=organization_id,
                actor_principal_id=actor_principal_id,
                action="resource.version.created",
                resource_type=resource_type,
                resource_id=resource_id,
                previous_hash=previous,
                payload=json.dumps(asdict(version), sort_keys=True, default=str),
            )
        )
        return version

    def export_resource(
        self, organization_id: str, resource_type: str, resource_id: str
    ) -> dict[str, Any]:
        key = (organization_id, resource_type, resource_id)
        return {
            "resource": sanitize_shared_resource(self._resources[key]),
            "versions": [asdict(version) for version in self._versions.get(key, [])],
            "audit_events": [
                asdict(event)
                for event in self._audit
                if event.organization_id == organization_id
                and event.resource_type == resource_type
                and event.resource_id == resource_id
            ],
        }

    def delete_resource(
        self,
        organization_id: str,
        resource_type: str,
        resource_id: str,
        *,
        remove_search_index: Callable[[str, str, str], None],
        remove_derived_previews: Callable[[str, str, str], None],
    ) -> None:
        key = (organization_id, resource_type, resource_id)
        self._resources.pop(key, None)
        self._versions.pop(key, None)
        remove_search_index(*key)
        remove_derived_previews(*key)

    def delete_tenant(
        self,
        organization_id: str,
        *,
        remove_search_index: Callable[[str, str, str], None],
        remove_derived_previews: Callable[[str, str, str], None],
    ) -> None:
        keys = [key for key in self._resources if key[0] == organization_id]
        for key in keys:
            self.delete_resource(
                *key,
                remove_search_index=remove_search_index,
                remove_derived_previews=remove_derived_previews,
            )
