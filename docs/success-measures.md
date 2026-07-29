# Release success measures

Evaluation fixtures are immutable, versioned, and run with scripted providers
plus temporary SQLite/filesystem state. Each release records fixture version,
sample count, numerator/denominator, percentile method, confidence notes, and
regressions for research, projects, memory, artifacts, workflows, routing, and
automation. Baselines are established before a feature flag is widened; a
release may not lower a threshold without an explicit decision record.

| Measure | Definition | Initial release gate |
| --- | --- | --- |
| Time to first useful message | Wall time from accepted user event to the first content-bearing, task-relevant client event; report p50 and p95 by surface. | No statistically meaningful regression from the recorded baseline; zero cases without a useful event. |
| Verified outcome completion | Eligible tasks whose asserted outcome is confirmed by a typed tool receipt, accepted artifact, or fixture oracle divided by all eligible tasks. | 100% of deterministic fixtures. |
| Citation entailment | Atomic externally verifiable claims supported by a cited source that entails the claim divided by cited atomic claims, scored against fixture evidence. | At least 95%, with no fabricated citation. |
| Artifact acceptance | Produced artifacts passing schema, media, digest, parser, scope, and fixture-specific semantic checks divided by requested artifacts. | 100% of deterministic fixtures. |
| Duplicate-side-effect count | Additional committed effects beyond one per tenant-scoped idempotency key during retry/restart fixtures. | Exactly 0. |
| Recovery success | Fault scenarios returning to a terminal correct state without lost acknowledged data or an unclassified outcome divided by injected recoverable scenarios. | 100%; ambiguous external writes end explicitly as `outcome_unknown`. |
| Approval comprehension | Participants who correctly identify action, target, effect, cost ceiling, and how to deny after reviewing the approval UI divided by participants tested. | At least 90%, and 100% can deny or cancel. |
| Accessibility completion | Critical-flow tasks completed using the tested assistive mode without assistance divided by attempted tasks, reported for keyboard-only and screen-reader modes. | 100% for each mode, with no critical automated violation. |
| Cost-estimate accuracy | Absolute estimated-minus-actual cost divided by actual cost for paid operations; report median and p95, with zero-cost cases separately. | Median at most 10% and p95 at most 25%; never exceed an approved ceiling. |
| Restore success | Backup fixtures restored into empty state with all expected rows readable, artifact digests valid, scopes preserved, and jobs in valid states divided by restore attempts. | 100%. |
