"""Initial connector catalog. Writes remain narrowly scoped and approval-gated."""

from __future__ import annotations

from .models import (
    ActionSchema,
    ConnectorManifest,
    RateLimit,
    Reversibility,
    RiskClass,
    SecretRequirement,
)


_OBJECT = {"type": "object", "additionalProperties": False}


def _manifest(
    connector_id: str, name: str, scopes: tuple[str, ...], actions: tuple[ActionSchema, ...]
) -> ConnectorManifest:
    return ConnectorManifest(
        "1.0",
        connector_id,
        name,
        "https",
        ("search",) + (("draft",) if actions else ()),
        scopes,
        actions,
        "account_metadata",
        (RateLimit(60, 60),),
        (SecretRequirement("oauth_client", "OAuth client configuration"),),
    )


def builtin_manifests() -> tuple[ConnectorManifest, ...]:
    draft = ActionSchema(
        "create_draft",
        {**_OBJECT, "required": ["content"], "properties": {"content": {"type": "string"}}},
        RiskClass.LOW_WRITE,
        Reversibility.REVERSIBLE,
        True,
    )
    issue = ActionSchema(
        "create_issue",
        {**_OBJECT, "required": ["title"], "properties": {"title": {"type": "string"}}},
        RiskClass.LOW_WRITE,
        Reversibility.NONE,
        True,
    )
    note = ActionSchema(
        "create_note",
        {
            **_OBJECT,
            "required": ["title", "body"],
            "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
        },
        RiskClass.LOW_WRITE,
        Reversibility.REVERSIBLE,
        True,
    )
    return (
        _manifest("calendar", "Calendar", ("calendar.read",), ()),
        _manifest("email", "Email", ("mail.read", "drafts.write"), (draft,)),
        _manifest("cloud_drive", "Cloud drive", ("files.metadata.read",), ()),
        _manifest("team_chat", "Team chat", ("messages.read",), ()),
        _manifest("issue_tracking", "Issue tracking", ("issues.read", "issues.write"), (issue,)),
        _manifest("notes", "Notes", ("notes.read", "notes.write"), (note,)),
    )
