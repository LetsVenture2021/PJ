"""Skill lifecycle, corpus ingestion, and synchronization operations."""

from .service import (
    SKILLOPS_DISPATCH,
    SKILLOPS_SCHEMAS,
    activate_skill,
    create_skill,
    deprecate_skill,
    get_n8n_corpus_status,
    get_vector_sync_status,
    learn_from_vector_store,
    list_coding_capabilities,
    list_n8n_capabilities,
    list_observations,
    observe_pattern,
    review_skills,
    sync_vector_store,
)

__all__ = [
    "SKILLOPS_DISPATCH",
    "SKILLOPS_SCHEMAS",
    "activate_skill",
    "create_skill",
    "deprecate_skill",
    "get_n8n_corpus_status",
    "get_vector_sync_status",
    "learn_from_vector_store",
    "list_coding_capabilities",
    "list_n8n_capabilities",
    "list_observations",
    "observe_pattern",
    "review_skills",
    "sync_vector_store",
]
