"""Stable data model for quality findings and reports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


SEVERITY_ORDER = {"blocker": 0, "critical": 1, "major": 2, "minor": 3, "advisory": 4}


@dataclass(frozen=True)
class QualityConfig:
    max_heading_depth: int = 4
    max_table_columns: int = 12
    max_table_rows: int = 100
    max_token_length: int = 80
    require_title: bool = True

    def normalized(self) -> dict[str, Any]:
        return {
            "max_heading_depth": self.max_heading_depth,
            "max_table_columns": self.max_table_columns,
            "max_table_rows": self.max_table_rows,
            "max_token_length": self.max_token_length,
            "require_title": self.require_title,
        }


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    message: str
    line: int = 0
    column: int = 0
    waived: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "waived": self.waived,
        }


@dataclass
class QualityReport:
    source: str
    source_sha256: str
    config_sha256: str
    findings: list[Finding] = field(default_factory=list)
    schema_version: str = "1.0"

    def ordered_findings(self) -> list[Finding]:
        return sorted(
            self.findings,
            key=lambda item: (
                item.line,
                item.column,
                SEVERITY_ORDER[item.severity],
                item.rule_id,
                item.message,
            ),
        )

    @property
    def failed(self) -> bool:
        return any(
            not finding.waived and finding.severity in {"blocker", "critical", "major"}
            for finding in self.findings
        )

    def _payload(self, *, include_digest: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "config_sha256": self.config_sha256,
            "status": "fail" if self.failed else "pass",
            "findings": [item.as_dict() for item in self.ordered_findings()],
        }
        if include_digest:
            payload["digest"] = self.digest
        return payload

    @property
    def digest(self) -> str:
        payload = self._payload(include_digest=False)
        # A checkout or temporary-directory path is not quality-significant.
        payload.pop("source")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return self._payload(include_digest=True)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=indent, sort_keys=True)


def report_schema() -> dict[str, Any]:
    """Return the bundled JSON Schema without requiring package resources."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://pj.local/schemas/document-quality-report-v1.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "source",
            "source_sha256",
            "config_sha256",
            "status",
            "findings",
            "digest",
        ],
        "properties": {
            "schema_version": {"const": "1.0"},
            "source": {"type": "string"},
            "source_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "config_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "digest": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "status": {"enum": ["pass", "fail"]},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["rule_id", "severity", "message", "line", "column", "waived"],
                    "properties": {
                        "rule_id": {"type": "string", "pattern": "^DOC-[A-Z]+-[0-9]{3}$"},
                        "severity": {"enum": list(SEVERITY_ORDER)},
                        "message": {"type": "string"},
                        "line": {"type": "integer", "minimum": 0},
                        "column": {"type": "integer", "minimum": 0},
                        "waived": {"type": "boolean"},
                    },
                },
            },
        },
    }
