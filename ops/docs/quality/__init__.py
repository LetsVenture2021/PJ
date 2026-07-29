"""Pure, deterministic document-quality validation.

Validators accept text, a profile, and metadata and perform no filesystem,
database, network, logging, or mutation operations.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable

from . import (
    accessibility,
    artifact_structure,
    claims_citations,
    cross_document_consistency,
    lifecycle_approval,
    link_integrity,
    placeholders,
    prose_style,
    schema_metadata,
    security_privacy,
    template_structure,
)
from .models import (
    ControlResult,
    QualityFinding,
    QualityProfile,
    QualityReport,
    Severity,
    VALIDATOR_VERSION,
)

_VALIDATORS = (
    ("schema_metadata", schema_metadata.validate),
    ("template_structure", template_structure.validate),
    ("placeholders", placeholders.validate),
    ("prose_style", prose_style.validate),
    ("claims_citations", claims_citations.validate),
    ("link_integrity", link_integrity.validate),
    ("security_privacy", security_privacy.validate),
    ("accessibility", accessibility.validate),
    ("artifact_structure", artifact_structure.validate),
    ("lifecycle_approval", lifecycle_approval.validate),
    ("cross_document_consistency", cross_document_consistency.validate),
)


def validate_document(
    text: str, profile: QualityProfile | dict | str | None = None, metadata: dict | None = None
) -> QualityReport:
    """Return a deterministic report for one document (apart from its timestamp)."""
    selected = QualityProfile.from_value(profile)
    context = dict(metadata or {})
    findings: list[QualityFinding] = []
    controls = {}
    for name, validator in _VALIDATORS:
        current = validator(text, selected, context)
        findings.extend(current)
        controls[name] = (
            ControlResult.FAIL
            if any(
                f.severity in {Severity.BLOCKER, Severity.CRITICAL, Severity.MAJOR} for f in current
            )
            else (ControlResult.REVIEW if current else ControlResult.PASS)
        )
    source_hash = hashlib.sha256(text.encode()).hexdigest()
    payload = {
        "source_hash": source_hash,
        "profile": selected.digest_payload(),
        "findings": [f.to_dict() for f in findings],
        "controls": controls,
        "validator_version": VALIDATOR_VERSION,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()
    return QualityReport(
        source_hash,
        selected,
        tuple(findings),
        controls,
        datetime.now(timezone.utc).isoformat(),
        VALIDATOR_VERSION,
        digest,
    )


def validate_library(
    documents: Iterable[str] | dict[str, str],
    profile: QualityProfile | dict | str | None = None,
    metadata: dict | None = None,
) -> dict[str, QualityReport]:
    items = (
        documents.items()
        if isinstance(documents, dict)
        else ((str(index), text) for index, text in enumerate(documents))
    )
    return {name: validate_document(text, profile, metadata) for name, text in items}


__all__ = [
    "ControlResult",
    "QualityFinding",
    "QualityProfile",
    "QualityReport",
    "Severity",
    "VALIDATOR_VERSION",
    "validate_document",
    "validate_library",
]
