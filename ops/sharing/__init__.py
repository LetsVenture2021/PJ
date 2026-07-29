"""Explicit resource sharing, authorization, and safe collaboration records."""

from .authorization import AuthorizationContext, AuthorizationError, authorize
from .models import (
    ApprovalRoute,
    AuditEvent,
    Comment,
    Mention,
    Permission,
    ResourceGrant,
    ResourceVersion,
    ShareLink,
)
from .sanitization import sanitize_shared_resource

__all__ = [
    "ApprovalRoute",
    "AuditEvent",
    "AuthorizationContext",
    "AuthorizationError",
    "Comment",
    "Mention",
    "Permission",
    "ResourceGrant",
    "ResourceVersion",
    "ShareLink",
    "authorize",
    "sanitize_shared_resource",
]
