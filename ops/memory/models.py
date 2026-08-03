"""Public memory-domain values and validation."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

STATUSES = {"proposed", "accepted", "rejected", "superseded", "deleted"}
CONFIDENCE_BANDS = {"low", "medium", "high"}
SOURCE_AUTHORITIES = {"owner", "assistant", "tool", "untrusted"}
SENSITIVE_CATEGORIES = {
    "credential",
    "health",
    "financial",
    "authentication",
    "protected_characteristic",
}
_CREDENTIAL = re.compile(
    r"(?i)(?:api[_ -]?key|password|secret|bearer|token)\s*[:=]\s*\S+|sk-[A-Za-z0-9_-]{12,}"
)
_INSTRUCTION = re.compile(
    r"(?im)^\s*(?:system|assistant|developer)\s*:|ignore (?:all |previous )?instructions"
)


class ProposalError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def normalize_proposal(value: Any, *, source_ref: str, project_scope: str) -> dict[str, Any]:
    """Strictly validate provider output and strip content-borne instructions."""
    required = {"content", "category", "confidence", "source_type"}
    if not isinstance(value, dict) or set(value) != required:
        raise ProposalError("proposal has unexpected fields")
    if any(not isinstance(value[k], str) for k in required):
        raise ProposalError("proposal fields must be strings")
    text = " ".join(value["content"].split()).strip()
    if not text or len(text) > 500 or _INSTRUCTION.search(text):
        raise ProposalError("unsafe proposal content")
    if _CREDENTIAL.search(text):
        raise ProposalError("credential-shaped content")
    category = value["category"].strip().lower()
    confidence = value["confidence"].strip().lower()
    authority = value["source_type"].strip().lower()
    if confidence not in CONFIDENCE_BANDS or authority not in SOURCE_AUTHORITIES:
        raise ProposalError("invalid confidence or source authority")
    return {
        "content": text,
        "category": category,
        "confidence": confidence,
        "source_type": authority,
        "source_ref": source_ref,
        "project_scope": project_scope,
        "content_hash": content_hash(text),
    }
