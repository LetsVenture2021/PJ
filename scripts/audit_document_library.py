#!/usr/bin/env python3
"""Verify the document catalog without network or provider access."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator, FormatChecker  # noqa: E402

from ops.docs.quality import validate_content  # noqa: E402


DEFAULT_MANIFEST = ROOT / "documents" / "library-manifest.json"
SCHEMA_DIR = ROOT / "schemas" / "documents"
CURRENT_RECORD_SCHEMA_VERSION = "1.0.0"


def _entry_id(entry: dict) -> str:
    return str(entry.get("stable_id") or entry.get("document_id") or "")


def _entry_digest(entry: dict) -> str | None:
    value = entry.get("content_sha256") or entry.get("sha256")
    return str(value) if value is not None else None


def _entry_is_legacy(entry: dict) -> bool:
    return "document_id" in entry or "sha256" in entry or "schema_version" not in entry


def _schema_id(value: str) -> str:
    return value.upper()


def _normalize_schema_patterns(value):
    if isinstance(value, dict):
        normalized = {key: _normalize_schema_patterns(item) for key, item in value.items()}
        if isinstance(normalized.get("pattern"), str):
            normalized["pattern"] = normalized["pattern"].replace("\\\\", "\\")
        return normalized
    if isinstance(value, list):
        return [_normalize_schema_patterns(item) for item in value]
    return value


def _schema_manifest(manifest: dict) -> dict:
    documents = []
    for entry in manifest.get("documents", []):
        if not _entry_is_legacy(entry):
            documents.append(dict(entry))
            continue
        document_class = entry.get("quality_profile") or entry.get("class") or "technical"
        documents.append(
            {
                "schema_version": CURRENT_RECORD_SCHEMA_VERSION,
                "stable_id": _schema_id(_entry_id(entry)),
                "path": entry.get("path", ""),
                "class": entry.get("class", document_class),
                "owner": entry.get("owner", "PJ DocOps"),
                "status": entry.get("status", "published"),
                "classification": entry.get("classification", "internal"),
                "source_of_truth": entry.get("source_of_truth", True),
                "content_sha256": _entry_digest(entry) or "",
                "last_reviewed_at": entry.get("last_reviewed_at"),
                "next_review_at": entry.get("next_review_at"),
                "quality_profile": document_class,
                "supersedes": entry.get("supersedes", []),
                "superseded_by": entry.get("superseded_by", []),
                "derived_from": entry.get("derived_from", []),
                "supports": entry.get("supports", []),
                "references": entry.get("references", []),
                "generated_artifacts": entry.get("generated_artifacts", []),
            }
        )
    normalized = dict(manifest)
    normalized["documents"] = documents
    return normalized


def _timestamp_schema_errors(manifest: dict) -> list[str]:
    errors: list[str] = []
    for entry in manifest.get("documents", []):
        document_id = _entry_id(entry)
        for field in ("last_reviewed_at", "next_review_at"):
            value = entry.get(field)
            if not isinstance(value, str):
                continue
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{document_id} {field} is not a valid RFC3339 date-time")
    return errors


def audit(manifest_path: Path = DEFAULT_MANIFEST) -> dict:
    root = ROOT.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Manifests written before the versioned governance inventory used
    # ``document_id`` and ``sha256``. They remain readable by normalizing those
    # records individually while still validating versioned records and the
    # actual manifest envelope.
    schema = json.loads((SCHEMA_DIR / "document-library-v1.json").read_text())
    schema["properties"]["documents"]["items"] = json.loads(
        (SCHEMA_DIR / "document-metadata-v1.json").read_text()
    )
    schema = _normalize_schema_patterns(schema)
    schema_manifest = _schema_manifest(manifest)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    schema_errors = sorted(error.message for error in validator.iter_errors(schema_manifest))
    schema_errors.extend(_timestamp_schema_errors(schema_manifest))
    schema_errors.sort()
    findings: list[dict] = []
    seen: set[str] = set()
    for entry in manifest.get("documents", []):
        document_id = _entry_id(entry)
        if document_id in seen:
            findings.append({"document_id": document_id, "error": "duplicate_document_id"})
        seen.add(document_id)
        path = (root / entry.get("path", "")).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            findings.append({"document_id": document_id, "error": "path_outside_repository"})
            continue
        if not path.is_file():
            findings.append({"document_id": document_id, "error": "missing_file"})
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != _entry_digest(entry):
            findings.append({"document_id": document_id, "error": "sha256_mismatch"})
        if path.suffix.casefold() == ".md" and entry.get("class") != "corpus":
            report = validate_content(
                path.read_text(encoding="utf-8"), profile=entry.get("class", "governed")
            )
            if report["status"] != "pass":
                findings.append(
                    {
                        "document_id": document_id,
                        "error": "quality_gate_failed",
                        "counts": report["counts"],
                    }
                )
    return {
        "status": "pass" if not schema_errors and not findings else "fail",
        "documents_checked": len(manifest.get("documents", [])),
        "schema_errors": schema_errors,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json", action="store_true", help="Emit the complete JSON report")
    args = parser.parse_args()
    result = audit(args.manifest)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"document library: {result['status']} "
            f"({result['documents_checked']} documents, "
            f"{len(result['schema_errors'])} schema errors, "
            f"{len(result['findings'])} findings)"
        )
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
