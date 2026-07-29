"""Unified facade over DocOps, ImageOps, PresentationOps, and CodeOps."""

from .models import ArtifactDescriptor, OutcomeRecord, RevisionRequest, ValidationFinding
from .service import ArtifactError, ArtifactFacade, register_revision_router

__all__ = [
    "ArtifactDescriptor",
    "ArtifactError",
    "ArtifactFacade",
    "OutcomeRecord",
    "RevisionRequest",
    "ValidationFinding",
    "register_revision_router",
]
