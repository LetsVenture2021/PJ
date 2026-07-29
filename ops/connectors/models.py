"""Versioned connector data contracts.  Models intentionally contain no secret values."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RiskClass(str, Enum):
    READ = "read"
    LOW_WRITE = "low_write"
    HIGH_WRITE = "high_write"


class Reversibility(str, Enum):
    NONE = "none"
    COMPENSATABLE = "compensatable"
    REVERSIBLE = "reversible"


@dataclass(frozen=True)
class RateLimit:
    requests: int
    per_seconds: int


@dataclass(frozen=True)
class SecretRequirement:
    name: str
    purpose: str
    required: bool = True


@dataclass(frozen=True)
class ActionSchema:
    name: str
    input_schema: Mapping[str, Any]
    risk_class: RiskClass
    reversibility: Reversibility = Reversibility.NONE
    supports_idempotency: bool = False


@dataclass(frozen=True)
class ConnectorManifest:
    schema_version: str
    connector_id: str
    display_name: str
    transport: str
    capabilities: tuple[str, ...]
    data_scopes: tuple[str, ...]
    actions: tuple[ActionSchema, ...]
    health_probe: str
    rate_limits: tuple[RateLimit, ...]
    secret_requirements: tuple[SecretRequirement, ...]

    def action(self, name: str) -> ActionSchema:
        return next(action for action in self.actions if action.name == name)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CredentialRecord:
    credential_handle: str
    connector_id: str
    account_id: str
    account_label: str
    granted_scopes: tuple[str, ...]
    expires_at: datetime | None = None


class HealthReason(str, Enum):
    HEALTHY = "healthy"
    MISSING_SECRET = "missing_secret"
    EXPIRED = "expired"
    REVOKED = "revoked"
    INSUFFICIENT_SCOPE = "insufficient_scope"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConnectorHealth:
    connector_id: str
    credential_handle: str
    healthy: bool
    reason_code: HealthReason
    granted_scopes: tuple[str, ...]
    expiry: datetime | None
    last_successful_probe: datetime | None = None
    last_failure: datetime | None = None
    remediation: str = ""


@dataclass(frozen=True)
class AuthorizationState:
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class ActionPreview:
    connector_id: str
    action: str
    summary: str
    changes: tuple[str, ...]
    estimated_cost: float | None
    currency: str | None
    approval_required: bool


@dataclass(frozen=True)
class ActionReceipt:
    receipt_id: str
    connector_id: str
    action: str
    idempotency_key: str
    status: str
    external_reference: str | None
    verified: bool
    occurred_at: datetime = field(default_factory=utc_now)
