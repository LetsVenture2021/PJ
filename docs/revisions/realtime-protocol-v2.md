---
document_id: pj.docs.realtime-protocol
version: 2
status: approved
template_id: protocol_specification
template_version: 1
supersedes: docs/realtime-protocol.md
prior_sha256: 9a470ecc0dd8f38f489e281c77b7b91aab282b75576bcb6f25eb161060d56aff
change_note: Added provenance, review, and approval metadata; authored content is unchanged.
provenance: Repository-authored operational documentation.
reviewed_by: repository-owner
reviewed_at: 2026-07-29
approved_by: repository-owner
approved_at: 2026-07-29
---

# Realtime protocol compatibility

PJ's browser, realtime Flask server, and Cloudflare Worker use protocol version `1`.
The release-oriented `contract_version` is separate and may change without changing
the wire format.

## Message envelope

Every PJ JSON message sent by the browser includes a top-level integer `version`:

```json
{
  "version": 1,
  "message": "Example endpoint payload"
}
```

Endpoint fields remain at the top level; `version` is reserved and is removed before
endpoint-specific validation. JSON responses and server-sent events also include the
current `version`.

WebRTC SDP is not JSON, so `/session` carries the same value in the
`x-pj-protocol-version` request and response header. Messages on the `oai-events` data
channel use OpenAI's Realtime event schema and are not PJ protocol envelopes.

## Compatibility behavior

- A client sends `version: 1` on JSON requests and `x-pj-protocol-version: 1` on all
  requests.
- The Flask server and Worker return HTTP `426` with
  `error.code = "unsupported_protocol_version"` when either supplied version is not
  supported. The error includes the received and supported versions.
- The browser rejects responses whose JSON envelope or SDP response header is missing
  or unsupported, preventing a client from continuing against an incompatible server.
- During migration, servers accept requests that omit both version markers so existing
  callers continue to work. Any explicitly supplied unsupported version is rejected.
- Future additive changes may retain version `1`. Breaking field or behavior changes
  require a new protocol version and an overlap period where servers advertise every
  supported version.
