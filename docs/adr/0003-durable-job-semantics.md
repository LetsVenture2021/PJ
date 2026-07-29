# ADR 0003: Durable job semantics

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Long-running workflows outlive HTTP requests and may be retried after process
restart. External side effects cannot generally be made exactly once.

## Decision

Jobs use a durable state machine: `queued`, `leased`, `waiting_approval`,
`succeeded`, `failed`, `cancelled`, or `outcome_unknown`. Claims use an atomic
lease with owner, expiry, attempt number, and heartbeat. Expired leases may be
reclaimed only when the action contract says replay is safe.

Submission requires a tenant-scoped idempotency key and stores an immutable
request digest. The same key and digest returns the existing job; a different
digest conflicts. Transient failures use bounded exponential backoff with
jitter and an attempt ceiling. Permanent failures do not retry. Side-effecting
steps require a downstream idempotency key or a durable intent/result ledger.
If interruption occurs after dispatch but before a verifiable result, the job
enters `outcome_unknown` and requires reconciliation or human resolution.
Cancellation is cooperative and never claims to undo a committed effect.

## Consequences

PJ promises at-least-once execution only for replay-safe work, not universal
exactly-once effects. Fault tests cover restart, retry, expired leases, approval
timeout, SQLite lock, and unknown outcomes; telemetry excludes job bodies.
