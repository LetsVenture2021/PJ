"""Deterministic document quality and security gates."""

from ops.docs.quality.security import scan_text, validate_audience

__all__ = ["scan_text", "validate_audience"]
