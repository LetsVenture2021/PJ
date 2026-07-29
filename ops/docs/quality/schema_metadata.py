"""Schema and metadata controls."""

from .models import QualityFinding, QualityProfile, Severity


def validate(text: str, profile: QualityProfile, metadata: dict) -> list:
    result = []
    for key in metadata.get("required_metadata", ()):
        if not metadata.get(key):
            result.append(
                QualityFinding(
                    "QLT-META-001",
                    Severity.MAJOR,
                    f"Required metadata is missing: {key}",
                    "metadata",
                    key,
                    "Supply the required metadata field.",
                    waiver_eligible=False,
                )
            )
    if not isinstance(text, str) or not text.strip():
        result.append(
            QualityFinding(
                "QLT-META-002",
                Severity.BLOCKER,
                "Document is empty.",
                "document",
                "zero usable characters",
                "Provide document content.",
                waiver_eligible=False,
            )
        )
    return result
