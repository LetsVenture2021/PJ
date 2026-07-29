# Document Quality and Security Standard

## Security policy

Every document and upload has an owner, an explicit intended audience, a
classification (`public`, `internal`, `confidential`, or `restricted`), and a
retention disposition. Authors disclose only the minimum information necessary
for the stated purpose. Secrets, authentication material, regulated personal
data, and sensitive local paths must be removed, tokenized, or irreversibly
redacted before finalization; sensitive source values must never enter logs or
scanner reports.

External sharing is deny-by-default. Finalization requires an explicit audience
and a compatibility check: public/unrestricted artifacts may contain only
public material; restricted material is limited to specifically authorized
recipients and can never be exported as unrestricted or public. Classification
may be raised automatically but lowered only through an auditable approval.

Security reports contain rule identifiers, locations, counts, and redacted
one-way fingerprints only. Prompts, full matches, tool arguments, document
bodies, credentials, and authorization headers are prohibited from logs. All
report metadata also passes through the shared recursive redactor.

## Active-content controls

HTML is parsed through an allowlist and scripts, forms, frames, embedded
objects, event handlers, and unsafe URLs are removed. OOXML packages drop
macros, embedded objects, external relationships, and remote templates. PDF
attachments and actions, RTF objects and fields, and spreadsheet macros,
external links, data connections, and dangerous formulas are removed or cause
the artifact to fail closed. Sanitization precedes any audience-ready export.

Uploads follow **accept broadly, parse narrowly**. Each batch member is handled
independently so one malformed file does not prevent safe files from being
registered. Only allowlisted text-like formats are parsed. ML weights receive
bounded header-level inspection only, and pickle-family formats are never
deserialized. Executables and credential-shaped filenames remain refused.

## Quarantine, retention, and destruction

An artifact that fails a security gate transitions to `quarantined`, receives no
audience-ready download URL, and, if retained for investigation, remains
authenticated and owner-only. Remediation produces a new immutable descendant;
it never overwrites quarantined bytes or lineage.

Artifacts and uploads record creation time, owner, classification, retention
policy, and `retain_until` (or a documented legal hold). Expiry and approved
deletion transition through auditable states (`ready` → `expired` →
`tombstoned`/`destroyed`). A tombstone preserves identifiers, hashes, lineage,
reason, actor, and transition timestamps while payload destruction removes the
stored bytes. Immutable lineage and audit records are never rewritten.

Approval-sensitive tools, paid generation, environment editing, Codex
delegation, and approval-sensitive or long-running realtime tools remain
excluded or gated exactly as defined by their existing policies.
