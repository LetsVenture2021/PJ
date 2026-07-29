"""Deterministic document quality and security gates."""

from ops.docs.quality.validation import validate_content, validate_path
from ops.docs.quality.scorecard import (
    calculate_scorecard,
    generate_monthly_scorecard,
    record_control_calibration,
    record_quality_incident,
    record_quality_run,
    regression_alerts,
)
from ops.docs.quality.security import scan_text, validate_audience

__all__ = [
    "calculate_scorecard",
    "generate_monthly_scorecard",
    "record_control_calibration",
    "record_quality_incident",
    "record_quality_run",
    "regression_alerts",
    "scan_text",
    "validate_audience",
    "validate_content",
    "validate_path",
]
