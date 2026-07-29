"""Customer-facing connector settings view model and HTML renderer."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from .models import ConnectorHealth, ConnectorManifest, CredentialRecord


@dataclass(frozen=True)
class ConnectorSettings:
    manifest: ConnectorManifest
    credential: CredentialRecord
    health: ConnectorHealth
    last_used: str | None


def render_settings(items: tuple[ConnectorSettings, ...]) -> str:
    cards = []
    for item in items:
        status = "Connected" if item.health.healthy else "Needs attention"
        capabilities = ", ".join(item.manifest.capabilities)
        scopes = ", ".join(item.credential.granted_scopes)
        cards.append(
            f"""<article class="connector"><h2>{escape(item.manifest.display_name)}</h2><p><strong>{escape(item.credential.account_label)}</strong></p><dl><dt>Status</dt><dd>{status}</dd><dt>Capabilities</dt><dd>{escape(capabilities)}</dd><dt>Access</dt><dd>{escape(scopes)}</dd><dt>Last use</dt><dd>{escape(item.last_used or "Never")}</dd></dl><form method="post"><button name="action" value="reconnect">Reconnect</button><button name="action" value="revoke">Revoke</button></form></article>"""
        )
    return (
        "<!doctype html><html><head><title>Connections</title></head><body><main><h1>Connections</h1>"
        + "".join(cards)
        + "</main></body></html>"
    )
