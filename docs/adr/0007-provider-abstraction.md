# ADR 0007: Provider abstraction

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Provider SDK objects and wire formats must not leak through orchestration or
domain models, and tests must not require network access.

## Decision

Provider-neutral protocols and adapters live in `ops/shared/providers`.
Responses and prompting cross `ResponsesProvider`; other capabilities receive
similarly narrow interfaces only when needed. Domain code uses typed internal
requests, streamed events, usage/cost records, citations, and classified
errors. The OpenAI adapter alone translates SDK types and preserves provider
request IDs for diagnostics.

Timeout, cancellation, retry classification, maximum attempts, model
capabilities, and usage normalization are explicit. Retries occur only for
classified transient failures and replay-safe requests. Test adapters are
deterministic scripted fakes. Runtime selection and credentials come solely
from typed `runtime_config.py`; production validates required settings at
startup and redaction covers nested configuration.

## Consequences

No feature domain imports a provider SDK or reads provider environment
variables. Provider timeout and malformed-stream fixtures become mandatory,
and telemetry records provider/model class, routing reason, latency, status,
cost, and failure class without content.
