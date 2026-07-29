"""Markdown and template structure controls."""

import re
from collections import Counter

from .common import finding, headings
from .models import QualityFinding, QualityProfile, Severity


def validate(text: str, profile: QualityProfile, metadata: dict) -> list:
    result = []
    hs = headings(text)
    anchor_headings = (
        hs if metadata.get("artifact_type") != "presentation" else [h for h in hs if h[0] <= 2]
    )
    counts = Counter(title.casefold() for _, title, _ in anchor_headings)
    for level, title, offset in hs:
        if (level, title, offset) in anchor_headings and counts[title.casefold()] > 1:
            result.append(
                QualityFinding(
                    "QLT-STRUCT-001",
                    Severity.MAJOR,
                    f"Duplicate heading: {title}",
                    f"line {text.count(chr(10), 0, offset) + 1}",
                    title,
                    "Rename or consolidate duplicate sections.",
                )
            )
    for previous, current in zip(hs, hs[1:]):
        if current[0] > previous[0] + 1:
            result.append(
                QualityFinding(
                    "QLT-STRUCT-002",
                    Severity.MAJOR,
                    "Heading level is skipped.",
                    f"line {text.count(chr(10), 0, current[2]) + 1}",
                    f"H{previous[0]} to H{current[0]}",
                    "Use sequential heading levels.",
                )
            )
    present = {title.casefold() for _, title, _ in hs}
    for required in profile.required_sections:
        if required.casefold() not in present:
            result.append(
                QualityFinding(
                    "QLT-STRUCT-003",
                    Severity.MAJOR,
                    f"Required section is missing: {required}",
                    "document",
                    required,
                    "Add the required, non-empty section.",
                    waiver_eligible=False,
                )
            )
    for match in re.finditer(r"(?m)^\|(?:\s*\|)+$", text):
        result.append(
            finding(
                "QLT-STRUCT-004",
                Severity.MAJOR,
                "Table has no content.",
                text,
                match,
                "Populate or remove the table.",
            )
        )
    for match in re.finditer(r"(?m)(?<!\|\n)^\|[^\n]+\|\n(\|[^\n]+\|)", text):
        if not re.fullmatch(r"\|(?:\s*:?-{3,}:?\s*\|)+", match.group(1)):
            result.append(
                finding(
                    "QLT-STRUCT-005",
                    Severity.MAJOR,
                    "Markdown table has no header delimiter.",
                    text,
                    match,
                    "Add a Markdown header delimiter row.",
                )
            )
    for match in re.finditer(r"(?is)<(script|style|iframe|object|embed|form)\b", text):
        result.append(
            finding(
                "QLT-STRUCT-006",
                Severity.CRITICAL,
                "Uncontrolled raw HTML element.",
                text,
                match,
                "Remove raw active HTML or allow it through a reviewed sanitizer.",
                waiver=False,
            )
        )
    anchors = [
        re.sub(r"[^a-z0-9 -]", "", title.casefold()).replace(" ", "-")
        for _, title, _ in anchor_headings
    ]
    for anchor, count in Counter(anchors).items():
        if anchor and count > 1:
            result.append(
                QualityFinding(
                    "QLT-STRUCT-007",
                    Severity.MAJOR,
                    "Duplicate generated anchor.",
                    "document",
                    anchor,
                    "Give headings unique anchor text.",
                )
            )
    if text.count("```") % 2:
        result.append(
            QualityFinding(
                "QLT-STRUCT-008",
                Severity.MAJOR,
                "Unclosed fenced code block.",
                "document",
                "odd fence count",
                "Close the Markdown code fence.",
            )
        )
    return result
