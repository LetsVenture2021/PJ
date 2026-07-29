# Collaboration threat model

**Status:** collaboration is disabled by default and must not be enabled in production
until every pre-enable test below passes against the production persistence adapter.

## Boundaries and invariants

Cloudflare Access proves an external identity; it does not authorize a PJ resource.
PJ separately binds that subject to one principal and tenant, then checks an active,
exact-resource grant on every collaborative request. There is no project, folder, or
tenant-wide implicit inheritance. Owner-only behavior remains the default capability.

Shared representations are constructed from an allowlist. They never inherit memories,
other projects, connector credentials, tool arguments, private source contents, or machine
paths. Links, if operationally required, are short-lived, revocable, hashed at rest,
single-resource, and capped at commenter. They cannot invoke connectors or access memory.

## Abuse cases and required tests

| Threat                     | Control and release test                                                                                                             |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| IDOR / artifact guessing   | Change resource IDs and assert exact-grant denial, including valid IDs in another tenant.                                            |
| Confused deputy            | Supply an authorized resource alongside a different target or connector action; assert the server re-resolves and denies the target. |
| Project-memory leakage     | Snapshot every share/export representation and assert forbidden private fields and derived content are absent.                       |
| Forged membership          | Alter email, subject, principal, organization, and membership payloads independently; require the server-side binding.               |
| Invitation replay          | Redeem once, after expiry, and after revocation; only the first valid atomic redemption may succeed.                                 |
| Comment injection          | Render stored comments as text, validate length/encoding, and test HTML, script, Markdown URL, and mention spoof payloads.           |
| Concurrent edits           | Submit two writes against one base version; exactly one succeeds and the other returns an explicit conflict.                         |
| Revoked session/grant/link | Revoke each credential and retry immediately; all subsequent requests must fail despite a valid Access assertion.                    |
| Tenant deletion            | Assert primary rows, search indexes, and all derived previews are removed; audit retention follows tenant policy.                    |

## Residual risks and enablement gate

Local PJ state has no migration or multi-instance transaction coordinator. The reference
store is therefore not a production collaboration database. Before enablement, select a
transactional tenant store, make invitation redemption and version creation atomic, define
audit retention/export policy, add rate limits, and complete adversarial Worker and runtime
integration tests. Any missing identity/tenant configuration is a startup/configuration
failure, not a fallback to email authorization.
