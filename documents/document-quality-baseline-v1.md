# Document Quality Baseline v1

> Inventory date: 2026-07-29 · Manifest: `ops/docs/templates/manifest-v1.json` · First-pass source edits: none

This report triages every file present in `docs/` and `documents/` at the baseline snapshot. Findings describe the historical input; remediation is delivered only through new revisions.

| Severity | File | Owner | Due | Dependency | Finding |
|---|---|---|---|---|---|
| HIGH | `documents/DOC-985fcc33-pj-session-startup-v1.md` | runtime-owner | 2026-08-05 | template-manifest-v1 | Draft procedure has no approval and stale/underspecified launcher assumptions; retire or replace before use. |
| HIGH | `documents/n8n-capability-corpus-v1.md` | evidence-owner | 2026-08-05 | template-manifest-v1; evidence reconciliation | Untrusted structured data; repeated item delimiters and source mappings require validation. |
| MEDIUM | `docs/architecture.md` | documentation-owner | 2026-08-12 | template-manifest-v1 | Missing governed provenance/review header in baseline; addressed by controlled v2 revision. |
| MEDIUM | `docs/code-review-2026-07-29.md` | documentation-owner | 2026-08-12 | template-manifest-v1 | Missing governed provenance/review header in baseline; addressed by controlled v2 revision. |
| MEDIUM | `docs/customer-ux-evaluation-2026-07-29.md` | documentation-owner | 2026-08-12 | template-manifest-v1 | Missing governed provenance/review header in baseline; addressed by controlled v2 revision. |
| MEDIUM | `docs/dependency-audit-2026-07-28.md` | documentation-owner | 2026-08-12 | template-manifest-v1 | Missing governed provenance/review header in baseline; addressed by controlled v2 revision. |
| MEDIUM | `docs/huggingface-mcp-server.md` | documentation-owner | 2026-08-12 | template-manifest-v1 | Missing governed provenance/review header in baseline; addressed by controlled v2 revision. |
| MEDIUM | `docs/product-technology-report-2026-07-28.md` | documentation-owner | 2026-08-12 | template-manifest-v1 | Missing governed provenance/review header in baseline; addressed by controlled v2 revision. |
| MEDIUM | `docs/product-vision.md` | documentation-owner | 2026-08-12 | template-manifest-v1 | Missing governed provenance/review header in baseline; addressed by controlled v2 revision. |
| MEDIUM | `docs/realtime-protocol.md` | documentation-owner | 2026-08-12 | template-manifest-v1 | Missing governed provenance/review header in baseline; addressed by controlled v2 revision. |
| MEDIUM | `docs/runbook.md` | documentation-owner | 2026-08-12 | template-manifest-v1 | Missing governed provenance/review header in baseline; addressed by controlled v2 revision. |
| MEDIUM | `docs/security-controls.md` | documentation-owner | 2026-08-12 | template-manifest-v1 | Missing governed provenance/review header in baseline; addressed by controlled v2 revision. |
| MEDIUM | `documents/n8n-evaluation-evidence-v1.json` | evidence-owner | 2026-08-12 | template-manifest-v1; evidence reconciliation | Evidence-set lineage and cross-artifact mappings are incomplete in v1. |
| MEDIUM | `documents/n8n-evaluation-receipt-v1.json` | evidence-owner | 2026-08-12 | template-manifest-v1; evidence reconciliation | Evidence-set lineage and cross-artifact mappings are incomplete in v1. |
| MEDIUM | `documents/n8n-source-census-v1.json` | evidence-owner | 2026-08-12 | template-manifest-v1; evidence reconciliation | Evidence-set lineage and cross-artifact mappings are incomplete in v1. |
| LOW | `documents/DOC-1cc579c8-pj-capability-overview-v1.md` | runtime-owner | 2026-08-12 | template-manifest-v1 | No material structural issue found in baseline scan. |

## Triage policy

- **High:** not approved for operational use; resolve before the due date or explicitly accept risk.
- **Medium:** governance or traceability gap; resolve in the next controlled revision.
- **Low:** structurally acceptable baseline; review on the normal cadence.

## Dependencies and disposition

1. Approve the template manifest before accepting controlled revisions.
2. Retire `DOC-985fcc33` v1 through its explicit v2 retirement record.
3. Release the corpus, census, evidence, and receipt together only after structural validation and hash reconciliation.
