"""Lifecycle and approval controls."""

from .models import QualityFinding, QualityProfile, Severity


def validate(text: str, profile: QualityProfile, metadata: dict) -> list:
    if profile.high_impact and not metadata.get("approval_record"):
        return [
            QualityFinding(
                "QLT-LIFECYCLE-001",
                Severity.BLOCKER,
                "High-impact profile requires an explicit approval record.",
                "metadata.approval_record",
                "approval absent",
                "Record approver, decision, scope, and timestamp before finalization.",
                waiver_eligible=False,
            )
        ]
    return []
