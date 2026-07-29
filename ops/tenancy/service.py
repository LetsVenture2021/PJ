"""Authorization, federation, and lifecycle orchestration for tenants."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .models import (
    AdminRole,
    FederationRequest,
    OIDCAdapter,
    SAMLAdapter,
    SCIMAdapter,
    TenantContext,
    TenantDataBackend,
    ValidatedIdentity,
)
from .repository import TenantRepository

_ROLE_ACTIONS = {
    AdminRole.OWNER: frozenset(
        {
            "manage_roles",
            "manage_policy",
            "export",
            "delete_tenant",
            "rotate_keys",
            "audit",
            "manage_connectors",
        }
    ),
    AdminRole.ADMINISTRATOR: frozenset(
        {"manage_roles", "manage_policy", "export", "audit", "manage_connectors"}
    ),
    AdminRole.MEMBER: frozenset(),
    AdminRole.AUDITOR: frozenset({"audit"}),
    AdminRole.CONNECTOR_ADMINISTRATOR: frozenset({"manage_connectors"}),
}
_SENSITIVE = frozenset({"manage_policy", "export", "delete_tenant", "rotate_keys"})
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class GovernanceService:
    def __init__(
        self,
        repository: TenantRepository,
        data_root: Path,
        backends: Sequence[TenantDataBackend] = (),
    ):
        self.repository = repository
        self.data_root = data_root.resolve()
        self.backends = tuple(backends)

    def tenant_path(self, context: TenantContext, category: str, *parts: str) -> Path:
        """Return a tenant-namespaced path; no unscoped path API exists."""
        components = (context.tenant_id, category, *parts)
        if any(not _SAFE_COMPONENT.fullmatch(part) for part in components):
            raise ValueError("unsafe tenant path component")
        path = self.data_root.joinpath(*components).resolve()
        path.relative_to(self.data_root)
        return path

    def authorize(
        self, context: TenantContext, action: str, *, reauthenticated_at: datetime | None = None
    ) -> None:
        allowed = any(
            action in _ROLE_ACTIONS[role]
            for role in self.repository.roles(context, context.actor_id)
        )
        if not allowed:
            raise PermissionError("role does not permit action")
        if action in _SENSITIVE:
            now = datetime.now(timezone.utc)
            if reauthenticated_at is None or now - reauthenticated_at.astimezone(
                timezone.utc
            ) > timedelta(minutes=5):
                raise PermissionError("recent reauthentication required")

    def authenticate_oidc(
        self, adapter: OIDCAdapter, token: str, request: FederationRequest
    ) -> ValidatedIdentity:
        identity = adapter.validate_id_token(token, request)
        self._check_federated_identity(identity, request)
        return identity

    def authenticate_saml(
        self, adapter: SAMLAdapter, response: str, request: FederationRequest
    ) -> ValidatedIdentity:
        identity = adapter.validate_response(response, request)
        self._check_federated_identity(identity, request)
        return identity

    @staticmethod
    def _check_federated_identity(identity: ValidatedIdentity, request: FederationRequest) -> None:
        # Signature, nonce/state, expiry and clock-skew validation are adapter obligations;
        # matching again here prevents a confused-deputy adapter configuration.
        if (
            identity.issuer != request.expected_issuer
            or identity.audience != request.expected_audience
        ):
            raise PermissionError("federated issuer or audience mismatch")
        if not request.nonce or not request.state or not 0 <= request.clock_skew_seconds <= 300:
            raise PermissionError("invalid federation replay controls")

    def provision_scim(
        self, context: TenantContext, adapter: SCIMAdapter, payload: Mapping[str, object]
    ) -> None:
        user = adapter.parse_user(payload)
        self.repository.put_user(context, user.external_id, user.email, active=user.active)
        if not user.active:
            # Deprovisioning revokes both administrative roles and resource grants.
            self.repository.set_roles(context, user.external_id, set())
            self.repository.replace_grants(context, user.external_id, set())
        self.repository.audit(
            context,
            resource_type="user",
            resource_id=user.external_id,
            action="scim.provision",
            policy_decision="allow",
            status="active" if user.active else "deprovisioned",
        )

    def export_tenant(
        self, context: TenantContext, destination: Path, *, reauthenticated_at: datetime
    ) -> dict[str, object]:
        self.authorize(context, "export", reauthenticated_at=reauthenticated_at)
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(destination)
        staging = Path(tempfile.mkdtemp(prefix="tenant-export-", dir=destination.parent))
        try:
            files: list[Path] = []
            metadata = {
                "tenant_id": context.tenant_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "sqlite_counts": self.repository.tenant_table_counts(context),
                "backends": [backend.name for backend in self.backends],
            }
            manifest_file = staging / "metadata.json"
            manifest_file.write_text(json.dumps(metadata, sort_keys=True, indent=2))
            files.append(manifest_file)
            for backend in self.backends:
                backend_dir = staging / backend.name
                backend_dir.mkdir()
                files.extend(backend.export(context, backend_dir))
            manifest = {
                str(path.relative_to(staging)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in files
            }
            (staging / "manifest.sha256.json").write_text(
                json.dumps(manifest, sort_keys=True, indent=2)
            )
            os.replace(staging, destination)
            return {"destination": str(destination), "manifest": manifest}
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def delete_tenant(
        self, context: TenantContext, *, reauthenticated_at: datetime
    ) -> dict[str, bool]:
        """Delete all registered stores and verify each; backups honor backend guarantees."""
        self.authorize(context, "delete_tenant", reauthenticated_at=reauthenticated_at)
        results: dict[str, bool] = {}
        for backend in self.backends:
            backend.delete(context)
            results[backend.name] = backend.verify_deleted(context)
        tenant_dir = self.tenant_path(context, "state").parent
        if tenant_dir.exists():
            shutil.rmtree(tenant_dir)
        results["filesystem"] = not tenant_dir.exists()
        if not all(results.values()):
            raise RuntimeError("tenant deletion verification failed")
        return results

    @staticmethod
    def residency_statement(policy: Mapping[str, object]) -> str:
        regional = policy.get("regional_controls")
        if not isinstance(regional, Mapping) or not regional.get("enforced_local_region"):
            return "No data-residency guarantee. Provider and connector routing require separate support."
        return "Local storage region enforced; provider and connector residency is not guaranteed unless separately attested."
