# Architecture decision records

PJ uses architecture decision records (ADRs) for decisions that affect stored
state, trust boundaries, public contracts, or more than one runtime. An ADR is
accepted before implementation starts. A later decision is recorded by adding
a superseding ADR rather than rewriting accepted history.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-schema-migration-and-compatibility.md) | Schema migration and compatibility | Accepted |
| [0002](0002-project-and-tenant-scoping.md) | Project and tenant scoping | Accepted |
| [0003](0003-durable-job-semantics.md) | Durable job semantics | Accepted |
| [0004](0004-identity-and-authorization.md) | Identity and authorization | Accepted |
| [0005](0005-artifact-identity-and-lineage.md) | Artifact identity and lineage | Accepted |
| [0006](0006-connector-action-contracts.md) | Connector action contracts | Accepted |
| [0007](0007-provider-abstraction.md) | Provider abstraction | Accepted |
| [0008](0008-client-event-protocol.md) | Client event protocol | Accepted |
| [0009](0009-encrypted-synchronization.md) | Encrypted synchronization | Accepted |

## Required implementation evidence

Every increment governed by these records must identify its owning domain,
capability flag, configuration additions, contract version, compatibility and
rollback behavior, telemetry fields, fault/evaluation fixtures, and relevant
release-checklist evidence. Shared models belong to the owning `ops/` domain;
`ops/shared` is reserved for atomic provider, validation, I/O, retry, logging,
cryptographic, and protocol utilities.
