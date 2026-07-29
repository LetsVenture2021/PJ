# Durable jobs operator guide

Job definitions under `ops/jobs` are provider- and UI-neutral. The local SQLite
database is coordination state, not a claim of distributed execution. Run one
`JobExecutor.run_forever()` loop by default, either in an operator-owned process
or a controlled application thread; importing the package starts nothing.

## Operations

* **Shutdown:** call `stop()` and allow the current bounded handler operation to
  return. Cancellation and shutdown do not imply that an external effect was
  rolled back.
* **Lease expiry:** leases use an atomic `BEGIN IMMEDIATE` acquisition. A dead
  executor's expired lease is removed on a later acquisition. Heartbeats extend
  only a matching, unexpired token. Operators should use a lease duration longer
  than the maximum bounded operation and limit repeated recovery attempts.
* **Unknown outcomes:** an effect that fails after dispatch is recorded as
  `outcome_unknown`; do not replay it until its connector confirms the outcome.
  Stable operation keys and canonical request hashes prevent conflicting reuse.
* **Backup:** stop the executor, use SQLite's online backup API (or a locked
  filesystem snapshot), and back up the database together with any domain
  artifacts. Never copy a live database file without its WAL files.
* **Recovery:** restore the complete snapshot, run SQLite integrity checks, and
  start exactly one executor. Inspect unknown outcomes and expired leases before
  enabling schedules. This release deliberately has no event-triggered jobs;
  connectors must gain signature verification first.

Schedules store an IANA timezone, missed-run policy, optional quiet hours, and a
budget. One-time and five-field cron configurations are typed. Schedule API
persistence is intentionally reserved until an operator supplies an authenticated
management surface.
