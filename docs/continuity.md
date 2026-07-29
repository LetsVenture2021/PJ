# Release and state continuity

Release manifests are Ed25519-signed and contain only identifiers, hashes, versions,
routes, configuration **names**, and a UTC deployment timestamp. Signing keys and
deployment credentials remain in operator secret storage. `scripts/promote_release.sh`
promotes client, Worker, then runtime independently; each stage performs preflight,
promotion, health verification, and writes an immutable receipt with a rollback pointer.

Before sync, `MigrationLedger` requires a nonempty backup, verifies every applied
migration checksum, rejects future schemas, and applies each new migration in one
transaction. Sync transports AES-256-GCM authenticated immutable change records—not a
live SQLite file. Records bind device, tenant/owner scope, local sequence, entity version,
operation ID, content hash, and conflict rule. Keys are passed separately. Rotation adds
a new key ID, revoked devices are refused, sequence watermarks prevent replay, and
recovery keys must stay in offline operator custody and must never enter assistant tools.

Conflict rules are deterministic: artifacts coexist, comments append, preferences raise
explicit version conflicts, documents branch, approvals never merge, and external-action
receipts remain immutable.

Backups include a database hash, artifact inventory, schema version, encryption scheme,
and completion flag. Verification is mandatory after creation. Restore copies into an
isolated directory, validates it, creates a rollback sibling, and only then atomically
replaces active state. Operator status is emitted by `dashboard_snapshot`; it covers
release parity, migrations, sync lag, backup age, restore verification, connector health,
and kill switches using bounded reason codes only.
