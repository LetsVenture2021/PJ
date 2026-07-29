# ADR 0001: Schema migration and compatibility

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

PJ persists SQLite rows, `state.json`, and filesystem artifacts without a
migration framework or multi-instance coordination. Existing installations
must remain readable while features are delivered incrementally.

## Decision

Persist an integer schema version with every new record and envelope. Database
changes are ordered, transactional, idempotent migrations recorded in a
migration ledger. They acquire SQLite's write lock, use a bounded busy timeout,
and are safe after interruption. Prefer additive nullable columns and new
tables; destructive changes use expand/migrate/contract and the contract phase
cannot ship until the supported rollback window expires.

Readers accept the current version and explicitly listed older versions,
normalizing them into the owning domain's typed model. Writers emit only the
current version. Unknown newer versions fail closed without modifying state.
Filesystem writes use write/fsync/atomic-rename and retain the prior usable
file until validation succeeds. Backups and a restore rehearsal are required
before a production migration.

Moved top-level Python modules remain aliases made through `sys.modules`, not
re-export shims, so old and `ops.*` imports resolve to the same module object.

## Consequences

Every state change needs forward migration, compatibility tests from the oldest
supported version, interrupted-migration and lock tests, and a documented
rollback boundary. There is still one local writer; this ADR does not claim
distributed coordination.
