"""Document templates, versioning, exports, and artifact operations."""

from .service import (
    DOCOPS_DISPATCH,
    DOCOPS_SCHEMAS,
    create_doc_template,
    draft_document,
    draft_presentation,
    export_document,
    finalize_document,
    get_document,
    get_presentation_spec,
    list_doc_templates,
    list_documents,
    list_export_artifacts,
    resolve_export_artifact,
    revise_document,
    revise_presentation,
)

__all__ = [
    "DOCOPS_DISPATCH",
    "DOCOPS_SCHEMAS",
    "create_doc_template",
    "draft_document",
    "draft_presentation",
    "export_document",
    "finalize_document",
    "get_document",
    "get_presentation_spec",
    "list_doc_templates",
    "list_documents",
    "list_export_artifacts",
    "resolve_export_artifact",
    "revise_document",
    "revise_presentation",
]
