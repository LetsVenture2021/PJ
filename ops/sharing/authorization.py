"""Server-side, exact-resource authorization without implicit inheritance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import Permission, ResourceGrant, ShareLink


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class AuthorizationContext:
    principal_id: str | None
    organization_id: str
    resource_type: str
    resource_id: str
    required: Permission
    now: datetime | None = None
    share_token: str | None = None
    connector_action: bool = False
    memory_access: bool = False


def authorize(
    context: AuthorizationContext,
    *,
    grants: list[ResourceGrant] | tuple[ResourceGrant, ...] = (),
    links: list[ShareLink] | tuple[ShareLink, ...] = (),
) -> None:
    """Authorize only an exact tenant/resource match; deny by default."""
    for grant in grants:
        if (
            context.principal_id is not None
            and grant.principal_id == context.principal_id
            and grant.organization_id == context.organization_id
            and grant.resource_type == context.resource_type
            and grant.resource_id == context.resource_id
            and context.required <= grant.permission
            and grant.active(context.now)
        ):
            return
    if context.share_token and not (context.connector_action or context.memory_access):
        for link in links:
            if (
                link.organization_id == context.organization_id
                and link.resource_type == context.resource_type
                and link.resource_id == context.resource_id
                and link.permits(context.share_token, context.required, context.now)
            ):
                return
    raise AuthorizationError("access to this exact tenant resource is denied")
