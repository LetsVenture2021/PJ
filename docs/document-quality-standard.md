# PJ document quality standard

The ratified machine-readable standard is `governance/document-quality/standard.json`. The governed manifest inventories **15** library files. Every entry has an owner, class, validation profile, lifecycle state, remediation disposition, and source SHA-256. The untrusted n8n corpus is an ingestion input, not a library document, and is intentionally outside this denominator.

## Controls and severity

Rule IDs are stable and grouped as metadata, structure, placeholders, security, provenance, freshness, links, and integrity. `blocker` and `critical` findings fail validation; `warning` and `info` remain visible without representing a pass as stronger than it is. Reports contain only paths, hashes, rule IDs, severities, and coarse locations—never excerpts or matched values.

## Operation

Run `python scripts/audit_document_library.py` for a non-mutating advisory audit. Add `--persist` to write canonical, hash-bound evidence and `--blocking` for CI enforcement. The denominator is the manifest entry count; passing and failing counts always add to that denominator. Unsupported export conversions must be recorded as skipped in future artifact assurance and never counted as validated.

`finalize_document` runs the same deterministic source gate and persists its report before changing lifecycle state. The explicit finalization call is the approval act for runtime documents; profiles in the standard declare additional organizational roles for managed-library approval workflows.

Scheduled freshness review, dependency-triggered review, monthly metadata-only scorecards, quarterly calibration, and annual standard review are operating obligations. Renderer-specific semantic, visual, accessibility, and deterministic artifact assurance remains a separate enforcement stage and must bind evidence to immutable artifact IDs.
