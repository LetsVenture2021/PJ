"""Durable, citation-aware research domain."""

from .export import artifact_hash, export_json, export_markdown
from .lifecycle import ResearchCheckpointStore, ResearchLifecycle
from .models import (
    EvidenceExcerpt,
    Claim,
    ClaimSupport,
    ResearchBundle,
    ResearchPlan,
    ResearchQuestion,
    ResearchStage,
    SearchRun,
    SourceRecord,
)
from .service import RESEARCH_DISPATCH, RESEARCH_SCHEMAS, get_deep_research, start_deep_research
from .sources import (
    check_broken_links,
    enforce_source_limits,
    normalize_local_source,
    normalize_web_source,
)
from .verification import collapse_duplicate_sources, verify_bundle

__all__ = [
    "Claim",
    "ClaimSupport",
    "EvidenceExcerpt",
    "ResearchBundle",
    "ResearchCheckpointStore",
    "ResearchLifecycle",
    "ResearchPlan",
    "ResearchQuestion",
    "ResearchStage",
    "SearchRun",
    "SourceRecord",
    "artifact_hash",
    "check_broken_links",
    "collapse_duplicate_sources",
    "enforce_source_limits",
    "export_json",
    "export_markdown",
    "get_deep_research",
    "normalize_local_source",
    "normalize_web_source",
    "start_deep_research",
    "verify_bundle",
    "RESEARCH_DISPATCH",
    "RESEARCH_SCHEMAS",
]
