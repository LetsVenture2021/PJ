"""Artifact quality API and immutable, provenance-bound reports."""

from __future__ import annotations

import json
import platform
import tempfile
from pathlib import Path
from typing import Any

from ops.shared.io import sha256_file

from .adapters import ADAPTERS
from .model import Block, CanonicalDocument


def quality_check(path: str | Path, format: str, canonical: CanonicalDocument) -> dict[str, Any]:
    """Inspect an export, compare it with the canonical model, and return a deterministic report."""
    path = Path(path)
    adapter = ADAPTERS.get(format.lower())
    if adapter is None:
        return {"status": "skipped", "format": format, "reason": "no quality adapter is registered"}
    inspection = adapter.inspect(path)
    adapter.compare(canonical, inspection)
    return {
        "status": "failed" if inspection.errors else "passed",
        "format": adapter.format,
        "renderer_version": adapter.renderer_version,
        "platform": platform.system(),
        "checks": inspection.checks,
        "errors": inspection.errors,
        "warnings": inspection.warnings,
        "canonical_block_count": len(canonical.blocks),
    }


def write_quality_report(
    artifact: dict[str, Any], artifact_path: str | Path, report: dict[str, Any], *, source_hash: str
) -> Path:
    """Write the report beside immutable bytes and bind it to their identity."""
    path = Path(artifact_path)
    bound = dict(report)
    bound["artifact"] = {
        key: artifact[key] for key in ("artifact_id", "byte_size", "mime_type", "sha256")
    }
    bound["source_hash"] = source_hash
    if path.stat().st_size != artifact["byte_size"] or sha256_file(path) != artifact["sha256"]:
        raise ValueError("artifact changed before quality report registration")
    destination = path.parent / "quality-report.json"
    encoded = (json.dumps(bound, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=".quality-report.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
        handle.flush()
    temporary.chmod(0o600)
    temporary.replace(destination)
    return destination


__all__ = ["ADAPTERS", "Block", "CanonicalDocument", "quality_check", "write_quality_report"]
