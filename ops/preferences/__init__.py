"""Typed, consent-aware personalization storage."""

from .service import (
    ConsentStatus,
    Preference,
    PreferenceScope,
    PreferenceSource,
    PreferenceStore,
    Proposal,
)

__all__ = [
    "ConsentStatus",
    "Preference",
    "PreferenceScope",
    "PreferenceSource",
    "PreferenceStore",
    "Proposal",
]
