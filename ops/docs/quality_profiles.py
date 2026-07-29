"""Versioned quality-control profiles for governed documents."""

from dataclasses import dataclass


@dataclass(frozen=True)
class QualityProfile:
    """Controls and measurable acceptance thresholds for a document class."""

    name: str
    version: str
    required_controls: tuple[str, ...]
    thresholds: dict[str, float]


QUALITY_PROFILES: dict[str, QualityProfile] = {
    "business": QualityProfile(
        "business",
        "1.0.0",
        ("owner", "approval", "currency", "source_citations"),
        {"required_section_coverage": 1.0, "citation_coverage": 0.9},
    ),
    "operational": QualityProfile(
        "operational",
        "1.0.0",
        ("owner", "approval", "rollback", "review_date"),
        {"required_section_coverage": 1.0, "procedure_test_coverage": 0.9},
    ),
    "technical": QualityProfile(
        "technical",
        "1.0.0",
        ("owner", "technical_review", "references", "verification"),
        {"required_section_coverage": 1.0, "reference_integrity": 1.0},
    ),
    "audit": QualityProfile(
        "audit",
        "1.0.0",
        ("owner", "independent_approval", "provenance", "immutable_evidence"),
        {"evidence_integrity": 1.0, "control_coverage": 1.0},
    ),
    "corpus": QualityProfile(
        "corpus",
        "1.0.0",
        ("owner", "source_census", "provenance", "retrieval_evaluation"),
        {"source_coverage": 1.0, "retrieval_success": 0.95},
    ),
    "structured-data": QualityProfile(
        "structured-data",
        "1.0.0",
        ("owner", "schema_validation", "digest", "provenance"),
        {"schema_validity": 1.0, "record_validity": 1.0},
    ),
}


def get_quality_profile(name: str) -> QualityProfile:
    """Return a named profile, rejecting undocumented profile selection."""
    try:
        return QUALITY_PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown document quality profile: {name}") from exc
