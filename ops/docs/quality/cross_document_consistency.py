"""Cross-document dependency consistency controls."""

from .models import QualityFinding, QualityProfile, Severity


def validate(text: str, profile: QualityProfile, metadata: dict) -> list:
    expected = set(profile.source_dependencies)
    supplied = set(metadata.get("source_dependencies", ()))
    missing = expected - supplied
    if missing:
        return [
            QualityFinding(
                "QLT-CROSSDOC-001",
                Severity.MAJOR,
                "Required source dependencies were not supplied for consistency validation.",
                "metadata.source_dependencies",
                f"{len(missing)} missing dependency record(s)",
                "Supply exact dependency identifiers and hashes.",
                waiver_eligible=False,
            )
        ]
    return []
