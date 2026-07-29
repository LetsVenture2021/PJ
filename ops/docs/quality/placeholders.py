"""Placeholder and drafting-residue controls."""

import re

from .common import finding
from .models import QualityProfile, Severity


_RULES = (
    ("QLT-PLACEHOLDER-001", r"(?i)(?<![\w-])(TBD|TODO|FIXME|XXX)(?![\w-])", "drafting token"),
    (
        "QLT-PLACEHOLDER-002",
        r"\{\{[^{}]+\}\}|\{%[^%]+%\}|\$\{[^}]+\}",
        "unexpanded template variable",
    ),
    ("QLT-PLACEHOLDER-003", r"(?i)\b(?:example\.(?:com|org|net)|test\.invalid)\b", "dummy domain"),
    (
        "QLT-PLACEHOLDER-004",
        r"(?<!\d)(?:000[- .]?000[- .]?0000|555[- .]?01\d{2})(?!\d)",
        "dummy number",
    ),
    ("QLT-PLACEHOLDER-005", r"(?i)\blorem\s+ipsum\b", "dummy prose"),
    ("QLT-PLACEHOLDER-006", r"\[[^\]]*\]\(\s*\)", "empty Markdown link"),
    (
        "QLT-PLACEHOLDER-007",
        r"(?i)\[(?:verify|check|confirm)(?:\s+current)?\]",
        "vague verification label",
    ),
)


def validate(text: str, profile: QualityProfile, metadata: dict) -> list:
    findings = []
    for rule, pattern, label in _RULES:
        for match in re.finditer(pattern, text):
            findings.append(
                finding(
                    rule,
                    Severity.BLOCKER,
                    f"Unresolved {label}.",
                    text,
                    match,
                    "Replace it with reviewed content or a precise provenance record.",
                    waiver=False,
                )
            )
    return findings
