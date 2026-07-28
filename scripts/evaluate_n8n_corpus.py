#!/usr/bin/env python3
"""Evaluate an n8n corpus and emit durable evidence plus a release receipt."""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import skillops  # noqa: E402


DEFAULT_CORPUS = ROOT / "documents" / "n8n-capability-corpus-v1.md"
DEFAULT_CENSUS = ROOT / "documents" / "n8n-source-census-v1.json"
DEFAULT_EVIDENCE = ROOT / "documents" / "n8n-evaluation-evidence-v1.json"
DEFAULT_RECEIPT = ROOT / "documents" / "n8n-evaluation-receipt-v1.json"

RETRIEVAL_CASES = (
    ("schedule timezone trigger activation", "N8N-024"),
    ("webhook endpoint authentication request validation", "N8N-025"),
    ("HTTP Request API response integration", "N8N-023"),
    ("sub-workflow reusable inputs outputs ownership", "N8N-009"),
    ("item linking lineage source data", "N8N-014"),
    ("pinned mock data testing samples", "N8N-018"),
    ("queue mode workers broker scaling", "N8N-033"),
    ("error workflow alert diagnostic context", "N8N-010"),
    ("source control environments promotion review", "N8N-040"),
    ("binary files images storage metadata", "N8N-019"),
)

SECURITY_CASES = (
    ("SSRF server-side request forgery outbound network", "N8N-030"),
    ("redact sensitive execution data logs", "N8N-031"),
    ("community nodes supply-chain unverified risk", "N8N-037"),
    ("external secrets vault least privilege", "N8N-028"),
    ("projects RBAC roles least privilege ownership", "N8N-039"),
)


def _canonical_json(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if len(token) > 2
    }


def _search(records: list[dict], query: str) -> list[str]:
    query_tokens = _tokens(query)
    ranked = []
    for record in records:
        searchable = " ".join((
            record["canonical_title"],
            record["what_it_teaches"],
            " ".join(record["taxonomy"]),
            " ".join(record["task_types"]),
            " ".join(record["workflow"]),
            " ".join(record["safety_controls"]),
            " ".join(record["failure_modes"]),
        ))
        score = len(query_tokens & _tokens(searchable))
        ranked.append((score, record["item_id"]))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item_id for _, item_id in ranked[:5]]


def _run_cases(records: list[dict], cases: tuple) -> list[dict]:
    results = []
    for query, expected in cases:
        top5 = _search(records, query)
        results.append({
            "query": query,
            "expected_item_id": expected,
            "top5_item_ids": top5,
            "passed": expected in top5,
        })
    return results


def evaluate(
        corpus_path: Path,
        census_path: Path,
        evaluated_at: str) -> tuple[dict, dict]:
    corpus_bytes = corpus_path.read_bytes()
    corpus_text = corpus_bytes.decode("utf-8")
    census = json.loads(census_path.read_text(encoding="utf-8"))
    parsed = skillops._parse_n8n_capability_corpus(corpus_text)
    retrieval = _run_cases(parsed["records"], RETRIEVAL_CASES)
    security = _run_cases(parsed["records"], SECURITY_CASES)
    source_ids = {
        source["source_record_id"] for source in census.get("sources", [])
    }
    record_source_ids = {
        record["source_record_id"] for record in parsed["records"]
    }
    source_integrity = (
        len(census.get("sources", [])) == len(parsed["records"])
        and source_ids == record_source_ids
        and all(record["source_content_sha256"] for record in parsed["records"])
    )
    credential_exposures = sum(
        1
        for pattern in skillops._N8N_SECRET_PATTERNS
        if pattern.search(corpus_text)
    )
    invented_node_parameters = len(re.findall(
        r"(?im)^\s*(?:node_)?parameter\s*:",
        corpus_text,
    ))
    metrics = {
        "canonical_pages_total": int(census["canonical_pages_total"]),
        "canonical_pages_covered": (
            len(parsed["records"]) if source_integrity else 0
        ),
        "inaccessible_sources_total": int(
            census["inaccessible_sources_total"]
        ),
        "inaccessible_sources_dispositioned": sum(
            1
            for source in census.get("sources", [])
            if not source.get("accessible_at_snapshot")
            and source.get("disposition")
        ),
        "retrieval_cases_total": len(retrieval),
        "retrieval_top5_passed": sum(case["passed"] for case in retrieval),
        "security_warning_cases_total": len(security),
        "security_warning_cases_passed": sum(
            case["passed"] for case in security
        ),
        "invented_node_parameters": invented_node_parameters,
        "credential_exposures": credential_exposures,
    }
    gates = skillops._n8n_evaluation_gates(
        len(parsed["records"]),
        metrics,
        source_integrity=source_integrity and not parsed["errors"],
    )
    evidence = {
        "schema_version": "1",
        "evaluation_method": "deterministic-token-retrieval-and-structural-scan",
        "evaluated_at": evaluated_at,
        "corpus_path": str(corpus_path.relative_to(ROOT)),
        "census_path": str(census_path.relative_to(ROOT)),
        "parser_errors": parsed["errors"],
        "retrieval_cases": retrieval,
        "security_warning_cases": security,
        "metrics": metrics,
        "gates": gates,
        "source_integrity": source_integrity,
        "census_sha256": hashlib.sha256(
            _canonical_json(census)
        ).hexdigest(),
    }
    evidence_sha256 = hashlib.sha256(_canonical_json(evidence)).hexdigest()
    receipt = {
        "schema_version": "1",
        "evaluation_id": "n8n-eval-" + evidence_sha256[:16],
        "evaluated_at": evidence["evaluated_at"],
        "evaluator": "scripts/evaluate_n8n_corpus.py",
        "evidence_sha256": evidence_sha256,
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "corpus_version": parsed["corpus_version"],
        "registry_sha256": skillops._n8n_records_sha256(parsed["records"]),
        "metrics": metrics,
    }
    return evidence, receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    if args.receipt.is_file():
        existing = json.loads(args.receipt.read_text(encoding="utf-8"))
        existing_evaluated_at = str(existing.get("evaluated_at") or "").strip()
        if existing_evaluated_at:
            evaluated_at = existing_evaluated_at
    evidence, receipt = evaluate(args.corpus, args.census, evaluated_at)
    args.evidence.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "evaluation_id": receipt["evaluation_id"],
        "corpus_sha256": receipt["corpus_sha256"],
        "receipt_sha256": skillops._n8n_receipt_sha256(receipt),
        "gates_passed": bool(evidence["gates"]["passed"]),
        "parser_errors": evidence["parser_errors"],
        "metrics": receipt["metrics"],
    }, indent=2, sort_keys=True))
    return 0 if (
        evidence["gates"]["passed"] and not evidence["parser_errors"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
