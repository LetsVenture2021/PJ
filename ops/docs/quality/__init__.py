"""Deterministic document quality and security gates."""

from .model import QualityConfig, QualityReport, report_schema
from .scorecard import (
    calculate_scorecard,
    generate_monthly_scorecard,
    record_control_calibration,
    record_quality_incident,
    record_quality_run,
    regression_alerts,
)
from .security import scan_text, validate_audience
from .service import (
    QualityGateError,
    approve_report,
    assert_report_current,
    validate_document,
    validate_manifest,
)
from .validation import validate_content, validate_path

__all__ = [
    "QualityConfig",
    "QualityGateError",
    "QualityReport",
    "approve_report",
    "assert_report_current",
    "calculate_scorecard",
    "generate_monthly_scorecard",
    "record_control_calibration",
    "record_quality_incident",
    "record_quality_run",
    "regression_alerts",
    "report_schema",
    "scan_text",
    "validate_audience",
    "validate_content",
    "validate_document",
    "validate_manifest",
    "validate_path",
]
