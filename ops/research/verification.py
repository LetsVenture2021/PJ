"""Deterministic verification passes; uncertain evidence remains partial."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import ResearchBundle


def collapse_duplicate_sources(bundle: ResearchBundle) -> dict[str, str]:
    canonical: dict[tuple[str, str], str] = {}
    redirects: dict[str, str] = {}
    unique = []
    for source in bundle.sources:
        key = (source.identity, source.content_hash)
        if key in canonical:
            redirects[source.id] = canonical[key]
        else:
            canonical[key] = source.id
            unique.append(source)
    bundle.sources = unique
    for excerpt in bundle.evidence:
        excerpt.source_id = redirects.get(excerpt.source_id, excerpt.source_id)
    for claim in bundle.claims:
        for support in claim.supports:
            support.source_id = redirects.get(support.source_id, support.source_id)
    return redirects


def verify_bundle(bundle: ResearchBundle, *, stale_after_days: int = 365) -> dict:
    collapse_duplicate_sources(bundle)
    evidence = {item.id: item for item in bundle.evidence}
    sources = {item.id: item for item in bundle.sources}
    stale_before = datetime.now(timezone.utc) - timedelta(days=stale_after_days)
    unsupported = []
    stale = []
    for source in bundle.sources:
        stamp = source.updated_at or source.published_at
        if stamp:
            try:
                if datetime.fromisoformat(stamp.replace("Z", "+00:00")) < stale_before:
                    stale.append(source.id)
            except ValueError:
                stale.append(source.id)
    for claim in bundle.claims:
        valid = [
            s
            for s in claim.supports
            if s.source_id in sources
            and s.evidence_id in evidence
            and evidence[s.evidence_id].source_id == s.source_id
        ]
        if claim.consequential and not valid:
            claim.verification = "unsupported"
            unsupported.append(claim.id)
        elif valid and all(s.classification == "entailed" for s in valid):
            claim.verification = "verified"
        else:
            claim.verification = "partial"
        if any(s.classification == "contradicted" for s in valid):
            bundle.conflicts.append(
                {"claim_id": claim.id, "type": "contradiction", "status": "open"}
            )
            claim.verification = "partial"
    covered = {
        qid
        for claim in bundle.claims
        for qid in claim.question_ids
        if claim.verification != "unsupported"
    }
    required = {q.id for q in bundle.plan.questions if q.required}
    missing = sorted(required - covered)
    bundle.gaps.extend({"question_id": qid, "type": "plan_coverage"} for qid in missing)
    return {
        "unsupported_claims": unsupported,
        "stale_sources": stale,
        "coverage_gaps": missing,
        "conflicts": list(bundle.conflicts),
    }
