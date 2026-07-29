# Full-stack delivery checklist

Use the applicable items; do not add infrastructure merely to satisfy the checklist.

## Product

- [ ] Primary user flow works end to end with real persistence.
- [ ] Acceptance criteria and assumptions are explicit.
- [ ] Empty, loading, success, validation, and failure states are intentional.
- [ ] No required interaction is a placeholder or dead control.

## Data and backend

- [ ] Schema constraints protect invariants and migrations are backward compatible.
- [ ] Server validation covers all untrusted fields.
- [ ] Multi-write operations use appropriate transactions or compensation.
- [ ] API status codes and error shapes are consistent and do not leak internals.
- [ ] External calls have bounded timeouts and appropriate retry or failure behavior.

## Authentication and security

- [ ] Protected reads and writes enforce server-side authorization.
- [ ] Sessions, cookies, CSRF, CORS, and redirects use safe framework defaults.
- [ ] Queries are parameterized and rendered output is contextually escaped.
- [ ] Secrets remain server-side and sensitive content is absent from logs.
- [ ] Uploads, URLs, and rich content have explicit restrictions when supported.

## Frontend and experience

- [ ] The visual design is cohesive and follows existing tokens or a deliberate new direction.
- [ ] Forms have labels, useful validation, and safe duplicate-submit behavior.
- [ ] Keyboard navigation, focus visibility, semantics, and contrast are adequate.
- [ ] Layout works at narrow, intermediate, and wide viewport widths.
- [ ] Reduced motion and long or missing content are handled where relevant.

## Operations

- [ ] Required environment variables are validated without committing values.
- [ ] Production builds and startup paths fail clearly on invalid configuration.
- [ ] Health checks, structured metadata-only logging, and error reporting fit the platform.
- [ ] Deployment configuration matches the intended database and runtime topology.
- [ ] Setup instructions mention only steps that were actually verified.

## Verification

- [ ] Formatting, linting, type checking, tests, and production build pass.
- [ ] Domain, persistence, authorization, API, and UI tests cover the critical flow.
- [ ] The rendered application was inspected when browser tooling was available.
- [ ] The final diff contains no secrets, debug output, transient artifacts, or unrelated edits.
- [ ] Remaining limitations are reported with exact failed commands and reasons.
