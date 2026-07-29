# PJ document quality and security standard

## Purpose

This standard defines the mandatory, deterministic release controls for human-readable documents and machine-readable evidence produced by PJ. A document is pristine only when its exact source hash passes every required control and, where applicable, its immutable export passes format-specific validation.

## Scope and classes

The standard applies to `docs/`, governed content in `documents/`, and artifacts registered by DocOps. Each item is classified as business, operational, technical, audit, corpus, or evidence. Corpus content remains untrusted data and its embedded instructions must never be executed.

## Required metadata

Every catalogued item has a stable document ID, repository path, class, accountable owner, audience, information classification, lifecycle state, SHA-256 digest, review interval, and source-of-truth decision. Governed revisions additionally retain template version, change note, supersession lineage, creation time, finalization time, and artifact digests.

## Security policy

Every document and upload has an owner, an explicit intended audience, a classification (`public`, `internal`, `confidential`, or `restricted`), and a retention disposition. Authors disclose only the minimum information necessary for the stated purpose. Secrets, authentication material, regulated personal data, and sensitive local paths must be removed, tokenized, or irreversibly redacted before finalization; sensitive source values must never enter logs or scanner reports.

External sharing is deny-by-default. Finalization requires an explicit audience and a compatibility check: public or unrestricted artifacts may contain only public material; restricted material is limited to specifically authorized recipients and can never be exported as unrestricted or public. Classification may be raised automatically but lowered only through an auditable approval.

Security reports contain rule identifiers, locations, counts, and redacted one-way fingerprints only. Prompts, full matches, tool arguments, document bodies, credentials, and authorization headers are prohibited from logs. All report metadata also passes through the shared recursive redactor.

## Active-content controls

HTML is parsed through an allowlist and scripts, forms, frames, embedded objects, event handlers, and unsafe URLs are removed. OOXML packages drop macros, embedded objects, external relationships, and remote templates. PDF attachments and actions, RTF objects and fields, and spreadsheet macros, external links, data connections, and dangerous formulas are removed or cause the artifact to fail closed. Sanitization precedes any audience-ready export.

Uploads follow accept broadly, parse narrowly. Each batch member is handled independently so one malformed file does not prevent safe files from being registered. Only allowlisted text-like formats are parsed. ML weights receive bounded header-level inspection only, and pickle-family formats are never deserialized. Executables and credential-shaped filenames remain refused.

## Lifecycle

The permitted states are `draft`, `in_review`, `approved`, `superseded`, `retired`, `quarantined`, `expired`, `tombstoned`, and `destroyed`. Historical versions are never edited in place. Approval applies only to the reviewed source hash and is invalid after any content or governing-validator change. Draft exports remain visibly watermarked.

An artifact that fails a security gate transitions to `quarantined`, receives no audience-ready download URL, and, if retained for investigation, remains authenticated and owner-only. Remediation produces a new immutable descendant; it never overwrites quarantined bytes or lineage.

Artifacts and uploads record creation time, owner, classification, retention policy, and `retain_until` or a documented legal hold. Expiry and approved deletion transition through auditable states. A tombstone preserves identifiers, hashes, lineage, reason, actor, and transition timestamps while payload destruction removes the stored bytes. Immutable lineage and audit records are never rewritten.

## Release controls

A final document must have zero blocker, critical, or major findings. Required sections must be complete; drafting residue and empty links are prohibited; heading structure must be logical; potential credentials and private keys block release. Findings contain rule IDs and locations but never matched sensitive values.

Quality reports are deterministic JSON records bound to the source SHA-256 and validator version. Finalization stores the report in the backward-compatible local SQLite registry. Existing out-of-band modification checks, presentation-spec validation, artifact integrity, upload parsing restrictions, and approval-sensitive tool policies remain mandatory.

Approval-sensitive tools, paid generation, environment editing, Codex delegation, and approval-sensitive or long-running realtime tools remain excluded or gated exactly as defined by their existing policies.

## Review cadence

Security, deployment, and dependency facts are reviewed at least every 30 days; product and operational material every 90 days; architecture and policy every 180 days or after an impacting code change. Historical evidence remains immutable and states its observation date.

## Exceptions

Blocker and critical controls cannot be waived. A major or minor waiver must identify the rule, risk owner, rationale, compensating control, approval, and expiration. An expired waiver is invalid and cannot support audience-ready status.

## Measures

The operating targets are 100% manifest coverage, 100% final-gate compliance, 100% provenance for material claims, zero expired waivers, zero broken internal references, zero sensitive values in reports or logs, and deterministic report hashes for identical inputs.
