"""Deterministic, offline document quality validation."""

from .model import QualityConfig, QualityReport, report_schema
from .service import (
    QualityGateError,
    approve_report,
    assert_report_current,
    validate_document,
    validate_manifest,
)

__all__ = [
    "QualityConfig",
    "QualityGateError",
    "QualityReport",
    "approve_report",
    "assert_report_current",
    "report_schema",
    "validate_document",
    "validate_manifest",
]
