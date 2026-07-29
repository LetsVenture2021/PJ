"""Collaboration records with explicit scope, expiry, revocation, and attribution."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any
from uuid import uuid4


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Permission(IntEnum):
    VIEWER = 1
    COMMENTER = 2
    EDITOR = 3
    APPROVER = 4


@dataclass(frozen=True)
class ResourceGrant:
    organization_id: str
    resource_type: str
    resource_id: str
    principal_id: str
    permission: Permission
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=utcnow)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    def active(self, at: datetime | None = None) -> bool:
        at = at or utcnow()
        return self.revoked_at is None and (self.expires_at is None or at < self.expires_at)


@dataclass(frozen=True)
class ShareLink:
    organization_id: str
    resource_type: str
    resource_id: str
    permission: Permission
    token_hash: str
    expires_at: datetime
    id: str = field(default_factory=lambda: str(uuid4()))
    revoked_at: datetime | None = None

    @classmethod
    def issue(cls, **fields: Any) -> tuple[ShareLink, str]:
        token = secrets.token_urlsafe(32)
        return cls(token_hash=cls.hash_token(token), **fields), token

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def permits(self, token: str, permission: Permission, at: datetime | None = None) -> bool:
        at = at or utcnow()
        return (
            self.revoked_at is None
            and at < self.expires_at
            and secrets.compare_digest(self.token_hash, self.hash_token(token))
            and permission <= self.permission
            and permission <= Permission.COMMENTER
        )


@dataclass(frozen=True)
class Comment:
    resource_type: str
    resource_id: str
    version_id: str
    author_principal_id: str
    body: str
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=utcnow)
    deleted_at: datetime | None = None


@dataclass(frozen=True)
class Mention:
    comment_id: str
    principal_id: str
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class ResourceVersion:
    resource_type: str
    resource_id: str
    version: int
    content_hash: str
    author_principal_id: str
    parent_version_id: str | None
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class ApprovalRoute:
    resource_type: str
    resource_id: str
    version_id: str
    approver_principal_id: str
    id: str = field(default_factory=lambda: str(uuid4()))
    decision: str | None = None
    decided_at: datetime | None = None


@dataclass(frozen=True)
class AuditEvent:
    organization_id: str
    actor_principal_id: str
    action: str
    resource_type: str
    resource_id: str
    receipt_hash: str
    id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=utcnow)

    @classmethod
    def receipt(cls, *, previous_hash: str = "", payload: str, **fields: Any) -> AuditEvent:
        digest = hashlib.sha256(f"{previous_hash}\n{payload}".encode()).hexdigest()
        return cls(receipt_hash=digest, **fields)
