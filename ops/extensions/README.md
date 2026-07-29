# PJ extension protocol

Extensions are offline, content-addressed `.pjx` ZIP archives. `manifest.json` is canonical-JSON signed with a reviewed publisher's Ed25519 key; every payload hash is covered by that signed manifest. The installer verifies the complete archive before creating an extraction directory.

The v1 JSON Schemas in `contracts/` define provider-neutral contracts for local read-only tools, approval actions, connector transports, workflow templates, artifact renderers/validators, event sources, and UI metadata. Packages must declare every capability and resource. Unknown tools default to **deny**; approval tools are never eligible for Realtime.

Lifecycle is preview → explicit permission approval → installed/disabled → staged activation. Upgrades show permission diffs and broader access requires fresh approval. Operators can rollback, disable, revoke, or uninstall while retaining a tombstone. The included registry is curated and local only: remote install, ratings, and automatic execution are intentionally absent.

Code is disabled by default. The only currently supported execution boundary is macOS `/usr/bin/sandbox-exec`; tests skip on other platforms rather than substituting weaker isolation. A production runner must enforce `SandboxLimits` for CPU, memory, wall time, filesystem roots, environment names, and network domains.
