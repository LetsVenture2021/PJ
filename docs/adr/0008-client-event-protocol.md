# ADR 0008: Client event protocol

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Flask, Worker, browser, SSE, and Realtime messages evolve independently.
Unbounded or unversioned payloads create compatibility and denial-of-service
risks.

## Decision

Every PJ JSON request, response, Worker message, and SSE event uses a validated
envelope containing protocol version, event type, event ID, request/session
IDs where applicable, timestamp, and typed payload. SDP carries the version in
its existing header. Validation occurs both inbound and outbound in the shared
protocol boundary (`ops/realtime/payload_validation.py` until superseded).
Unknown versions return `426`; unknown event types fail closed.

Schemas set explicit limits for bytes, string lengths, array items, object
properties and depth, upload size, pagination size/cursor length, and numeric
ranges. SSE event IDs are monotonically ordered per session. Replay requires a
bounded cursor and is limited by count, age, and bytes; an unavailable cursor
returns a typed resynchronization event. Additive optional fields are compatible;
removing or changing meaning requires a new protocol version.

## Consequences

Contract fixtures are shared across Python and JavaScript tests. Malformed,
oversized, deeply nested, replay overflow, duplicate, and out-of-order cases
are release gates. Disabled capabilities omit associated routes and event
types from discovery.
