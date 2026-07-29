"""Deterministic, content-safe document security scanning.

Findings deliberately contain no captured text.  A location, occurrence count,
and one-way fingerprint are sufficient to remediate and correlate a finding
without copying sensitive source material into reports or logs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Pattern

from ops.shared.logging import redact_sensitive


@dataclass(frozen=True, slots=True)
class _Rule:
    rule_id: str
    expression: Pattern[str]


def _compile(expression: str, flags: int = 0) -> Pattern[str]:
    return re.compile(expression, flags)


RULES = (
    _Rule(
        "SEC001_COMMON_SECRET",
        _compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,})\b"
        ),
    ),
    _Rule("SEC002_PRIVATE_KEY", _compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    _Rule(
        "SEC003_AUTHORIZATION",
        _compile(r"\b(?:Authorization\s*:\s*)?(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.I),
    ),
    _Rule(
        "SEC004_CONNECTION_STRING",
        _compile(
            r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?|mssql)://[^\s'\"<>]+", re.I
        ),
    ),
    _Rule(
        "SEC005_CREDENTIAL",
        _compile(
            r"\b(?:api[_ -]?key|client[_ -]?secret|password|passwd|access[_ -]?token)\b\s*[:=]\s*[^\s,;]{6,}",
            re.I,
        ),
    ),
    _Rule(
        "SEC006_LOCAL_PATH",
        _compile(
            r"(?<![\w/])(?:/Users/[^\s:'\"<>]+|/home/[^\s:'\"<>]+|[A-Za-z]:\\(?:Users|Documents and Settings)\\[^\r\n'\"<>]+|\\\\[^\\\s]+\\[^\r\n'\"<>]+)"
        ),
    ),
    _Rule(
        "SEC007_EMAIL",
        _compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I),
    ),
    _Rule("SEC008_PHONE", _compile(r"(?<!\w)(?:\+?\d[\d .()-]{7,}\d)(?!\w)")),
    _Rule("SEC009_GOVERNMENT_ID", _compile(r"(?<!\d)(?:\d{3}-\d{2}-\d{4}|\d{2}-\d{7})(?!\d)")),
    _Rule("SEC010_PAYMENT_CARD", _compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")),
    _Rule("SEC011_INVISIBLE_CONTROL", _compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")),
)

CLASSIFICATION_LEVELS = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}
AUDIENCE_LEVELS = {
    "public": 0,
    "unrestricted": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


def validate_audience(classification: str, audience: str) -> dict:
    """Require known, compatible classification and audience labels."""
    normalized_classification = str(classification or "").strip().lower()
    normalized_audience = str(audience or "").strip().lower()
    if normalized_classification not in CLASSIFICATION_LEVELS:
        return {"status": "blocked", "reason": "explicit valid classification required"}
    if normalized_audience not in AUDIENCE_LEVELS:
        return {"status": "blocked", "reason": "explicit valid audience required"}
    if AUDIENCE_LEVELS[normalized_audience] < CLASSIFICATION_LEVELS[normalized_classification]:
        return {"status": "blocked", "reason": "classification is incompatible with audience"}
    return {
        "status": "passed",
        "classification": normalized_classification,
        "audience": normalized_audience,
    }


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def scan_text(text: str) -> dict:
    """Scan text and return metadata-only findings in stable source order."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    findings: list[dict] = []
    counts: dict[str, int] = {}
    for rule in RULES:
        matches = list(rule.expression.finditer(text))
        counts[rule.rule_id] = len(matches)
        for match in matches:
            start = match.start()
            line = text.count("\n", 0, start) + 1
            previous_newline = text.rfind("\n", 0, start)
            findings.append(
                {
                    "rule_id": rule.rule_id,
                    "location": {
                        "line": line,
                        "column": start - previous_newline,
                        "offset": start,
                    },
                    "count": 1,
                    "fingerprint": _fingerprint(match.group(0)),
                }
            )
    findings.sort(key=lambda item: (item["location"]["offset"], item["rule_id"]))
    report = {
        "status": "blocked" if findings else "passed",
        "finding_count": len(findings),
        "rule_counts": {key: value for key, value in counts.items() if value},
        "findings": findings,
    }
    # Defense in depth: every metadata field crosses the shared recursive
    # formatter even though the schema above never includes captured content.
    return redact_sensitive(report)


def scan_bytes(payload: bytes, *, encoding: str = "utf-8") -> dict:
    """Decode bounded textual bytes strictly; never include decode input in errors."""
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    try:
        text = payload.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return {"status": "skipped", "finding_count": 0, "rule_counts": {}, "findings": []}
    return scan_text(text)
