---
document_id: pj.docs.product-technology-report-2026-07-28
version: 2
status: approved
template_id: product_technology_report
template_version: 1
supersedes: docs/product-technology-report-2026-07-28.md
prior_sha256: 3e8e3deb6820173bd3d9bbc9e3a62a9e32349351007674df13cbf048e8d270e4
change_note: Added provenance, review, and approval metadata; authored content is unchanged.
provenance: Repository-authored operational documentation.
reviewed_by: repository-owner
reviewed_at: 2026-07-29
approved_by: repository-owner
approved_at: 2026-07-29
---

# PJ Product and Technology Report

**Date:** July 28, 2026
**Scope:** The PJ assistant as represented by this repository and its live
deployment (pj-assistant.ai, the Cloudflare Worker API, and the private
runtime). Deployment facts below were verified directly against the running
system on the report date.

## 1. Executive summary

PJ is a personal AI assistant built on the OpenAI Responses and Realtime APIs.
One Python runtime powers four user surfaces — terminal chat, terminal voice, a
web application with three assistant modes, and a standalone Hugging Face MCP
server — while keeping all durable state (chat history, tasks, notes,
documents, artifacts) local in SQLite and the filesystem. A Cloudflare edge
(Pages, Worker, Access, and a private tunnel) publishes the web application at
pj-assistant.ai for owner-only use without exposing the runtime or its
credentials to the public internet.

The product's defining trait is governed capability: roughly 85 local function
tools spanning documents, code, images, presentations, strategy, and skills are
callable by the model, but approval-sensitive tools are declared in
`tool_policy.json`, paid features are fail-closed behind explicit budgets, and
provider calls cross narrow, auditable interfaces.

## 2. Product overview

### Assistant surfaces

| Surface | Entry point | What it offers |
| --- | --- | --- |
| Terminal chat | `./pj` / `python pj.py` | Interactive or one-shot Responses conversations with streamed text, tool calls, prompt refinement, structured JSON output, and durable history commands (`/new`, `/chats`, `/resume`, `/search`). |
| Terminal voice | `./pj voice` | Microphone/speaker WebRTC session with server VAD, transcription, interruption, tool calls, and calibration metering. |
| Web application | pj-assistant.ai or `http://127.0.0.1:3001/` | **Fast Voice** (immediate Realtime responses), **Full Power Voice** (transcripts refined before responding), and **Full Power Text** (the terminal-equivalent Responses runtime: web/file search, MCP, local tools, citations, structured output, shared history). |
| Hugging Face MCP server | `python huggingface_mcp_server.py` | Bounded public Hub discovery and token-authenticated inference over stdio MCP. |

### Web application features

- Mode switching between Fast Voice, Full Power Voice, and Full Power Text.
- Document upload from the chat composer in every mode: a **+** icon uploads
  one or more files and a **folder** icon uploads a directory tree. Selection
  uploads immediately; progress, completion (with size), and errors are
  reported as system bubbles in the conversation. Files land in DocOps under
  `documents/uploads/<session>/<upload-id>/`.
- Saved conversation history with search and resume (Full Power Text).
- Optional structured JSON output against a user-supplied schema.
- Capability status reporting (tool catalog size, model, connected MCP
  servers).

### Capability catalog

- **Local tools (~85 functions):** task/note/contact/commitment tracking,
  versioned Markdown documents with exports to `md`, `html`, `pdf`, `docx`,
  `rtf`, `xlsx`, and governed native `pptx`, safe local SVG creation and image
  asset management, code operations, strategy and prompting workflows, and
  generated-skill lifecycle management.
- **Hosted tools:** OpenAI Web Search and File Search over configured vector
  stores.
- **Remote MCP connectors:** assembled from `mcp_servers.json` (currently
  deepwiki, github, and cloudflare in the live deployment); connectors with
  missing secrets are omitted rather than half-configured.
- **Governance:** `tool_policy.json` approval gates, budget- and
  idempotency-gated paid image generation, artifact verification, bounded tool
  recursion, and fail-closed governed corpora ingestion (e.g., the n8n
  capability corpus with release receipts).

## 3. Technology architecture

### Components

```
Owner's browser ── Cloudflare Access (owner-only) ── pj-assistant.ai
    │                                                    │
    │  static app                       API routes (7)   │
    ▼                                                    ▼
Cloudflare Pages                            Cloudflare Worker
(pj-assistant-web:                          (pj-realtime-backend:
 index.html + assets)                        auth, signaling, proxy)
                                                         │
                              bridge token + WAF skip    │
                                                         ▼
                                     tools.pj-assistant.ai (cloudflared tunnel)
                                                         │
                                                         ▼
                               Private runtime on the owner's Mac
                               gunicorn → Flask (ops/realtime/server.py)
                               launchd: ai.pj.tool-runtime @ 127.0.0.1:3001
                                                         │
                             ┌───────────────────────────┼───────────────┐
                             ▼                           ▼               ▼
                     OpenAI Responses            Local tools (ops/*)  SQLite +
                     & Realtime APIs             ~85 functions        documents/
```

Voice media is an exception to the proxy path: after signaling, the browser's
WebRTC audio and `oai-events` data channel connect directly to OpenAI; neither
the Worker nor Flask stays in the media path.

### Stack

| Layer | Technology |
| --- | --- |
| Runtime | Python 3.11, Flask + Flask-CORS, gunicorn, SQLite, `ops/` domain packages with compatibility shims at the repository root |
| Provider | OpenAI Python SDK (Responses API, model `gpt-5.6-sol` in live config; Realtime `gpt-realtime-2.1`) |
| Edge API | Cloudflare Worker (vanilla JS, no framework), 7 zone routes, Cloudflare Access assertion verification |
| Frontend | Single-file static client `webrtc_client.html` + `assets/pj_web_utils.js` ES module (cache-busted via a `?v=` import query); no build step |
| Config | `runtime_config.py` profiles (`dev`/`staging`/`prod`), `config.json`, typed `PJ_CONFIG__*` environment overrides |
| Testing | Python `unittest` (mocked providers, temp DBs), Node 20 `node:test` for the Worker and browser module, Ruff, mypy, ESLint, Prettier, pip-audit, npm audit |
| Automation | launchd agents: `ai.pj.tool-runtime` (gunicorn), `ai.pj.tool-tunnel` (cloudflared), `com.pj.dbbackup` (nightly), `com.pj.vector-store-sync` |

### Data and state

All durable state is local to the runtime host: `pj_data.sqlite3` (chats,
sessions, approvals, exactly-once tool execution records, artifacts),
`state.json` (terminal continuation), `documents/` (versioned documents,
exports, uploads), and OpenAI vector stores for File Search. There is no shared
storage or multi-instance coordination by design.

## 4. Live deployment topology (verified July 28, 2026)

| Component | Live value |
| --- | --- |
| Frontend | Cloudflare Pages project `pj-assistant-web`, custom domain `pj-assistant.ai`, deployed by direct upload (`wrangler pages deploy`) |
| API | Worker `pj-realtime-backend` bound to `pj-assistant.ai/{health,session,token,tool-schemas,execute-tool,responses*,upload*}` |
| Identity | Cloudflare Access application on `pj-assistant.ai/*` (owner-only allow policy, team domain `aimhi.cloudflareaccess.com`); the Worker independently validates the Access assertion, audience, and owner email |
| Bridge | `tools.pj-assistant.ai` → cloudflared tunnel → `127.0.0.1:3001`; requests authenticated by `PJ_TOOL_BRIDGE_TOKEN` |
| Zone security | Super Bot Fight Mode plus a WAF custom rule ("PJ authenticated tool bridge bypass") that skips challenge products for the authenticated bridge paths — `/execute-tool`, `/tool-schemas`, `/health`, `/responses/*`, and `/upload/*` — and for public liveness/preflight on the apex |
| Runtime | gunicorn (1 worker, 4 threads) under launchd from a git checkout tracking the deployment branch; JSON structured logs with secret redaction in `~/Library/Logs/pj-tool-runtime-*.log` |

Public exposure is minimal: `GET /health` and CORS preflight are the only
unauthenticated API responses; everything else requires an Access identity, and
upload/tool/Full Power routes additionally require the private runtime to be
reachable with a matching bridge token.

## 5. Security model

Defense in layers, each independently enforced:

1. **Cloudflare Access** gates every page and API route on the apex domain.
2. **The Worker re-verifies** the Access JWT assertion, application audience,
   owner email allowlist, origin allowlist, and PJ protocol version before
   proxying anything.
3. **The bridge** requires a bearer token known only to the Worker and the
   runtime; the browser never receives it.
4. **The local web client** works only for loopback requests via an HTTP-only,
   same-origin owner session (`PJ_LOCAL_WEB_OWNER_SESSION_ENABLED=1`).
5. **The runtime** enforces per-tool approval policy, upload size caps,
   protocol version checks, and request-scoped structured logging that records
   metadata but never prompts, tool arguments, bodies, or credentials.
6. **Fail-closed defaults**: paid image generation, governed corpora, and MCP
   connectors with missing secrets are disabled rather than degraded.

## 6. Quality engineering

- CI quality gate on `master` (GitHub Actions): Ruff lint/format, mypy, Python
  unit tests, pip-audit, ESLint, Prettier, Worker tests, npm audit.
- 30 Node tests cover Worker auth, the upload proxy, and the browser module's
  initialization contract; the Python suite uses mocked providers and
  temporary databases.
- Pre-commit hooks mirror CI checks on staged files.
- A dependency audit is tracked in `docs/dependency-audit-2026-07-28.md`.

## 7. Changes shipped July 28, 2026

- **Composer uploads in all modes:** + (files) and folder icons in the chat
  box across Fast Voice, Full Power Voice, and Full Power Text; immediate
  upload with progress/completion/error reported in the conversation; the
  former sidebar "Upload documents" panel was removed.
- **Cache-safe module loading:** the client's ES module import is versioned
  (`?v=`) after a stale-cache pairing of new HTML with an old module disabled
  every event listener on the page.
- **Worker and route sync:** the deployed Worker was upgraded to the current
  repository code (it predated upload support) and the missing
  `pj-assistant.ai/upload*` route was added.
- **WAF fix for uploads:** `/upload/*` was added to the bridge
  challenge-skip rule; uploads previously died at a bot challenge the Worker
  cannot answer, which presented as "uploading" with no result.
- **Runtime upgrade:** the serving checkout was moved from a 115-commit-old
  branch to current code (gaining the upload endpoints and Full Power
  improvements), with a database snapshot taken before the switch.

## 8. Known gaps and risks

- **Deployment drift is the systemic risk.** The frontend, Worker, WAF rule,
  and runtime checkout are each deployed ad hoc, and all four were found stale
  or misaligned on the report date. There is no single manifest or script that
  deploys the full surface; recreating today's alignment depends on operator
  discipline.
- The feature branch carrying today's changes is not yet merged to `master`;
  deployments currently depend on an unmerged branch.
- `www.pj-assistant.ai` is not a domain of the Pages project; only the apex is
  canonical.
- The runtime is single-instance and macOS-hosted (launchd, `textutil` for
  DOCX/RTF); there is no container or managed-host recipe.
- Python 3.11 is the only CI-exercised version; the `./pj` launcher is
  zsh-specific.
- Inbound SIP and the legacy `POST /webhook` handler remain intentionally
  unsupported and undeployed.
- Some checked-in MCP connector entries are enabled with placeholder
  credentials and stay inert until real secrets are configured.

## 9. Recommendations

1. Merge the current feature branch to `master` so deployed artifacts trace to
   the default branch.
2. Script the full deployment (Pages upload, Worker deploy, WAF rule
   assertion, runtime checkout sync) so the four surfaces cannot drift
   silently; extend `scripts/verify_cloudflare_sync.sh` to assert the WAF
   skip-rule paths alongside Worker routes.
3. Add `/upload/*` coverage to the runbook's health triage, mirroring today's
   diagnosis path (access log → bridge test → WAF rule).
4. Consider binding `www.pj-assistant.ai` to the Pages project or redirecting
   it at the zone level.
