# Incident runbook

This operational document is governed by the
[PJ document quality standard](document-quality-standard.md). Incident records,
evidence, post-incident reviews, and exports produced from this runbook must
apply that standard's applicable metadata, gates, retention, and supersession
requirements.

This runbook covers PJ's Responses and Realtime APIs, the Cloudflare Worker,
the private `realtime_server.py` runtime, local tools, and configured upstream
providers. Use it when realtime sessions, Full Power turns, or tool calls are
unavailable, slow, unsafe, or returning unexpected policy decisions.

## Operational model

- The Worker (`pj-realtime-backend`) serves the edge API. Only `GET /health`
  and CORS preflight are public; all other routes require Cloudflare Access.
- The private runtime (`pj-realtime-server`) owns Responses sessions, durable
  local tool execution, approvals, and local state. The Worker cannot execute
  local tools without its authenticated bridge to this runtime.
- Fast Voice uses OpenAI Realtime directly. Full Power Voice delegates advanced
  work to the Responses runtime. Approval-sensitive and long-running tools are
  deliberately excluded from Realtime.
- Tool policy modes are `allow`, `deny`, and `approval`. A requested approval or
  an intentional deny is not an incident. An incorrect decision, inability to
  complete an approval, or broad unexpected denial is.
- Both health endpoints can return HTTP 200 while a capability is degraded.
  On Worker health, `full_tooling_ready: false` is degraded even when
  `ok: true`.

## Severity

Assign the highest applicable severity and reassess after each mitigation.

- **SEV-1 Critical:** Unauthorized tool execution; approval or deny policy
  bypass; suspected secret exposure; unreconciled durable tool outcome that
  could cause material harm; or complete production loss with no safe
  workaround. Declare immediately. Stop affected tool execution, page the
  incident commander and relevant owners, and involve the security owner for
  access/policy exposure. Respond continuously until contained.
- **SEV-2 Major:** Most production realtime or Responses traffic fails; a
  sustained timeout or provider-error spike; the private bridge is unavailable;
  or `full_tooling_ready: false` removes a critical workflow with no acceptable
  workaround. Declare promptly, assign an incident commander, notify service
  owners, and begin mitigation. Escalate to SEV-1 if safety, authorization, or
  durable effects become uncertain.
- **SEV-3 Minor:** One surface, tool family, provider, or small user set is
  impaired and a safe workaround exists. No unauthorized or unknown side
  effect occurred. The runtime owner investigates during active support and
  records the incident. Escalate if scope grows or the workaround fails.
- **SEV-4 Advisory:** A transient, self-recovered, test-only, or near-miss event
  with no material user impact. Track it for trend analysis and planned
  remediation.

For this runbook, treat three or more matching failures in five minutes as a
spike until a service-specific baseline is established. A single policy bypass
or unknown durable side effect is SEV-1 regardless of count.

## Ownership and escalation

One person may hold several roles for a small deployment, but name them in the
incident record.

- **Incident commander (IC):** owns severity, timeline, decisions,
  communication, and the final recovery declaration. The IC should not be the
  only person making production changes during SEV-1/SEV-2.
- **Edge owner:** owns the Worker deployment, Cloudflare Access, routes,
  origins, Worker secrets, and edge logs.
- **Runtime owner:** owns the private Flask/Gunicorn process, Responses
  orchestration, SQLite/filesystem state, tool bridge, and runtime logs.
- **Provider liaison:** checks OpenAI and other configured provider status,
  quotas, rate limits, and support escalation.
- **Security owner:** must join for unauthorized access, policy bypass, leaked
  credentials, or unexpected privileged tool execution.

Escalate immediately to the runtime owner when an approval has
`approval_execution_outcome_unknown`; do **not** replay it. Escalate to the
provider liaison when failures reproduce directly at an external boundary and
local health is stable. Escalate to the edge owner when direct private-runtime
tests pass but the same Worker route fails.

## Incident-response flow

1. **Declare and scope.** Record start time in UTC, severity, IC, affected
   profile (`dev`, `staging`, or `prod`), surfaces (`/session`, `/token`,
   `/responses/*`, `/execute-tool`, terminal voice), last known-good time, and
   current Worker/private-runtime release identifiers.
2. **Make unsafe work stop.** Pause approvals and mutating tool calls when an
   outcome or policy decision is uncertain. Do not clear SQLite state, delete
   pending approvals, rotate credentials, restart services, or replay calls
   until evidence is captured unless containment requires it.
3. **Capture health from both boundaries.**

   ```bash
   curl -fsS https://YOUR_DOMAIN/health | python -m json.tool
   curl -fsS http://127.0.0.1:3001/health | python -m json.tool
   ```

   Record Worker `full_tooling_ready`,
   `tool_schema_reconciliation_status`, `tool_schema_cache_source`,
   `last_successful_reconciliation_at`, hashes, and realtime model. Record the
   private runtime's `contract_version`, `protocol_version`, `tool_count`,
   `tool_policy_sha256`, and `bridge_auth_enabled`.
4. **Correlate without collecting payloads.** Preserve the response
   `x-request-id` or structured `error.request_id`, session ID, route, status,
   error code, timestamp, and duration. Search JSON logs for that request ID
   and events such as `http.request.completed`, `session.upstream_error`,
   `token.upstream_error`, `tool.bridge_error`, `tool.execution.failed`, and
   `responses.turn.failed`. Never copy prompts, tool arguments/results,
   authorization headers, cookies, or request bodies into the incident record.
5. **Classify the boundary.** A healthy private route with a failed Worker
   route points to edge, Access, or bridge configuration. Failures at both
   boundaries point to the private runtime or provider. A single tool failure
   with otherwise healthy turns points to that tool or its provider.
6. **Stabilize with one reversible change at a time.** Record the operator,
   command/change, expected result, and rollback point. Prefer disabling an
   affected capability or using a known-good release over increasing timeouts
   or adding unbounded retries.
7. **Recover, verify, and monitor.** Follow the relevant playbook and the
   verification checklist below. The IC alone downgrades or closes the
   incident.

## Playbook: timeout spikes

### Identify

Typical signals include HTTP 504 with `openai_timeout` from the private
`/session` or `/token` routes, Worker `openai_realtime_unreachable`,
`openai_client_secret_unreachable`, or `tool_bridge_unreachable`, long
`duration_ms`, a stalled Responses stream ending in `responses_turn_failed`,
or repeated tool failures near a fixed duration.

Known timeout boundaries help identify the caller that stopped waiting:

- Worker realtime signaling: 30 seconds; client-secret minting: 12 seconds.
- Private signaling: 35 seconds; private client-secret minting: 20 seconds.
- Worker tool-schema bridge: 12 seconds.
- Normal Worker tool bridge: 85 seconds;
  `delegate_advanced_task`: 280 seconds.
- Prompt perfecting defaults to 30 seconds in `config.json`.

### Respond

1. Split failures by route, mode (Fast Voice or Full Power), tool name,
   provider, status/error code, and release. Compare edge and private-runtime
   durations for the same request ID.
2. Check CPU, memory, file descriptors, network/DNS/TLS, process saturation,
   and provider status. Confirm the private bridge is reachable from the
   Worker; loopback health alone does not prove that path.
3. Make one controlled retry with a new request ID. Do not create a retry loop:
   `/session` makes one upstream signaling request, and mutating tools may have
   completed after their caller timed out.
4. If only Full Power or tools are affected, keep Fast Voice available and
   announce that advanced/local tools are degraded. If one provider-backed tool
   is affected, stop offering that tool rather than disabling all local tools.
5. If a mutating tool timed out, inspect its durable execution/approval record.
   `approval_execution_outcome_unknown` means do not replay. Escalate for
   manual reconciliation of the external effect.
6. Drain or restart a saturated private process only after recording in-flight
   request and approval IDs. If the spike began with a deployment or config
   change, use the rollback procedure.

### Recover

Restore the failed dependency or known-good release, then make one fresh
request rather than resuming a timed-out transport. Leave unknown durable
outcomes quarantined. Do not raise timeout values until measurements prove the
normal operation legitimately exceeds the current bound and the edge,
runtime, and client budgets remain aligned.

## Playbook: provider or upstream errors

### Identify

Group by upstream and code:

- OpenAI Realtime: `openai_realtime_failed`,
  `openai_realtime_unreachable`, `openai_client_secret_failed`,
  `openai_client_secret_unreachable`, `invalid_client_secret_payload`, or
  private-runtime `openai_request_failed`.
- Private bridge: `tool_bridge_failed`, `tool_bridge_unreachable`,
  `bridge_auth_not_configured`, or `edge_tools_unavailable`.
- Tool/provider path: `tool_execution_error`, `tool.execution.failed`, an MCP
  error, or a tool result containing an error.
- Rate limiting and service faults: HTTP 429 or 5xx. Treat 400/401/403 as
  model, request, credential, Access, or configuration faults until proven
  otherwise.

### Respond

1. Verify both health endpoints and provider status. Confirm credentials are
   present, but never print them. Check quota/rate-limit metadata and recent
   model, MCP, bridge URL, origin, Access, or secret changes.
2. Reproduce with the narrowest safe read-only operation. If the private route
   works and the Worker route does not, inspect Worker-to-bridge reachability,
   `PJ_TOOL_BRIDGE_URL`, derived Responses bridge URL, and matching
   `PJ_TOOL_BRIDGE_TOKEN`.
3. For Worker Realtime, note that `REALTIME_MODEL_FALLBACK` is attempted
   automatically only for a 400 response mentioning an unsupported model. It
   is not a general outage or rate-limit fallback. Use only a model already
   validated for this release.
4. Isolate a failing MCP/provider integration or provider-backed tool while
   retaining deterministic local tools. If schema reconciliation fails, the
   Worker fails closed to no bridged realtime tools and reports
   `full_tooling_ready: false`; communicate that degraded mode explicitly.
5. For 429, reduce concurrency and wait for the provider's retry window. For
   5xx/transport faults, use bounded retries only where already implemented;
   do not layer browser, Worker, and runtime retry loops.
6. Rotate a credential only when it is invalid or exposed. Update both ends of
   the bridge together. If a release/config change caused the fault, roll back.

### Recover

Restore the upstream, credential pairing, bridge route, or validated model.
Wait for a successful tool-schema reconciliation and compare contract,
protocol, manifest, instruction, and policy hashes. A Worker health HTTP 200
without `full_tooling_ready: true` is not full recovery.

## Playbook: policy failures

### Identify

Expected policy behavior is:

- `deny`: dispatch returns `Tool '<name>' is blocked by policy (deny).`
- `approval`: Full Power emits `approval.required` and executes only after a
  trusted owner decision; model/HTTP `_approved` arguments are rejected.
- approval-sensitive and long-running tools are absent from Fast Voice.

Treat an unexpected allow/bypass as SEV-1. Treat broad unexpected denies,
missing approval prompts, approval state conflicts, repeated
`bridge_auth_required`, Cloudflare Access denials, or mismatched policy hashes
as a policy/configuration incident. Do not “fix” expected Fast Voice exclusion
by adding privileged tools to Realtime.

### Respond

1. Contain a suspected bypass by pausing approvals and tool execution. Apply a
   targeted emergency deny with `PJ_DENY_TOOLS` or a reviewed
   `tool_policy.json` change; do not change the default to `allow`.
2. Compare the effective sources: `tool_policy.json`,
   `PJ_TOOL_POLICY_PATH`, `PJ_TOOL_POLICY_JSON`, `PJ_DENY_TOOLS`, and
   `PJ_APPROVAL_TOOLS`. Validate that every mode is exactly `allow`, `deny`, or
   `approval`.
3. Compare private `tool_policy_sha256` with Worker health. Also inspect
   `tool_schema_reconciliation_status`, contract/protocol versions, Access
   owner allowlist, origins, and bridge authentication. A stale Worker schema
   cache normally refreshes within 60 seconds.
4. For a missing approval, confirm the call used Full Power and that the SSE
   stream emitted `approval.required`. Never call `/execute-tool` or inject
   `_approved` to bypass the trusted approval flow.
5. For `approval_execution_outcome_unknown`, preserve the pending record and
   reconcile the real-world effect manually. The system intentionally refuses
   unsafe replay.
6. Restart/redeploy the affected runtime after a reviewed policy/environment
   correction, then wait for successful Worker reconciliation. Roll back if
   the failure followed a release or policy change.

### Recover

Exercise one allowed read-only tool, one denied tool, and an approval tool in a
safe staging fixture. Verify the denied handler did not run, both approve and
reject paths resolve once, and hashes remain stable after cache refresh. Keep
emergency denies until the security/runtime owner approves their removal.

## Rollback procedure

Rollback is required when impact began with a deployment/config change, the
cause is still uncertain and severity is increasing, or a mitigation fails its
verification window.

1. Freeze further changes. Record the current and last known-good application
   revision, Worker deployment version, configuration sources, model values,
   and health/hash output. Preserve `pj_data.sqlite3`, chat history, artifacts,
   `state.json`, pending approvals, and execution records.
2. Roll back the Worker to the recorded known-good version:

   ```bash
   wrangler deployments list
   wrangler rollback VERSION_ID
   ```

   Reapply only the known-good Worker variables/secrets if they changed; do not
   copy secret values into the incident record.
3. Roll back the private runtime using its actual process/deployment manager to
   the same known-good repository revision and dependency set, then restart it
   gracefully. This repository has no production private-runtime deployment
   manifest, so record the environment-specific command in the incident.
4. If only checked-in configuration changed, restore only implicated files
   from the known-good revision, review the diff, and restart:

   ```bash
   git diff -- config.json mcp_servers.json tool_policy.json pj_instructions.txt
   git restore --source=KNOWN_GOOD_COMMIT -- IMPLICATED_FILE
   ```

   Restore environment overrides separately; a file rollback does not override
   `PJ_CONFIG_OVERRIDES`, `PJ_CONFIG__*`, or policy/model environment values.
5. Do not roll back or delete local data to match code. Do not replay an
   approval/tool call with an unknown outcome. Reconcile incompatible data
   forward or keep the affected capability disabled.
6. Run every post-recovery check below. If any check fails, keep the incident
   open, reapply containment, and escalate rather than repeatedly rolling
   forward and back.

## Post-recovery verification

The IC records evidence for every applicable item:

- [ ] Worker and private health respond; expected contract/protocol versions
      match. When full tooling is configured, Worker has
      `full_tooling_ready: true`, `tool_schema_reconciliation_status:
      "success"`, bridge-sourced tools, non-empty hashes, and a recent
      successful reconciliation.
- [ ] `tool_policy_sha256`, tool manifest, and instruction hashes match the
      intended release and stay stable after the 60-second schema-cache window.
- [ ] An authenticated Fast Voice session connects, transcribes, returns audio,
      and supports interruption. No approval-sensitive tool is advertised.
- [ ] A Full Power text or voice turn produces a completion without
      `responses_turn_failed`.
- [ ] A safe read-only local tool completes once and logs
      `tool.execution.started` then `tool.execution.completed` under the same
      request/session context.
- [ ] In staging or a safe fixture, deny, approval/rejection, and Access checks
      fail closed as intended. An unauthenticated privileged Worker route
      remains inaccessible.
- [ ] No pending approval or durable execution remains in an unknown state.
      Any quarantined unknown outcome has an owner and reconciliation record.
- [ ] Error rate and latency remain at baseline for at least 15 minutes and
      across both Worker and private-runtime paths.
- [ ] Containment is removed only after verification; monitoring and user
      communication reflect the restored capabilities.

After closure, create a post-incident review for SEV-1/SEV-2 and recurring
SEV-3 events. Include the UTC timeline, impact, detection gap, root and
contributing causes, rollback/mitigation results, provider evidence, and
owned/due-dated actions. Include request IDs and metadata only, never secrets or
user/tool payloads.
