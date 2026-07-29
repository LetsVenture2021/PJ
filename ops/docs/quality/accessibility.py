"""Markdown accessibility controls."""

import re

from .models import QualityFinding, QualityProfile, Severity


def validate(text: str, profile: QualityProfile, metadata: dict) -> list:
    result = []
    for match in re.finditer(r"!\[([^]]*)\]\([^)]+\)", text):
        if not match.group(1).strip():
            result.append(
                QualityFinding(
                    "QLT-A11Y-001",
                    Severity.MAJOR,
                    "Image has empty alternative text.",
                    f"line {text.count(chr(10), 0, match.start()) + 1}",
                    "empty alt text",
                    "Add concise alternative text or explicitly mark the image decorative.",
                )
            )
    if re.search(r"(?i)\b(?:click here|link here|read more)\b", text):
        result.append(
            QualityFinding(
                "QLT-A11Y-002",
                Severity.MINOR,
                "Link wording may not describe its destination.",
                "document",
                "generic link label",
                "Use descriptive link text.",
            )
        )
    return result
