"""Tenant governance primitives for PJ.

All stateful APIs in this package require an explicit :class:`TenantContext`.
"""

from .models import AdminRole, GovernedResource, TenantContext
from .repository import TenantRepository
from .service import GovernanceService

__all__ = [
    "AdminRole",
    "GovernedResource",
    "GovernanceService",
    "TenantContext",
    "TenantRepository",
]
