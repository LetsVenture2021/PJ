"""Helpers for pure quality validators."""

import re

from .models import QualityFinding, Severity, VALIDATOR_VERSION


def line_location(text: str, offset: int) -> str:
    return f"line {text.count(chr(10), 0, offset) + 1}"


def finding(
    rule: str,
    severity: Severity,
    message: str,
    text: str,
    match,
    remediation: str,
    *,
    evidence: str | None = None,
    waiver: bool = True,
) -> QualityFinding:
    safe_evidence = evidence if evidence is not None else match.group(0)[:80]
    return QualityFinding(
        rule,
        severity,
        message,
        line_location(text, match.start()),
        safe_evidence,
        remediation,
        VALIDATOR_VERSION,
        waiver,
    )


def headings(text: str) -> list[tuple[int, str, int]]:
    return [
        (len(m.group(1)), m.group(2).strip(), m.start())
        for m in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", text, re.M)
    ]
