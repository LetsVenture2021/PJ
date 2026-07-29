# PJ

PJ is a Python assistant runtime built on the OpenAI Responses and Realtime
APIs. It has a terminal client, durable local tools and chat history, terminal
voice, a local browser client, and an optional Cloudflare Worker edge proxy.

## Documentation

- [End-to-end architecture](docs/architecture.md)
- [Realtime protocol compatibility](docs/realtime-protocol.md)
- [Incident response and recovery runbook](docs/runbook.md)
- [GitHub security controls](docs/security-controls.md)
- [Dependency audit report (July 28, 2026)](docs/dependency-audit-2026-07-28.md)
- [Hugging Face MCP server setup and usage](docs/huggingface-mcp-server.md)

## What works

### Text assistant and local tools

- Interactive and one-shot Responses API conversations with streamed text,
  function calls, required prompt refinement, and optional JSON Schema output.
- SQLite-backed chat history, search/resume, tasks, notes, contacts,
  commitments, decisions, risks, projects, opportunities, and generated skills.
- Local tool families for code, documents, presentations, images, strategy,
  prompts, web checks, and skill lifecycle management. Approval-sensitive tools
  are declared in `tool_policy.json`.
- OpenAI web search and configured File Search vector stores. Optional MCP
  connectors are assembled from `mcp_servers.json`; connectors with missing
  environment secrets are omitted.

### Documents, presentations, and images

- Versioned Markdown documents and immutable downloadable artifacts.
- Exports to `md`, `html`, `pdf`, `docx`, `rtf`, `xlsx`, and governed native
  `pptx`. PowerPoint exports include validation and preview generation.
- Safe local SVG creation, raster/SVG registration, asset lookup, feedback, and
  deletion with lineage retained.
- Paid OpenAI image generation with explicit enablement, budget checks, and a
  required idempotency key.

### Realtime and browser use

- Terminal microphone/speaker mode over WebRTC, with server VAD, transcription,
  interruption, tool calls, and a microphone calibration meter.
- A Flask server on port `3001` serving a loopback-only browser client and
  endpoints for Realtime signaling, Full Power Responses sessions, approvals,
  artifacts, and an authenticated local tool bridge.
- The browser offers **Fast Voice**, **Full Power Voice**, and **Full Power
  Text**, including saved conversation history and structured JSON output.
- A Cloudflare Worker proxies the same API surfaces, obtains Realtime sessions
  from OpenAI, validates Cloudflare Access identity, and bridges privileged
  tool/Responses calls to the private Flask runtime.

### Governed knowledge and integrations

- Vector-store source ingestion/synchronization into DocOps, CodeOps, and the
  governed n8n capability corpus, including release receipts and fail-closed
  validation.
- A standalone stdio Hugging Face MCP server for bounded public Hub discovery
  and token-authenticated inference. See
  [`docs/huggingface-mcp-server.md`](docs/huggingface-mcp-server.md).

## Code organization

Operation implementations live under the `ops/` package and are grouped by
domain:

- `ops/skills`, `ops/docs`, `ops/realtime`, and `ops/prompting` own the primary
  assistant workflows.
- `ops/code`, `ops/images`, `ops/presentations`, `ops/strategy`, and `ops/chief`
  contain the remaining governed operation families.
- `ops/shared` contains atomic I/O, validation, retry and logging utilities,
  provider adapters, and the protocols used between orchestration and provider
  code.

The original top-level modules remain compatibility aliases, so existing
imports and monkeypatch-based integrations continue to resolve to the same
module objects. New code should import domain APIs from `ops.*`. Provider calls
from prompting and Responses orchestration cross the `ResponsesProvider`
interface and use the OpenAI adapter in `ops/shared/providers`.

## Prerequisites and setup

- Python **3.11** (the version exercised by CI) and `pip`.
- An OpenAI API key for assistant, voice, provider image, and remote ingestion
  flows. Local deterministic tools and most tests mock provider calls.
- Node.js 20.19+ with the built-in `node:test` runner for Worker tests.
- A microphone, speakers, and PortAudio-compatible audio devices for terminal
  voice.
- A Cloudflare account, an existing DNS zone, Wrangler v4.115.0, and Cloudflare
  Access only when deploying the Worker.

The `./pj` launcher is a zsh script that specifically expects `venv/bin/python`
and `~/.env`, so use that virtual-environment name:

```bash
git clone https://github.com/LetsVenture2021/PJ.git
cd PJ
python3.11 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install the local quality tools and Git pre-commit hook:

```bash
python -m pip install -r requirements-dev.txt
npm ci
pre-commit install
```

The hook checks staged Python files with Ruff lint and format validation, and
the Worker JavaScript files with ESLint and Prettier. Run every hook against all
tracked files at any time with:

```bash
pre-commit run --all-files
```

Create `~/.env` for the launcher, or export variables in the current shell when
using `python pj.py` directly:

```bash
cat >> ~/.env <<'EOF'
OPENAI_API_KEY=your-openai-api-key
EOF
chmod 600 ~/.env
```

All runtime settings are loaded through `runtime_config.py`. Select a deployment
profile with `PJ_PROFILE=dev|staging|prod` (`dev` is the default). Staging
requires `OPENAI_API_KEY`; production also requires `PJ_OWNER_EMAILS` and
`PJ_TOOL_BRIDGE_TOKEN`. Startup fails with a configuration error when any
required value is missing.

Environment values override checked-in settings. Common overrides are
`PJ_MODEL`, `PJ_VECTOR_STORE_IDS`, `PJ_REALTIME_MODEL`, and
`PJ_REALTIME_VOICE`. Any setting can be overridden with a typed
`PJ_CONFIG__SECTION__FIELD` value, for example:

```bash
export PJ_PROFILE=staging
export PJ_CONFIG__ASSISTANT__REASONING_EFFORT='"high"'
export PJ_CONFIG__REALTIME__VOICE='"marin"'
```

`PJ_CONFIG_OVERRIDES` accepts a JSON object for multiple nested overrides.
`PJ_MCP_SERVERS_JSON` and `PJ_TOOL_POLICY_JSON` replace or extend those
respective sections. Wrangler `[vars]` and `[env.<profile>.vars]` are exposed as
the loader's `worker` section and overridden by same-named environment values.
An optional `profiles` object in `config.json` can provide per-profile assistant
overlays; selected profile values are applied before environment overrides.

Do not commit secrets. Both `.env` and runtime files (`*.sqlite3`, `state.json`,
`documents/exports/`) are ignored. Review these tracked settings before use:

- `config.json`: model, instructions, vector stores, prompt refinement, and
  built-in OpenAI tool toggles.
- `mcp_servers.json`: MCP URLs, enablement, and approval policy. Header values
  can reference `$NAME` or `${NAME}` environment variables. The checked-in
  `******` values are placeholders, not credentials.
- `tool_policy.json`: local tools that require explicit approval.
- `pj_instructions.txt`: assistant instructions.

Repository administrators should follow the
[GitHub security controls checklist](docs/security-controls.md) to verify
Dependabot, secret scanning, and push protection.

The checked-in `config.json` contains project-specific model and vector-store
IDs; access to those resources is not provisioned by this repository.

### Structured logging and redaction

Server and operation logs are emitted as one JSON object per line. Every HTTP
request receives an `x-request-id` response header; a valid
`x-pj-client-request-id` is reused, otherwise the server generates a UUID.
Request and session IDs are bound to logging context so Responses orchestration
and local tool execution events can be traced across the same lifecycle. Set
`PJ_LOG_LEVEL` to change the default `INFO` threshold.

Logs intentionally record tool names, call IDs, decisions, status codes, and
durations but not prompts, tool arguments, results, authorization headers, or
request bodies. The shared formatter recursively replaces values whose keys are
credentials, authorization/cookie fields, or end in `_api_key`, `_password`,
`_secret`, or `_token`. It also redacts bearer tokens, OpenAI-style `sk-...`
keys, and common secret assignments embedded in strings. New log fields must
follow the same metadata-only rule; redaction is defense in depth, not
permission to log payloads.

## Primary user flows

Run commands from the repository root with the virtual environment active.

### Terminal chat

```bash
# Interactive, continuing the latest local chat
./pj

# One request, then exit
./pj "Summarize my open tasks"

# Validate a structured response against the checked-in schema
./pj --json schemas/task_triage.json "Triage these tasks: ..."
```

Interactive palette keys are `/` for commands, `#` for local tools, `%` for
features/connectors, and `$` for generated skills. `/new`, `/chats`,
`/resume`, `/history`, and `/search` operate on durable SQLite history.
Feature toggles write to `config.json` or `mcp_servers.json`.

To avoid the launcher, activate the environment, export `OPENAI_API_KEY`, and
replace `./pj` with `python pj.py`.

### Image operations

```bash
./pj image status
./pj image controlled 1200 630 --title "Launch"
./pj image get ASSET_ID
./pj image feedback ASSET_ID 5 --comments "Approved"
./pj image delete ASSET_ID
```

Controlled SVG creation is local. Provider generation is fail-closed unless all
of the following are set:

```bash
export PJ_IMAGE_GENERATION_ENABLED=1
export PJ_IMAGE_BUDGET_USD=1.00
export PJ_IMAGE_ESTIMATED_CALL_USD=0.10
./pj image generate "A restrained product illustration" \
  --idempotency-key launch-illustration-v1
```

### Terminal voice

```bash
./pj voice
./pj voice --meter
./pj voice --no-gate
```

`--meter` reports suggested noise and barge-in thresholds. `--no-gate` disables
local echo suppression and is intended for headphones. Optional controls
include `PJ_REALTIME_MODEL`, `PJ_REALTIME_VOICE`, `PJ_VOICE_LANG`,
`PJ_NOISE_FLOOR_RMS`, `PJ_BARGE_IN_RMS`, `PJ_BARGE_IN_HOLD_MS`, and
`PJ_VAD_EAGERNESS`.

### Local browser and private runtime

```bash
export OPENAI_API_KEY=...
python realtime_server.py
# Open http://127.0.0.1:3001/
```

The development server binds to `127.0.0.1:3001` by default. Opening `/` creates
a same-origin loopback owner session, allowing the browser to use protected
tool and Responses routes without exposing a bearer token to JavaScript.

For a non-development process, Gunicorn is included in `requirements.txt`:

```bash
export OPENAI_API_KEY=...
export PJ_TOOL_BRIDGE_TOKEN='a-long-random-secret'
gunicorn --bind 127.0.0.1:3001 realtime_server:app
```

Keep this service private or place it behind TLS and access controls. Set
`PJ_REALTIME_BIND_HOST` only for the Flask development command; Gunicorn's
`--bind` controls its listener. The built-in HTML client is deliberately
loopback-only when `realtime_server.py` starts.

### Hugging Face MCP server

```bash
python huggingface_mcp_server.py
```

It speaks MCP over stdio. Public metadata search needs no token; inference and
authorized repositories require `HF_TOKEN`.

### Knowledge ingestion

These are operator workflows, not automatic startup tasks:

```bash
python scripts/vector_store_ingest.py SOURCE.txt \
  --corpus-type other --version 1.0.0
python scripts/vector_store_sync.py --dry-run
python scripts/vector_store_sync.py
```

Ingestion needs `OPENAI_API_KEY` and a configured vector store. Governed n8n
ingestion additionally requires an independent evaluation receipt; inspect each
script's `--help` before changing a corpus.

## Tests and quality checks

The pull-request quality gate targets `master` and also runs after pushes to
`master`. It uses Python 3.11 and Node.js 20. Run the same checks locally:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pip_audit --requirement requirements.txt
python -m pip_audit --requirement requirements-dev.txt
python -m ruff check .
python -m mypy
python -m unittest discover tests -v
npm ci
npm run lint
npm test
npm audit --audit-level=high
```

CI also runs `ruff format --check` against every Python file changed by the push
or pull request. Format a changed file before pushing with
`python -m ruff format PATH`.

The Python suite uses temporary databases and mocked provider calls. On macOS,
one CodeOps sandbox test can run with `/usr/bin/sandbox-exec`; where that
executable is absent the test explicitly skips. The Node suite imports the
Worker directly and does not require a Cloudflare account.

The July 28, 2026 dependency audit and remediation summary is tracked in
`docs/dependency-audit-2026-07-28.md`.

### Repository settings checklist

Workflow failures only block merging after branch protection is enabled:

- [ ] In **Settings > Rules > Rulesets** (or **Settings > Branches**), add a rule
      targeting `master`.
- [ ] Require a pull request before merging.
- [ ] Require status checks to pass and select **Python quality**,
      **Python tests**, and **Worker quality**.
- [ ] Require branches to be up to date before merging.

## Deployment

For production triage, recovery, and rollback, follow the
[incident runbook](docs/runbook.md).

### Cloudflare Worker

`wrangler.toml.example` is the repository's only complete deployment manifest.
It deploys API routes only; it does **not** deploy `webrtc_client.html` or
`assets/`.

1. Install and authenticate Wrangler:

   ```bash
   npm install --global wrangler@4.115.0
   wrangler login
   cp wrangler.toml.example wrangler.toml
   ```

2. Edit `wrangler.toml`:
   - set the Worker name and route domain/zone for the existing Cloudflare zone;
   - set `PJ_ALLOWED_ORIGINS`;
   - set the Cloudflare Access team domain and application audience in
     `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD`;
   - point `PJ_TOOL_BRIDGE_URL` and `PJ_TOOL_SCHEMAS_URL` at the private
     `realtime_server.py` deployment. Optionally set
     `PJ_RESPONSES_BRIDGE_URL` when it is not derivable from the tool URL.

3. In Cloudflare Zero Trust, create an Access application covering every API
   route in the manifest and an owner-only Allow policy. The Worker also checks
   the Access assertion audience and owner email; Access configuration is not
   created by Wrangler.

4. Store secrets:

   ```bash
   wrangler secret put OPENAI_API_KEY
   wrangler secret put PJ_OWNER_EMAILS
   wrangler secret put PJ_TOOL_BRIDGE_TOKEN
   ```

   `PJ_TOOL_BRIDGE_TOKEN` must exactly match the private runtime's environment.
   `PJ_OWNER_EMAILS` is a comma-separated allowlist.

5. Validate and deploy:

   ```bash
   python scripts/validate_wrangler_config.py wrangler.toml.example
   node --test tests/test_worker_auth.mjs
   ./scripts/verify_cloudflare_sync.sh wrangler.toml
   wrangler deploy
   curl https://YOUR_DOMAIN/health
   ```

   The sync validator checks the expected API routes and required `[vars]`
   bindings in the manifest, confirms required remote secret names through
   Wrangler without reading their values, and prints the Cloudflare Access
   application/policy checks that must be confirmed in Zero Trust.

Only `GET /health` and CORS preflight are public. `/session`, `/token`,
`/tool-schemas`, `/execute-tool`, and `/responses/*` require a valid Cloudflare
Access identity. Full local tools and Full Power routes also require a reachable
private runtime and matching bridge token; without it the Worker remains useful
only for its direct Realtime path and reports degraded capability in `/health`.

### Browser frontend

The repository contains the static client (`webrtc_client.html` and `assets/`)
but no Pages, static-host, container, or other frontend deployment manifest.
The Worker routes intentionally exclude `/` and `/assets/*`. Serve those files
with an existing static frontend deployment, or use the loopback Flask client;
there is no repository-backed production frontend deployment command to run.

## Known gaps and operational limits

- Python 3.11 is the only version exercised in CI; no package metadata declares
  a broader supported range.
- The `./pj` convenience launcher is zsh-specific, always sources `~/.env`, and
  hard-codes `venv/bin/python`. Use `python pj.py` on other platforms.
- The Flask server uses the development server when run directly. No container,
  system service, managed-host manifest, database migration tool, or production
  private-runtime deployment recipe is included.
- State is local: chat/tool data uses SQLite, response continuation uses
  `state.json`, and exports are filesystem artifacts. The repository does not
  provide shared storage or multi-instance coordination.
- DOCX and RTF conversion invokes macOS `textutil`; PPTX is accepted only for a
  governed presentation document.
- The current OpenAI image adapter implements generation, but binary edit and
  variation calls return explicit `*_adapter_unavailable` errors. Paid
  generation defaults to disabled and a zero-dollar budget.
- Realtime deliberately excludes approval-sensitive and long-running tools;
  Full Power Voice delegates advanced work to the text Responses runtime.
- The Worker cannot execute local tools by itself. Its bridge URLs and token
  must target a separately operated private runtime.
- SIP webhook handling exists at `POST /webhook`, but the Wrangler manifest
  does not route or deploy that endpoint and the repository contains no SIP
  provisioning artifact.
- Several entries in the checked-in MCP configuration are enabled while their
  authorization values remain placeholders; those integrations are not ready
  until valid environment-backed headers are configured.

## License

Apache License 2.0; see [LICENSE](LICENSE).
