#!/usr/bin/env python3
"""Verify the document catalog without network or provider access."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator  # noqa: E402

from ops.docs.quality import validate_content  # noqa: E402


DEFAULT_MANIFEST = ROOT / "documents" / "library-manifest.json"
SCHEMA_DIR = ROOT / "schemas" / "documents"


def audit(manifest_path: Path = DEFAULT_MANIFEST) -> dict:
    root = ROOT.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Manifests written before the versioned governance inventory used
    # ``document_id`` and ``sha256``.  They remain readable so operators can
    # audit existing local catalogs before explicitly bootstrapping the new
    # format.
    legacy_manifest = any(
        "document_id" in entry or "sha256" in entry for entry in manifest.get("documents", [])
    )
    schema = json.loads((SCHEMA_DIR / "document-library-v1.json").read_text())
    schema["properties"]["documents"]["items"] = json.loads(
        (SCHEMA_DIR / "document-metadata-v1.json").read_text()
    )
    validator = Draft202012Validator(schema)
    schema_errors = (
        []
        if legacy_manifest
        else sorted(error.message for error in validator.iter_errors(manifest))
    )
    findings: list[dict] = []
    seen: set[str] = set()
    for entry in manifest.get("documents", []):
        document_id = entry.get("stable_id") or entry.get("document_id", "")
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
        expected_digest = entry.get("content_sha256") or entry.get("sha256")
        if digest != expected_digest:
            findings.append({"document_id": document_id, "error": "sha256_mismatch"})
        if (
            not legacy_manifest
            and path.suffix.casefold() == ".md"
            and entry.get("class") != "corpus"
        ):
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
