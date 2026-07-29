"""Provider-neutral tenant, authorization, and identity contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping, Protocol, Sequence


class AdminRole(StrEnum):
    OWNER = "owner"
    ADMINISTRATOR = "administrator"
    MEMBER = "member"
    AUDITOR = "auditor"
    CONNECTOR_ADMINISTRATOR = "connector_administrator"


class GovernedResource(StrEnum):
    CHATS = "chats"
    UPLOADS = "uploads"
    MEMORIES = "memories"
    ARTIFACTS = "artifacts"
    JOB_PAYLOADS = "job_payloads"
    AUDIT_EVENTS = "audit_events"
    BACKUPS = "backups"


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Required, validated scope passed to every tenant repository operation."""

    tenant_id: str
    actor_id: str
    request_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("tenant_id", self.tenant_id),
            ("actor_id", self.actor_id),
            ("request_id", self.request_id),
        ):
            if (
                not value
                or len(value) > 128
                or not value.replace("-", "").replace("_", "").isalnum()
            ):
                raise ValueError(f"invalid {name}")


@dataclass(frozen=True, slots=True)
class ValidatedIdentity:
    subject: str
    issuer: str
    audience: str
    email: str
    groups: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FederationRequest:
    expected_issuer: str
    expected_audience: str
    state: str
    nonce: str
    now_epoch: int
    clock_skew_seconds: int = 60


class OIDCAdapter(Protocol):
    """Adapter must cryptographically validate the ID token before returning."""

    def validate_id_token(self, token: str, request: FederationRequest) -> ValidatedIdentity: ...


class SAMLAdapter(Protocol):
    """Adapter must validate XML signatures and assertion conditions."""

    def validate_response(self, response: str, request: FederationRequest) -> ValidatedIdentity: ...


@dataclass(frozen=True, slots=True)
class SCIMUser:
    external_id: str
    active: bool
    email: str
    groups: tuple[str, ...] = ()


class SCIMAdapter(Protocol):
    def parse_user(self, payload: Mapping[str, object]) -> SCIMUser: ...


class TenantDataBackend(Protocol):
    """A tenant-aware backend participating in export and erasure."""

    name: str

    def export(self, context: TenantContext, destination: Path) -> Sequence[Path]: ...
    def delete(self, context: TenantContext) -> int: ...
    def verify_deleted(self, context: TenantContext) -> bool: ...
