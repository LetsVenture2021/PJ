# Code review — 2026-07-29

## Scope and method

This review covered the tracked Python, JavaScript, HTML, shell, and configuration
surfaces. It combined the complete automated test suite with Ruff, mypy, ESLint,
Prettier, Python bytecode compilation, and a manual review of shared I/O, retry,
configuration, upload, realtime, document, skill, and deployment boundaries.

No finite review can prove that a codebase contains no other defects. The items below
are the findings reproduced during this review and the highest-value maintainability
risks observed in the current tree.

## Corrected defect

### Non-retryable HTTP responses retained pooled connections

`get_with_retry` closed retryable error responses but raised immediately for ordinary
4xx responses without closing them. Requests-style clients retain a connection until
the response body is consumed or the response is closed, so repeated invalid requests
could exhaust the connection pool and degrade throughput. The non-retryable branch now
closes the response before raising, with a regression test that verifies both immediate
failure and cleanup.

## Optimization and maintainability backlog

### P1 — Break up oversized service modules

`ops/skills/service.py`, `ops/docs/service.py`, `ops/realtime/server.py`, and the browser
client each contain thousands of lines and mix validation, persistence, provider I/O,
and orchestration. Split them by boundary and keep public compatibility facades. This
will reduce import cost, narrow regression scope, and make ownership and testing more
tractable.

### P1 — Expand static typing beyond the six-file allowlist

The mypy configuration checks only a small shared/configuration subset. Incrementally
add upload, extraction, provider, and realtime payload modules, then enable stricter
options per migrated module. Boundary-heavy code benefits most because malformed
provider payloads otherwise fail only at runtime.

### P1 — Replace broad exception suppression with typed failures

Several production paths catch `Exception`; some intentionally keep telemetry or
optional extraction from taking down a request, but broad catches also hide programming
errors. Catch expected provider, parsing, filesystem, and subprocess exceptions; log
unexpected failures with operation context; and retain fail-open behavior only where it
is an explicit product requirement.

### P2 — Make atomic file durability explicit

Shared atomic writes flush and `fsync` file contents before replacement, which protects
against partial files. If crash durability is required, also `fsync` the containing
directory after `replace`. Centralize that behavior in `ops/shared/io.py` rather than
duplicating persistence logic across domain services.

### P2 — Bound retry latency and incorporate server guidance

The retry helper has exponential backoff but no maximum delay, jitter, or `Retry-After`
support. Add a delay cap and jitter to prevent synchronized callers, and honor bounded
server guidance for 429/503 responses. Keep deterministic sleep injection for tests.

### P2 — Add performance budgets for large-input paths

The functional suite is comprehensive, but it does not establish latency or memory
budgets for large uploads, document extraction, vector ingestion, or long realtime
sessions. Add representative benchmarks and peak-memory assertions before optimizing;
this avoids speculative changes and makes regressions measurable.

## Verification outcome

All existing Python and Node tests passed after the correction. Ruff, the configured
mypy scope, ESLint, Prettier, Python compilation, and the Cloudflare synchronization
validator also passed. Three Python tests remain intentionally skipped by their existing
environment/feature guards.
