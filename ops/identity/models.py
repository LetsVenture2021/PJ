"""Deliberately small identity models; authentication remains an edge concern."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TenantRole(StrEnum):
    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"


@dataclass(frozen=True)
class Principal:
    external_subject: str
    email: str
    id: str = field(default_factory=lambda: str(uuid4()))
    disabled_at: datetime | None = None


@dataclass(frozen=True)
class Organization:
    name: str
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class Membership:
    principal_id: str
    organization_id: str
    role: TenantRole = TenantRole.MEMBER
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=utcnow)
    revoked_at: datetime | None = None

    def active(self, at: datetime | None = None) -> bool:
        at = at or utcnow()
        return self.revoked_at is None and self.created_at <= at


@dataclass(frozen=True)
class Invitation:
    organization_id: str
    email: str
    token_hash: str
    expires_at: datetime
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=utcnow)
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None

    def redeemable(self, at: datetime | None = None) -> bool:
        at = at or utcnow()
        return self.accepted_at is None and self.revoked_at is None and at < self.expires_at
