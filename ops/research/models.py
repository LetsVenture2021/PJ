"""Provider-neutral records for durable, claim-level research."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchStage(StrEnum):
    DRAFT_PLAN = "draft_plan"
    OWNER_REFINEMENT = "owner_refinement"
    SOURCE_ACQUISITION = "source_acquisition"
    SYNTHESIS = "synthesis"
    CLAIM_EXTRACTION = "claim_extraction"
    VERIFICATION = "verification"
    CONFLICT_REVIEW = "conflict_review"
    FINALIZATION = "finalization"


@dataclass
class ResearchQuestion:
    id: str
    text: str
    required: bool = True


@dataclass
class ResearchPlan:
    title: str
    questions: list[ResearchQuestion]
    approved: bool = False
    scope: str = ""
    created_at: str = field(default_factory=utc_now)


@dataclass
class SearchRun:
    id: str
    question_id: str
    retrieval_method: str
    status: str = "pending"
    source_ids: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=utc_now)
    completed_at: str | None = None
    error_code: str | None = None


@dataclass
class SourceRecord:
    id: str
    identity: str
    title: str
    publisher: str | None
    accessed_at: str
    content_hash: str
    trust_class: str
    retrieval_method: str
    published_at: str | None = None
    updated_at: str | None = None
    byte_count: int = 0


@dataclass
class EvidenceExcerpt:
    id: str
    source_id: str
    text: str
    start: int | None = None
    end: int | None = None
    page: int | None = None
    section: str | None = None
    cell_range: str | None = None
    region: tuple[float, float, float, float] | None = None


@dataclass
class ClaimSupport:
    source_id: str
    evidence_id: str
    classification: str = "partial"
    confidence: float | None = None


@dataclass
class Claim:
    id: str
    text: str
    consequential: bool = True
    supports: list[ClaimSupport] = field(default_factory=list)
    verification: str = "partial"
    question_ids: list[str] = field(default_factory=list)


@dataclass
class ResearchBundle:
    id: str
    plan: ResearchPlan
    search_runs: list[SearchRun] = field(default_factory=list)
    sources: list[SourceRecord] = field(default_factory=list)
    evidence: list[EvidenceExcerpt] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    timestamps: dict[str, str] = field(default_factory=dict)
    artifact_hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
