"""Health evaluation and safe, metadata-only serialization."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .credentials import CredentialProvider
from .models import ConnectorHealth, CredentialRecord, HealthReason, utc_now


def evaluate_health(
    record: CredentialRecord,
    provider: CredentialProvider,
    required_scopes: set[str],
    *,
    now: datetime | None = None,
) -> ConnectorHealth:
    now = now or utc_now()
    reason = HealthReason.HEALTHY
    remediation = ""
    if not provider.exists(record.credential_handle):
        reason, remediation = HealthReason.MISSING_SECRET, "Reconnect this account."
    elif record.expires_at is not None and record.expires_at <= now:
        reason, remediation = HealthReason.EXPIRED, "Reconnect or refresh authorization."
    elif not required_scopes.issubset(record.granted_scopes):
        reason, remediation = HealthReason.INSUFFICIENT_SCOPE, "Grant the listed required scopes."
    healthy = reason is HealthReason.HEALTHY
    return ConnectorHealth(
        record.connector_id,
        record.credential_handle,
        healthy,
        reason,
        record.granted_scopes,
        record.expires_at,
        now if healthy else None,
        None if healthy else now,
        remediation,
    )


def health_to_public_dict(health: ConnectorHealth) -> dict[str, Any]:
    """Return customer-safe metadata, never provider response bodies."""
    return {
        "connector_id": health.connector_id,
        "healthy": health.healthy,
        "reason_code": health.reason_code.value,
        "granted_scopes": list(health.granted_scopes),
        "expiry": health.expiry.isoformat() if health.expiry else None,
        "last_successful_probe": health.last_successful_probe.isoformat()
        if health.last_successful_probe
        else None,
        "last_failure": health.last_failure.isoformat() if health.last_failure else None,
        "remediation": health.remediation,
    }
