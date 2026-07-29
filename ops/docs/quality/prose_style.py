"""Configurable prose and readability review signals."""

import re

from .models import QualityFinding, QualityProfile, Severity


def validate(text: str, profile: QualityProfile, metadata: dict) -> list:
    result = []
    prose = re.sub(r"(?ms)^```.*?^```", "", text)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", prose) if s.strip()]
    long = [s for s in sentences if len(s.split()) > profile.max_sentence_words]
    if long:
        result.append(
            QualityFinding(
                "QLT-PROSE-001",
                Severity.ADVISORY,
                "Some sentences exceed the configured length; readability metrics are review signals, not absolute truth.",
                "document",
                f"{len(long)} long sentence(s)",
                "Review long sentences for clarity; retain them when context warrants.",
            )
        )
    dense = [p for p in re.split(r"\n\s*\n", prose) if len(p.split()) > profile.max_paragraph_words]
    if dense:
        result.append(
            QualityFinding(
                "QLT-PROSE-002",
                Severity.ADVISORY,
                "Paragraph density exceeds the configured review threshold.",
                "document",
                f"{len(dense)} dense paragraph(s)",
                "Consider headings, lists, or shorter paragraphs.",
            )
        )
    passive = len(re.findall(r"(?i)\b(?:is|are|was|were|be|been|being)\s+\w+(?:ed|en)\b", prose))
    if sentences and passive / len(sentences) > profile.max_passive_voice_rate:
        result.append(
            QualityFinding(
                "QLT-PROSE-003",
                Severity.ADVISORY,
                "Estimated passive-voice rate exceeds the review threshold.",
                "document",
                f"estimated rate {passive / len(sentences):.0%}",
                "Review agency and accountability; do not rewrite mechanically.",
            )
        )
    defined = {m.group(2) for m in re.finditer(r"\b([A-Za-z][A-Za-z ]+)\s+\(([A-Z]{2,})\)", prose)}
    undefined = sorted(
        set(re.findall(r"\b[A-Z]{2,6}\b", prose)) - defined - {"TBD", "TODO", "FIXME"}
    )
    if undefined:
        result.append(
            QualityFinding(
                "QLT-PROSE-004",
                Severity.MINOR,
                "Potentially undefined acronyms.",
                "document",
                ", ".join(undefined[:10]),
                "Define acronyms on first use.",
            )
        )
    if re.search(
        r"(?i)\b(?:next|last|this)\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|week|month)\b",
        prose,
    ):
        result.append(
            QualityFinding(
                "QLT-PROSE-005",
                Severity.MAJOR,
                "Ambiguous relative date.",
                "document",
                "relative date expression",
                "Use an unambiguous ISO or fully written date.",
            )
        )
    for discouraged, preferred in profile.terminology.items():
        if re.search(rf"(?i)\b{re.escape(discouraged)}\b", prose):
            result.append(
                QualityFinding(
                    "QLT-PROSE-006",
                    Severity.MINOR,
                    "Terminology differs from the profile.",
                    "document",
                    discouraged,
                    f"Use “{preferred}” consistently.",
                )
            )
    if re.search(r"(?i)\b(?:best|leading|fastest|most secure|unmatched|unparalleled)\b", prose):
        result.append(
            QualityFinding(
                "QLT-PROSE-007",
                Severity.MAJOR,
                "Unbounded superlative requires qualification or evidence.",
                "document",
                "superlative language",
                "Bound the comparison and cite supporting evidence.",
            )
        )
    summary = re.search(r"(?ims)^#{1,6}\s+Executive Summary\s*$\n(.*?)(?=^#|\Z)", text)
    if summary and len(summary.group(1).split()) > profile.executive_summary_max_words:
        result.append(
            QualityFinding(
                "QLT-PROSE-008",
                Severity.MINOR,
                "Executive summary exceeds the configured length.",
                "Executive Summary",
                f"{len(summary.group(1).split())} words",
                "Condense the summary or adjust the profile threshold.",
            )
        )
    return result
