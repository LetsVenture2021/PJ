"""Local link and reference integrity controls (no network I/O)."""

import re

from .models import QualityFinding, QualityProfile, Severity


def validate(text: str, profile: QualityProfile, metadata: dict) -> list:
    result = []
    definitions = set(re.findall(r"(?m)^\[([^]]+)\]:\s+\S+", text))
    refs = set(re.findall(r"\[[^]]+\]\[([^]]+)\]", text))
    for ref in sorted(refs - definitions):
        result.append(
            QualityFinding(
                "QLT-LINK-001",
                Severity.MAJOR,
                "Undefined Markdown reference.",
                "document",
                ref,
                "Add the reference definition or use an inline destination.",
            )
        )
    for match in re.finditer(r"\[[^]]+\]\((#[^)]+)\)", text):
        target = match.group(1)[1:]
        anchors = {
            re.sub(r"[^a-z0-9 -]", "", h.casefold()).replace(" ", "-")
            for h in re.findall(r"(?m)^#{1,6}\s+(.+)$", text)
        }
        if target not in anchors:
            result.append(
                QualityFinding(
                    "QLT-LINK-002",
                    Severity.MAJOR,
                    "Internal link target does not exist.",
                    f"line {text.count(chr(10), 0, match.start()) + 1}",
                    target,
                    "Correct the target or add its heading.",
                )
            )
    return result
