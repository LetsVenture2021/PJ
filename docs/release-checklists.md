# Release checklists

Record links to automated runs, fixtures, and manual evidence for every item
that applies. A checked box means evidence was reviewed for the release, not
merely that a feature exists.

## Accessibility

- [ ] Complete every critical flow using keyboard only, including session
  creation, mode selection, tool approval/denial, cancellation, error recovery,
  artifact download, and project switching; focus remains visible and logical.
- [ ] Verify screen-reader names, roles, landmarks, headings, validation errors,
  streaming status, tool/job state, approval expiry, and completion announcements.
- [ ] Confirm announcements do not expose secrets or overwhelm users during
  token streaming; coalesce nonessential updates.
- [ ] Test 200% zoom, reflow at 320 CSS pixels, portrait/landscape layouts,
  reduced motion, high contrast, touch target size, and no two-dimensional
  scrolling in critical flows.
- [ ] Run automated accessibility checks and manually test a supported
  screen-reader/browser pair; triage every violation.
- [ ] Report the accessibility-flow completion rate defined in
  [success measures](success-measures.md).

## Security and privacy

- [ ] Execute the principal/action/resource authorization matrix, including
  unauthenticated, wrong-tenant, wrong-project, expired, replayed, and
  insufficient-role requests.
- [ ] Confirm disabled capability flags remove routes, tools, schedules, event
  discovery, and UI affordances.
- [ ] Run repository and generated-artifact secret scanning; inspect logs to
  confirm recursive redaction and one metadata-only JSON object per line.
- [ ] Verify every action has complete policy-schema coverage: effect class,
  scopes, approval, timeout, retry/idempotency, cost, and reconciliation.
- [ ] Run upload/parser regressions for size/depth limits, traversal,
  executables, credential-shaped names, polyglots, malformed archives,
  corrupted artifacts, header-only ML weight reads, pickle refusal, and
  per-file batch skipping.
- [ ] Exercise provider/connector timeout, Worker retry, Flask restart, SQLite
  lock, disk full, expired lease, approval timeout, corrupted artifact, and
  unknown side-effect outcome with mocked dependencies and temporary state.
- [ ] Confirm production-required configuration fails closed and diagnostic
  configuration output redacts credentials and nested secret values.
- [ ] Restore the release backup into an empty temporary environment and verify
  state and artifact digests.
