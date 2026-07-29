"""Typed, serializable values shared by the document quality validators."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


VALIDATOR_VERSION = "1.0.0"


class Severity(StrEnum):
    """Finding priority, ordered by its effect on release."""

    BLOCKER = "blocker"  # cannot finalize or distribute
    CRITICAL = "critical"  # material security, privacy, integrity, or factual risk
    MAJOR = "major"  # incomplete, inaccessible, or misleading content
    MINOR = "minor"  # presentation or consistency defect
    ADVISORY = "advisory"  # non-blocking improvement


class ControlResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    REVIEW = "review"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class QualityFinding:
    rule_id: str
    severity: Severity
    message: str
    location: str
    evidence: str
    remediation: str
    validator_version: str = VALIDATOR_VERSION
    waiver_eligible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QualityProfile:
    name: str = "standard"
    high_impact: bool = False
    required_sections: tuple[str, ...] = ()
    max_sentence_words: int = 35
    max_paragraph_words: int = 180
    max_passive_voice_rate: float = 0.25
    executive_summary_max_words: int = 250
    prohibited_internal_paths: tuple[str, ...] = ("/etc/", "/home/", "~/.ssh", "C:\\Users\\")
    terminology: dict[str, str] = field(default_factory=dict)
    source_dependencies: tuple[str, ...] = ()
    max_report_age_seconds: int = 3600

    @classmethod
    def from_value(cls, value: QualityProfile | dict[str, Any] | str | None) -> QualityProfile:
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            value = {"name": value}
        fields = cls.__dataclass_fields__
        cleaned = {key: val for key, val in value.items() if key in fields}
        for key in ("required_sections", "prohibited_internal_paths", "source_dependencies"):
            if key in cleaned:
                cleaned[key] = tuple(cleaned[key])
        return cls(**cleaned)

    def digest_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QualityReport:
    source_hash: str
    profile: QualityProfile
    findings: tuple[QualityFinding, ...]
    controls: dict[str, ControlResult]
    created_at: str
    validator_version: str = VALIDATOR_VERSION
    report_digest: str = ""

    @property
    def passing(self) -> bool:
        return not any(
            f.severity in {Severity.BLOCKER, Severity.CRITICAL, Severity.MAJOR}
            for f in self.findings
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["passing"] = self.passing
        return result
