"""Provider-neutral contracts for artifact lifecycle operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


VerificationStatus = Literal["pending", "verified", "failed"]


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    location: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    artifact_id: str
    domain: str
    media_type: str
    source_version: str
    content_hash: str
    lineage_parents: tuple[str, ...] = ()
    project_id: str | None = None
    session_id: str | None = None
    job_id: str | None = None
    verification_status: VerificationStatus = "pending"
    validation_results: tuple[ValidationFinding, ...] = ()
    created_at: str = ""
    available_operations: tuple[str, ...] = ()
    tombstoned_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OutcomeRecord:
    outcome_id: str
    result_summary: str
    deliverable_artifact_ids: tuple[str, ...] = ()
    evidence_bundle_ids: tuple[str, ...] = ()
    changes_made: tuple[str, ...] = ()
    unresolved_uncertainty: tuple[str, ...] = ()
    cost_estimate: float | None = None
    cost_actual: float | None = None
    elapsed_seconds: float = 0
    verification_status: VerificationStatus = "pending"
    follow_up_capabilities: tuple[str, ...] = ()
    undo_capabilities: tuple[str, ...] = ()
    project_id: str | None = None
    session_id: str | None = None
    job_id: str | None = None
    created_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RevisionRequest:
    artifact_id: str
    source_version: str
    region: str
    instruction: str
    expected_validation: tuple[str, ...]
    idempotency_key: str
