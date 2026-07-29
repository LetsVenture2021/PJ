"""Metadata-safe security and privacy controls."""

import re

from .common import finding
from .models import QualityProfile, Severity


_SENSITIVE = (
    ("QLT-SEC-001", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "private key"),
    ("QLT-SEC-002", r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+\S+", "authorization header"),
    (
        "QLT-SEC-003",
        r"(?i)\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*['\"]?[^\s'\"]{8,}",
        "credential-like assignment",
    ),
    (
        "QLT-SEC-004",
        r"(?i)\b(?:\.env|id_rsa|credentials\.json|service-account\.json)\b",
        "credential-shaped filename",
    ),
    ("QLT-PRIV-001", r"\b\d{3}-\d{2}-\d{4}\b", "possible government identifier"),
    ("QLT-PRIV-002", r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "email address"),
)


def validate(text: str, profile: QualityProfile, metadata: dict) -> list:
    result = []
    for rule, pattern, label in _SENSITIVE:
        for match in re.finditer(pattern, text, re.I):
            result.append(
                finding(
                    rule,
                    Severity.CRITICAL,
                    f"Document contains a {label}.",
                    text,
                    match,
                    "Remove or redact the sensitive value and rotate credentials when applicable.",
                    evidence=f"redacted {label} at {match.start()}",
                    waiver=False,
                )
            )
    for path in profile.prohibited_internal_paths:
        for match in re.finditer(re.escape(path), text, re.I):
            result.append(
                finding(
                    "QLT-SEC-005",
                    Severity.CRITICAL,
                    "Prohibited internal path is disclosed.",
                    text,
                    match,
                    "Replace the path with a safe logical reference.",
                    evidence="redacted internal path",
                    waiver=False,
                )
            )
    return result
