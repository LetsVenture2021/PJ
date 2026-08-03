"""Claim provenance controls."""

import re

from .models import QualityFinding, QualityProfile, Severity


_CLAIM = re.compile(
    r"(?i)(?:[$£€]\s*\d|\b\d+(?:\.\d+)?(?:%|\s+(?:users?|days?|hours?|million|billion))\b|\b(?:required by law|legally required|compliant|secure|currently|as of|supports?|does not)\b)"
)
_SOURCE = re.compile(
    r"(?i)(?:https?://\S+|\[[^]]+\]\([^)]+\)|\[(?:\^?\d+|[A-Za-z][\w-]*)\]|sources?\s*:|provenance\s*:\s*internal-observation)"
)


def validate(text: str, profile: QualityProfile, metadata: dict) -> list:
    result = []
    offset = 0
    for paragraph in re.split(r"(\n\s*\n)", text):
        if _CLAIM.search(paragraph) and not _SOURCE.search(paragraph):
            line = text.count("\n", 0, offset) + 1
            result.append(
                QualityFinding(
                    "QLT-CLAIM-001",
                    Severity.MAJOR,
                    "Factual, numeric, legal, security, date, or product-behavior claim lacks provenance.",
                    f"line {line}",
                    "unsupported claim category (content omitted)",
                    "Add a source reference or `provenance: internal-observation` record.",
                )
            )
        offset += len(paragraph)
    return result
