"""Deterministic evidence, freshness, terminology, and link governance.

Registry files are JSON so the local gate has no optional parser dependency.  Provider
fact checking is deliberately a separate, optional operation accepting only the
``ResponsesProvider`` protocol; audience-ready export never calls it.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from ops.shared.interfaces import ResponsesProvider

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = ROOT / "documents"
SOURCES_FILE = DOCUMENTS / "sources" / "registry.json"
TERMS_FILE = DOCUMENTS / "governance" / "terminology.json"
RECORDS_FILE = DOCUMENTS / "governance" / "records.json"
DEPENDENCIES_FILE = DOCUMENTS / "governance" / "dependencies.json"

SOURCE_TYPES = {
    "repository_range",
    "operator_observation",
    "test_evidence",
    "configuration_evidence",
    "external_authority",
    "approved_business_assertion",
}
IMPACT_BLOCKERS = {"security", "legal", "financial", "operational", "current_capability"}
CLAIM_ID = re.compile(r"^CLM-[A-Z0-9][A-Z0-9-]{2,63}$")
INLINE_CLAIM = re.compile(r"\[\^(CLM-[A-Z0-9][A-Z0-9-]{2,63})\]")


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _day(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def _claims_path(document_path: Path) -> Path:
    return document_path.with_suffix(".claims.json")


def content_digest(path: str | Path) -> str:
    """Return a reproducible SHA-256 digest for local evidence."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evaluate_document(document_path: str | Path, *, today: date | None = None) -> dict:
    """Evaluate one document without network, credentials, or mutable state."""
    path = Path(document_path)
    now = today or date.today()
    sidecar_path = _claims_path(path)
    if not sidecar_path.exists():
        return {"status": "unregistered", "document": str(path), "blockers": [], "errors": []}
    data = _load(sidecar_path, {})
    sources = {item["source_id"]: item for item in _load(SOURCES_FILE, {}).get("sources", [])}
    errors: list[str] = []
    blockers: list[str] = []
    expiries: list[date] = []

    review_date = data.get("review_date")
    if not review_date:
        errors.append("document review_date is required")
    else:
        try:
            own_expiry = _day(review_date, "review_date")
            expiries.append(own_expiry)
        except ValueError as exc:
            errors.append(str(exc))

    seen: set[str] = set()
    prose_claims = set(INLINE_CLAIM.findall(path.read_text(encoding="utf-8")))
    for claim in data.get("claims", []):
        claim_id = claim.get("claim_id", "")
        if not CLAIM_ID.fullmatch(claim_id) or claim_id in seen:
            errors.append(f"invalid or duplicate claim_id: {claim_id!r}")
        seen.add(claim_id)
        if claim_id not in prose_claims:
            errors.append(f"claim {claim_id} has no inline footnote")
        source_ids = claim.get("source_ids") or []
        if not source_ids:
            errors.append(f"claim {claim_id} has no sources")
        expired = False
        for source_id in source_ids:
            source = sources.get(source_id)
            if not source:
                errors.append(f"claim {claim_id} references unknown source {source_id}")
                continue
            expiry = source.get("expiry_review_date")
            if expiry:
                try:
                    expiry_day = _day(expiry, f"source {source_id} expiry_review_date")
                    expiries.append(expiry_day)
                    expired |= expiry_day < now
                except ValueError as exc:
                    errors.append(str(exc))
        if expired and claim.get("impact") in IMPACT_BLOCKERS:
            blockers.append(f"expired {claim.get('impact')} claim {claim_id}")

    earliest = min(expiries).isoformat() if expiries else None
    if expiries and min(expiries) < now:
        freshness = "expired"
    elif errors:
        freshness = "invalid"
    else:
        freshness = "current"
    return {
        "status": "blocked" if blockers or errors else "ready",
        "document": str(path),
        "freshness": freshness,
        "fresh_until": earliest,
        "blockers": blockers,
        "errors": errors,
    }


def validate_repository(*, today: date | None = None) -> dict:
    """Run all deterministic repository governance checks."""
    errors: list[str] = []
    source_data = _load(SOURCES_FILE, {})
    source_ids: set[str] = set()
    required = {
        "source_id",
        "title",
        "authority_level",
        "source_type",
        "locator",
        "retrieval_date",
        "effective_date",
        "expiry_review_date",
        "content_digest",
        "licensing_constraints",
    }
    for source in source_data.get("sources", []):
        missing = required - source.keys()
        if missing:
            errors.append(f"source {source.get('source_id')} missing {sorted(missing)}")
        source_id = source.get("source_id", "")
        if source_id in source_ids:
            errors.append(f"duplicate source_id {source_id}")
        source_ids.add(source_id)
        if source.get("source_type") not in SOURCE_TYPES:
            errors.append(f"source {source_id} has invalid source_type")

    records = _load(RECORDS_FILE, {}).get("records", [])
    record_ids = {record.get("record_id") for record in records}
    for record in records:
        successor = record.get("successor_id")
        if record.get("status") == "retired" and successor not in record_ids:
            errors.append(f"retired record {record.get('record_id')} lacks valid successor")

    for sidecar in DOCUMENTS.glob("*.claims.json"):
        result = evaluate_document(
            sidecar.with_name(sidecar.name.removesuffix(".claims.json") + ".md"), today=today
        )
        errors.extend(f"{sidecar.name}: {item}" for item in result["errors"])
        manifest_refs = _load(sidecar, {}).get("manifest_record_ids", [])
        errors.extend(
            f"{sidecar.name}: unknown manifest record {item}"
            for item in manifest_refs
            if item not in record_ids
        )

    terms = _load(TERMS_FILE, {}).get("terms", [])
    governed = [ROOT / item for item in ("docs", "documents")]
    files = [
        p
        for root in governed
        if root.exists()
        for p in root.rglob("*")
        if p.suffix.lower() in {".md", ".json"}
    ]
    for term in terms:
        for alias in term.get("prohibited_aliases", []):
            pattern = re.compile(rf"\b{re.escape(alias)}\b")
            for path in files:
                if path in {TERMS_FILE} or "/sources/" in path.as_posix():
                    continue
                if pattern.search(path.read_text(encoding="utf-8")):
                    errors.append(f"{path.relative_to(ROOT)} uses prohibited alias {alias!r}")
    return {"status": "passed" if not errors else "failed", "errors": sorted(set(errors))}


def documents_for_changes(paths: list[str]) -> list[str]:
    """Map changed repository paths to documents whose claims need review."""
    mapping = _load(DEPENDENCIES_FILE, {}).get("dependencies", [])
    affected: set[str] = set()
    for changed in paths:
        for entry in mapping:
            if any(re.fullmatch(pattern, changed) for pattern in entry.get("path_patterns", [])):
                affected.update(entry.get("documents", []))
    return sorted(affected)


def provider_fact_check(
    document_path: str | Path, provider: ResponsesProvider | None = None
) -> dict:
    """Optionally request supplementary review through the provider boundary."""
    if provider is None:
        return {"status": "skipped", "reason": "no ResponsesProvider configured"}
    local = evaluate_document(document_path)
    response = provider.create_response(
        input=json.dumps({"task": "fact_check", "local_governance": local}),
        store=False,
    )
    return {"status": "completed", "response": response, "local_governance": local}
