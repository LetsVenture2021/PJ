"""Artifact-specific structural controls."""

from .models import QualityFinding, QualityProfile, Severity


def validate(text: str, profile: QualityProfile, metadata: dict) -> list:
    kind = metadata.get("artifact_type", "markdown")
    if kind not in {"markdown", "presentation", "html", "document"}:
        return [
            QualityFinding(
                "QLT-ARTIFACT-001",
                Severity.MAJOR,
                "Artifact type is not recognized by the quality profile.",
                "metadata.artifact_type",
                str(kind),
                "Use a supported artifact type or extend the profile.",
            )
        ]
    return []
