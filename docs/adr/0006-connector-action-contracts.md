# ADR 0006: Connector action contracts

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Connector actions vary in authority, cost, replay safety, and observability.
Treating them as arbitrary tool calls makes approval and recovery ambiguous.

## Decision

Each action publishes a versioned typed contract with connector/action ID,
bounded input and output schemas, required credential scopes, tenant/project
rules, effect class (`read`, `reversible_write`, or `irreversible_write`),
idempotency support, approval requirement, timeout, retry policy, cost model,
and reconciliation operation. Inputs are validated before policy evaluation;
outputs are validated before persistence or provider submission.

Writes use prepare/authorize/execute/record phases. Approval binds the actor,
action version, target summary, cost ceiling, and canonical input digest and
expires after a bounded interval. Changed input invalidates approval. A timeout
is not evidence of failure: actions without a conclusive reconciliation result
become `outcome_unknown`. Secrets remain in the adapter boundary and never
enter contracts, logs, or provider-visible tool output.

## Consequences

New actions cannot ship without mocked timeout, denial, malformed response,
duplicate request, approval timeout, and unknown-outcome tests. Connector
telemetry contains IDs, timings, effect class, status, cost, and failure class
only.
