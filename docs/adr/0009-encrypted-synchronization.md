# ADR 0009: Encrypted synchronization

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

PJ is currently local-first. Synchronization introduces an untrusted transport,
conflicts, deletion semantics, key rotation, and new recovery risks.

## Decision

Synchronization is an optional typed capability and is disabled by default.
When disabled it registers no routes, tools, schedules, or UI affordances. A
sync client transmits versioned, authenticated-encryption envelopes containing
ciphertext plus minimal routing metadata: tenant, device, record type, opaque
record ID, schema version, sequence, key ID, and ciphertext length. Plaintext,
prompts, artifacts, and authorization credentials are never logged or visible
to the relay.

Keys are generated per tenant, stored through an OS-backed secret-store
adapter, separated by purpose, and derived per record with an approved AEAD.
Nonce uniqueness is enforced. Devices have independently revocable signing
identities. Envelopes authenticate scope and ordering metadata; signature and
AEAD verification precede parsing. Key rotation retains wrapped historical
keys only for the declared recovery window.

Sync is an append-only operation log with per-device sequence numbers,
idempotent operation IDs, tombstones, and deterministic type-specific conflict
rules. Rollback/replay, gaps, unknown devices/keys, and newer schemas fail
closed into quarantine. Recovery uses an encrypted export verified by an
actual restore rehearsal; there is no provider-mediated key recovery.

## Consequences

This decision does not enable sync. Implementation requires a threat model,
cryptographic review, device-loss and revocation UX, corrupted/truncated log
tests, conflict fixtures, metadata leakage review, and a measured restore test.
