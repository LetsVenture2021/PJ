# Browser client architecture

PJ intentionally retains a **zero-build, native ES module** browser client. The served entry point
remains `webrtc_client.html`; reusable, testable source lives in `web/`. This avoids generated output
drift while the client has no third-party runtime dependencies. Node 20.19 runs deterministic mocked
browser tests, linting, and formatting. A bundler should be introduced only when dependency graph or
browser compatibility requires compilation; generated assets must then be reproducible and must not
replace the compatibility entry point.

## Boundaries

- `webrtc_client.html`: accessible shell and protocol/UI integration.
- `web/session_controller.js`: privacy-bounded continuity, retry timing, and in-flight deduplication.
- `web/service-worker.js`: allowlisted static caching only. Cache names are immutable release versions;
  activation removes other `pj-static-*` versions, making deploy and rollback deterministic.
- Flask remains the authority for sessions, uploads, hashes, authorization, and idempotency. The Worker
  authenticates Access first and forwards opaque idempotency and cursor headers to Flask.

Only conversation IDs, event cursors, UI preferences, and upload ID/range metadata may be persisted in
browser storage. Request bodies and all private content remain memory-only. Service-worker caching is
an explicit static allowlist: API responses, uploads, transcripts, artifacts, and authorization material
cannot enter it.
