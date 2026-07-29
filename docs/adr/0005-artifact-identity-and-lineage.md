# ADR 0005: Artifact identity and lineage

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Exports and uploads need stable identity, integrity checks, provenance, and
safe replacement without exposing source contents in logs.

## Decision

An artifact has an opaque immutable `artifact_id`; a version has a separate
`artifact_version_id`, SHA-256 digest, byte length, declared and detected media
types, creation time, tenant/project scope, and validation status. Identity is
not a filename or path. Bytes are written to a quarantine location, bounded,
hashed while streaming, parsed only through the allowlist, then atomically
promoted after validation. Executables, credential-shaped filenames, and
pickle-family checkpoints remain refused; ML weights receive header-only reads.

Lineage is an append-only directed graph of typed edges such as `uploaded`,
`generated_from`, `transformed_from`, and `cited_by`. Each edge records actor,
tool/action contract version, source version IDs, and sanitized parameters
digest—not prompts, arguments, or source bodies. Downloads resolve registry
records, verify scope and digest, and never accept a raw path.

## Consequences

Mutable documents create new versions. Corruption is quarantined and reported
without serving bytes. Tests cover tampering, traversal, parser regressions,
batch per-file skipping, and lineage cycles.
