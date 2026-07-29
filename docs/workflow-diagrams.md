# Current application workflow diagrams

This document maps every current PJ feature described by the application and
its operation registries. The diagrams show user-visible workflows rather than
individual implementation helpers. A feature is **governed** when policy,
validation, approval, budget, or integrity checks can stop it before a side
effect occurs.

## Feature coverage

| Feature group | Included workflows |
| --- | --- |
| Entry surfaces | Terminal interactive and one-shot chat, structured JSON, terminal voice, browser Fast Voice, Full Power Voice, and Full Power Text |
| Conversation state | Streamed Responses turns, continuation, chat creation/list/search/resume, transcript persistence, citations, sources, and artifacts |
| Personal operations | Tasks, notes, semantic memory, contacts, calendar, reminders, email drafts, projects, commitments, opportunities, decisions, risks, daily briefs, and macOS Shortcuts |
| Governed tools | Local dispatch, realtime filtering, approvals, exact-once execution, timeouts, and artifact verification |
| Documents and uploads | Templates, draft/revise/finalize, immutable versions, upload screening, safe extraction, exports, downloads, and automatic vectorization |
| Presentations | Governed presentation specs, revision, native PPTX rendering, validation, and preview generation |
| Images and vision | Controlled SVG, registration, lookup, feedback, deletion, uploaded-image analysis, paid generation, and generated-image persistence |
| Code and delegation | CodeOps tasks, repository inspection/search/read, evidence, validation, guidance, completion, read-only Codex analysis, governed Codex delegation, and hosted shell artifacts |
| Skills and strategy | Pattern observation, generated-skill lifecycle and telemetry review, goal contracts, evidence bundles, timeline replay, and weekly reviews |
| Knowledge and research | Web Search, File Search, MCP connectors, deep research, vector-store ingestion/sync, n8n corpus governance, PJ knowledge MCP, and Hugging Face MCP |
| Utility and integration tools | Current time, capability snapshot, active browser tab, URL fetch and website health, generated-site deployment, and governed environment-file helpers |
| Browser edge | Loopback owner sessions, Cloudflare Access, schema reconciliation, signaling fallback, local-tool bridging, uploads, and health reporting |
| Configuration and operations | Typed profiles and overrides, fail-closed startup, structured redacted logging, request correlation, process mining, backup, deployment validation, and incident recovery |

## 1. Entry surface and mode selection

```mermaid
flowchart TD
    U([User]) --> E{Choose entry surface}
    E -->|Terminal prompt| T{Interaction type}
    T -->|Interactive or one-shot| R[Responses conversation]
    T -->|--json schema| J[Responses conversation plus schema validation]
    T -->|image subcommand| I[Image operations]
    T -->|voice or voice meter| V[Terminal Realtime voice]
    E -->|Browser| B{Choose mode}
    B --> FV[Fast Voice]
    B --> FPV[Full Power Voice]
    B --> FPT[Full Power Text]
    E -->|MCP client| M{Choose stdio server}
    M --> PM[PJ knowledge MCP]
    M --> HM[Hugging Face MCP]
    R --> O[Responses orchestrator]
    J --> O
    FV --> RT[OpenAI Realtime]
    FPV --> RT
    FPV --> O
    FPT --> O
```

## 2. Responses conversation and durable history

```mermaid
flowchart TD
    P[User prompt] --> C{Surface}
    C -->|Terminal| TS[Load continuation from state.json]
    C -->|Browser Full Power| BS[Create or resume SQLite chat session]
    TS --> PP{Prompt refinement enabled?}
    BS --> L[Acquire exclusive turn lease]
    L --> PP
    PP -->|Yes| PR[Refine prompt and retain original metadata]
    PP -->|No| REQ[Build provider request]
    PR --> REQ
    REQ --> TOOLS[Add local schemas, hosted tools, vector stores, and ready MCP connectors]
    TOOLS --> STREAM[Stream text and lifecycle events]
    STREAM --> CALL{Tool or approval event?}
    CALL -->|No| FINAL[Collect final text, citations, sources, and response ID]
    CALL -->|Tool| GOV[Governed tool workflow]
    CALL -->|MCP approval| AP[Pause for explicit owner decision]
    GOV --> OUT[Submit function output against preceding response]
    AP --> OUT
    OUT --> STREAM
    FINAL --> SAVE{Surface}
    SAVE -->|Terminal| ST[Atomically update state.json]
    SAVE -->|Browser| DB[Persist messages and checkpoint; release lease]
    ST --> DONE([Render result])
    DB --> DONE
```

Browser history commands and APIs follow the same state model:

```mermaid
flowchart LR
    A[Create session] --> B[Append turns and messages]
    B --> C[Store last response ID and artifacts]
    C --> D{Later action}
    D -->|List| E[Show recent chats]
    D -->|Search| F[Search durable history]
    D -->|Resume| G[Restore session continuation]
    G --> B
```

## 3. Governed local tool execution

```mermaid
flowchart TD
    FC[Model emits function call] --> V[Validate tool name and arguments]
    V --> KNOWN{Registered tool?}
    KNOWN -->|No| ERR[Return bounded error]
    KNOWN -->|Yes| MODE{Realtime request?}
    MODE -->|Yes| SAFE{Realtime-safe and short-running?}
    SAFE -->|No| ADV[Direct user to Full Power or bounded delegation]
    SAFE -->|Yes| POLICY
    MODE -->|No| POLICY{Approval-sensitive?}
    POLICY -->|Yes| PAUSE[Persist pending approval and pause]
    PAUSE --> DECIDE{Owner decision}
    DECIDE -->|Reject| DENY[Record rejection]
    DECIDE -->|Approve| RESERVE[Reserve exact-once execution]
    POLICY -->|No| RESERVE
    RESERVE --> DUP{Known completed call?}
    DUP -->|Yes| REPLAY[Return recorded outcome]
    DUP -->|No| RUN[Dispatch with configured timeout]
    RUN --> RESULT{Result type}
    RESULT -->|Artifact| VERIFY[Verify path, hash, and lineage; link to session]
    RESULT -->|JSON| RECORD[Record metadata and completion]
    VERIFY --> RECORD
    RECORD --> RETURN[Return function output]
```

The assistant never trusts a model-supplied `_approved` argument. Approval is
server-side state, and Realtime schemas exclude approval-sensitive and
long-running tools.

## 4. Personal operations and executive workflow

```mermaid
flowchart TD
    INPUT[Conversation or explicit tool call] --> KIND{Operation}
    KIND -->|Task| TASK[Add, list, prioritize, or complete task]
    KIND -->|Note| NOTE[Save or keyword-search durable note]
    KIND -->|Memory| MEM[Semantic search or cluster memories]
    KIND -->|Relationship| CONTACT[Log or search contact interactions]
    KIND -->|Calendar or reminder| APPLE[Read or create through macOS apps]
    KIND -->|Email| MAIL[List recent mail or create review-only draft]
    KIND -->|Project| PROJECT[Create, update, or portfolio review]
    KIND -->|Commitment| COMMIT[Log, list with overdue flags, or complete]
    KIND -->|Opportunity| PIPE[Log, update, or review weighted pipeline]
    KIND -->|Decision or risk| GOV[Journal decision or register risk]
    KIND -->|Shortcut| SHORT[Run named macOS Shortcut]
    TASK --> DB[(Local SQLite)]
    NOTE --> DB
    MEM --> DB
    CONTACT --> DB
    PROJECT --> DB
    COMMIT --> DB
    PIPE --> DB
    GOV --> DB
    DB --> BRIEF[Daily brief combines tasks, commitments, pipeline, portfolio, risks]
    APPLE --> BRIEF
    BRIEF --> USER([Executive summary])
```

## 5. Upload, extraction, and download workflow

```mermaid
flowchart TD
    PICK[Choose files with plus icon or directory with folder icon] --> UP[Upload immediately]
    UP --> EACH{For each file}
    EACH --> NAME{Executable or credential-shaped name?}
    NAME -->|Yes| SKIP[Skip file and report reason]
    NAME -->|No| ACCEPT[Accept broadly and register immutable artifact]
    ACCEPT --> TYPE{Explicit text-like parse allowlist?}
    TYPE -->|Normal text-like| PARSE[Bounded extraction and sanitized Markdown preview]
    TYPE -->|ML weights| HEADER[Read headers only]
    TYPE -->|Pickle-family checkpoint| NOPICKLE[Never deserialize; artifact only]
    TYPE -->|Other binary| BINARY[Artifact only]
    PARSE --> INDEX[Make upload available to tools and chat]
    HEADER --> INDEX
    NOPICKLE --> INDEX
    BINARY --> INDEX
    SKIP --> MORE{More files?}
    INDEX --> MORE
    MORE -->|Yes| EACH
    MORE -->|No| STATUS[Show progress, completion, and per-file errors]
    STATUS --> DL[Integrity-checked artifact download]
```

## 6. Governed document workflow

```mermaid
flowchart TD
    START[Document request] --> TEMPLATE{Template exists?}
    TEMPLATE -->|No| CREATE[Create versioned template with required sections]
    TEMPLATE -->|Yes| DRAFT
    CREATE --> DRAFT[Draft document version]
    DRAFT --> REVIEW[Review content, facts, and verification markers]
    REVIEW --> CHANGE{Changes required?}
    CHANGE -->|Yes| REVISE[Create immutable revised version with hash and change note]
    REVISE --> REVIEW
    CHANGE -->|No| GATE{Unresolved facts or markers?}
    GATE -->|Yes| BLOCK[Block finalization and return issues]
    BLOCK --> REVISE
    GATE -->|No| FINAL[Seal approved version FINAL]
    FINAL --> FORMAT{Export format}
    FORMAT --> MD[Markdown]
    FORMAT --> HTML[HTML or PDF]
    FORMAT --> OFFICE[DOCX, RTF, or XLSX]
    FORMAT --> PPTX[Governed native PPTX]
    MD --> ART[Immutable downloadable artifact]
    HTML --> ART
    OFFICE --> ART
    PPTX --> ART
    ART --> VECTOR[Quiet content-hash-deduplicated owner-store vectorization]
```

DOCX and RTF conversion depends on macOS `textutil`; PPTX export is allowed
only for a governed presentation document.

## 7. Presentation workflow

```mermaid
flowchart TD
    ASK[Presentation request] --> SPEC[Draft governed slide specification]
    SPEC --> VALIDATE[Validate supported layouts and required content]
    VALIDATE --> OK{Valid?}
    OK -->|No| FIX[Return actionable validation issues]
    FIX --> REV[Revise presentation into a new document version]
    REV --> VALIDATE
    OK -->|Yes| REVIEW[Review narrative and slide data]
    REVIEW --> CHANGES{Revise?}
    CHANGES -->|Yes| REV
    CHANGES -->|No| FINAL[Finalize governed document]
    FINAL --> RENDER[Render native PPTX with brand system]
    RENDER --> QA[Reopen and validate package and slide structure]
    QA --> PREVIEW[Generate preview]
    PREVIEW --> ART[Register immutable PPTX and preview artifacts]
```

## 8. Image, asset, and vision workflow

```mermaid
flowchart TD
    REQUEST[Image request] --> PATH{Capability}
    PATH -->|Controlled local design| SVG[Create safe controlled SVG]
    PATH -->|Existing raster or SVG| REG[Validate and register asset]
    PATH -->|Uploaded raster analysis| VISION[Send image with original detail to vision model]
    PATH -->|Provider generation| ENABLE{Explicitly enabled?}
    ENABLE -->|No| CLOSED[Fail closed]
    ENABLE -->|Yes| KEY{Idempotency key present?}
    KEY -->|No| CLOSED
    KEY -->|Yes| BUDGET{Budget permits estimated call?}
    BUDGET -->|No| CLOSED
    BUDGET -->|Yes| GEN[Generate through provider adapter]
    GEN --> PERSIST[Persist bytes, integrity metadata, and revised prompt]
    SVG --> CATALOG[(Asset catalog and lineage)]
    REG --> CATALOG
    PERSIST --> CATALOG
    VISION --> ANSWER[Return analysis]
    CATALOG --> ACTION{Asset action}
    ACTION -->|Get or find| VIEW[Return metadata and download]
    ACTION -->|Feedback| RATE[Record rating and comments]
    ACTION -->|Delete| DELETE[Remove active asset while retaining lineage]
```

The current provider adapter supports generation. Binary edit and variation
requests return explicit unavailable errors rather than silently substituting
another operation.

## 9. CodeOps, Codex delegation, and hosted compute

```mermaid
flowchart TD
    CODE[Code-related request] --> ROUTE{Scope}
    ROUTE -->|Read-only analysis| AUTO[Automatically route to codex_analyze]
    ROUTE -->|Repository workflow| TASK[Create CodeOps task]
    ROUTE -->|Agentic change| APPROVAL[Require approval for run_codex_task]
    TASK --> APPROVE[Approve task scope]
    APPROVE --> INSPECT[Inspect, search, and read bounded repository paths]
    INSPECT --> GUIDE[Import, list, or retrieve coding guidance]
    GUIDE --> EVIDENCE[Collect Git evidence]
    EVIDENCE --> VALIDATE[Run allowlisted validation in sandbox where supported]
    VALIDATE --> COMPLETE[Record completion and evidence]
    APPROVAL -->|Approved and daily limit available| CODEX[Run bounded Codex task]
    APPROVAL -->|Rejected or capped| STOP[Do not execute]
    AUTO --> RESULT[Return analysis]
    CODEX --> RESULT
    ROUTE -->|Hosted shell enabled| SHELL[Run command in OpenAI-managed container without network]
    SHELL --> FETCH[Fetch selected /mnt/data outputs]
    FETCH --> ART[Register durable artifacts]
```

## 10. Skill lifecycle and strategic governance

```mermaid
flowchart TD
    WORK[Recurring workflow or friction] --> OBS[Observe pattern]
    OBS --> LIST[List and assess observations]
    LIST --> CAND[Create candidate skill with parameters and validation]
    CAND --> TEST{Optional smoke test passes?}
    TEST -->|No| REVISE[Revise candidate]
    REVISE --> TEST
    TEST -->|Yes| ACT[Activate skill]
    ACT --> USE[Dispatch generated skill]
    USE --> TELEMETRY[Record calls, failures, and latency]
    TELEMETRY --> REVIEW[Review skill portfolio]
    REVIEW --> DECIDE{Recommendation}
    DECIDE -->|Maintain or optimize| ACT
    DECIDE -->|Pause or retire| DEP[Deprecate skill]
    DEP -->|Restore after revision| ACT
```

```mermaid
flowchart LR
    GOAL[Create goal contract] --> UPDATE[Update status and measures]
    UPDATE --> EVIDENCE[Build evidence bundle]
    EVIDENCE --> REPLAY[Replay timeline]
    REPLAY --> WEEKLY[Weekly operating review]
    WEEKLY --> UPDATE
```

## 11. Knowledge, hosted tools, MCP, and research

```mermaid
flowchart TD
    Q[Knowledge request] --> SOURCE{Source}
    SOURCE -->|Current public web| WEB[OpenAI Web Search]
    SOURCE -->|Configured private corpus| FILE[OpenAI File Search]
    SOURCE -->|Configured connector| MCP{Secrets resolved and connector enabled?}
    MCP -->|No| OMIT[Omit connector]
    MCP -->|Yes| REMOTE[Provider-side remote MCP call]
    REMOTE --> NEED{Approval requested?}
    NEED -->|Yes| OWNER[Durable owner approval flow]
    NEED -->|No| MERGE
    OWNER --> MERGE[Merge tool evidence]
    WEB --> MERGE
    FILE --> MERGE
    SOURCE -->|Long-running research| DEEP[Start background deep research]
    DEEP --> POLL[Retrieve research status or result]
    POLL --> MERGE
    MERGE --> ANSWER[Answer with citations and sources]
```

Operator-managed knowledge ingestion is deliberately separate from startup:

```mermaid
flowchart TD
    SRC[Source file, repository guidance, or public HF dataset] --> BANNER[Mark external corpus as untrusted data]
    BANNER --> PREP[Normalize and validate corpus metadata]
    PREP --> TYPE{Governed n8n corpus?}
    TYPE -->|Yes| RECEIPT{Independent evaluation receipt valid?}
    RECEIPT -->|No| FAIL[Fail closed]
    RECEIPT -->|Yes| INGEST
    TYPE -->|No| INGEST[Upload to explicitly selected vector store]
    INGEST --> SYNC[Sync source inventory and content hashes]
    SYNC --> STATUS[Report sync status and capabilities]
```

The standalone stdio servers follow bounded, read-oriented paths:

```mermaid
flowchart LR
    CLIENT[MCP client] --> WHICH{Server}
    WHICH -->|PJ knowledge| PJ[Search or fetch notes, tasks, and uploads; list open tasks]
    PJ --> SEM[Semantic embeddings with keyword fallback]
    WHICH -->|Hugging Face| HF[Public Hub discovery]
    HF --> AUTH{Inference or private repository?}
    AUTH -->|Yes| TOKEN[Require process HF_TOKEN]
    AUTH -->|No| RESULT[Return bounded metadata]
    TOKEN --> RESULT
```

## 12. Utility and integration tools

```mermaid
flowchart TD
    ASK[Utility request] --> TYPE{Operation}
    TYPE -->|Time| TIME[Return current local time]
    TYPE -->|Capability discovery| CAPS[Build current PJ capability snapshot]
    TYPE -->|Browser context| TAB[Read active browser tab where supported]
    TYPE -->|URL content| FETCH[Validate URL and fetch bounded visible text]
    TYPE -->|Website status| CHECK[Separate reachability, app health, and Access login state]
    TYPE -->|Generated site| SITE[Validate and deploy generated site artifact]
    TYPE -->|Environment setup| ENV{Requested action}
    ENV -->|Placeholder| PLACE[Approval-gated insertion of secret placeholder only]
    ENV -->|Edit| EDIT[Approval-gated opening of local environment file]
    PLACE --> SECRET[User supplies value outside the assistant]
    EDIT --> SECRET
    FETCH --> SAFE[Return sanitized, bounded result]
    CHECK --> SAFE
```

An HTTP 200 Cloudflare Access login page is reported as gated content, not as
proof that the protected application is healthy. Environment secret values
never pass through the assistant.

## 13. Realtime voice workflows

### Terminal voice

```mermaid
flowchart TD
    START[Run voice] --> METER{Calibration meter?}
    METER -->|Yes| CAL[Measure ambient and speech RMS; suggest noise and barge-in thresholds]
    METER -->|No| AUDIO[Open microphone and speaker]
    CAL --> AUDIO
    AUDIO --> RTC[Establish Realtime WebRTC session]
    RTC --> LISTEN[Server VAD and transcription]
    LISTEN --> SPEAK[Stream assistant audio]
    SPEAK --> INTERRUPT{User speech exceeds barge-in threshold?}
    INTERRUPT -->|Yes| CUT[Cancel playback and response]
    CUT --> LISTEN
    INTERRUPT -->|No| LISTEN
```

### Browser modes

```mermaid
flowchart TD
    MODE{Browser mode} -->|Fast Voice| FAST[Server VAD automatically creates responses]
    MODE -->|Full Power Voice| VOICE[Transcribe completed speech]
    MODE -->|Full Power Text| TEXT[Submit typed durable Responses turn]
    VOICE --> PERFECT[Prompt-perfect while retaining original transcript and metadata]
    PERFECT --> REPLACE[Replace Realtime item with refined text]
    REPLACE --> CREATE[Explicitly request response]
    FAST --> EVENTS[Receive audio, transcript, and tool events]
    CREATE --> EVENTS
    EVENTS --> TOOL{Realtime-safe function call?}
    TOOL -->|Yes| BRIDGE[Execute through HTTP tool bridge]
    BRIDGE --> OUTPUT[Send function output and request continuation]
    OUTPUT --> EVENTS
    TOOL -->|No| SAVE[Persist finalized transcript idempotently]
    TEXT --> SSE[Stream SSE text, tools, approvals, citations, sources, and artifacts]
    SSE --> SAVE
```

## 14. Browser, private runtime, and Cloudflare edge

```mermaid
flowchart TD
    B[Browser request] --> DEPLOY{Deployment}
    DEPLOY -->|Loopback| OWNER[GET / creates same-origin HTTP-only owner session]
    OWNER --> FLASK[Protected Flask route]
    DEPLOY -->|Edge| PUBLIC{Public route?}
    PUBLIC -->|GET /health or preflight| WORKER[Cloudflare Worker]
    PUBLIC -->|No| ACCESS[Validate Access JWT, audience, expiry, and owner email]
    ACCESS -->|Invalid| DENY[Reject]
    ACCESS -->|Valid| WORKER
    WORKER --> ROUTE{Route}
    ROUTE -->|session or token| SCHEMA[Fetch and reconcile private schemas, instructions, contract, and hashes]
    SCHEMA --> MATCH{Contract and hashes match?}
    MATCH -->|No| TOOLLESS[Create tool-less session rather than trust stale schemas]
    MATCH -->|Yes| OPENAI[Create OpenAI Realtime call or ephemeral secret]
    ROUTE -->|execute-tool, responses, upload| BRIDGE[Allowlist request and replace credentials with bridge headers]
    BRIDGE --> FLASK
    ROUTE -->|health| HEALTH[Report direct Realtime, bridge, and schema readiness]
    FLASK --> STATE[(SQLite and artifact storage)]
```

After signaling, WebRTC audio and the `oai-events` data channel connect
directly between the browser and OpenAI. HTTP tool calls, Full Power turns,
uploads, artifacts, and transcript persistence continue through the selected
API base. `POST /webhook` is unsupported, local-only legacy code and is not an
edge route.

### Signaling recovery

```mermaid
flowchart TD
    SDP[POST SDP to session endpoint] --> RESULT{Result}
    RESULT -->|Success| CONNECT[Apply answer and connect]
    RESULT -->|Recognized SDP rejection or server error| TOKEN[Request ephemeral client secret]
    RESULT -->|Other 4xx| FAIL[Fail immediately]
    TOKEN --> DIRECT[Post SDP directly to OpenAI]
    DIRECT --> FALLBACK{Success?}
    FALLBACK -->|Yes| CONNECT
    FALLBACK -->|No| CLEAN[Show error, close peer connection, stop microphone, return idle]
```

## 15. Configuration, observability, and operations

```mermaid
flowchart TD
    START[Process startup] --> BASE[Load config.json, instructions, MCP, policy, and Worker manifest]
    BASE --> PROFILE[Apply dev, staging, or prod profile overlay]
    PROFILE --> SHORT[Apply shorthand environment settings]
    SHORT --> JSON[Apply PJ_CONFIG_OVERRIDES]
    JSON --> TYPED[Apply typed PJ_CONFIG__SECTION__FIELD overrides]
    TYPED --> VALIDATE{Required profile values and types valid?}
    VALIDATE -->|No| CLOSED[Fail closed with configuration error]
    VALIDATE -->|Yes| SNAPSHOT[Expose immutable runtime configuration snapshot]
    SNAPSHOT --> RUN[Start selected runtime surface]
```

```mermaid
flowchart TD
    REQ[HTTP request or runtime operation] --> RID[Reuse valid client request ID or create UUID]
    RID --> CONTEXT[Bind request and session IDs to logging context]
    CONTEXT --> WORK[Perform operation]
    WORK --> LOG[Emit one metadata-only JSON object per line]
    LOG --> REDACT[Recursively redact credentials, headers, tokens, keys, and secret-shaped strings]
    REDACT --> RESP[Return bounded error or result with x-request-id]
    RESP --> INCIDENT{Operational issue?}
    INCIDENT -->|No| DONE([Complete])
    INCIDENT -->|Yes| RUNBOOK[Diagnose health, bridge, schema, provider, storage, and logs]
    RUNBOOK --> BACKUP[Back up local SQLite and artifacts before destructive recovery]
    BACKUP --> RECOVER[Apply recovery or rollback and verify health]
```

Deployment validation checks the Worker route allowlist, required variables,
declared remote secret names, schema synchronization, Cloudflare Access setup,
and private bridge readiness without reading secret values.

## 16. Process-mining optimization loop

The workflow diagrams above are the intended process model. PJ's process miner
uses the metadata-only structured log as an event log to discover how the
features behave in practice, compare common variants, and prioritize changes.
It never ingests prompts, arguments, results, bodies, authorization headers, or
secret values.

```mermaid
flowchart TD
    RUN[Users exercise terminal, browser, tool, upload, and Responses workflows] --> LOG[Metadata-only JSONL lifecycle events]
    LOG --> FILTER[Allowlist fixed activity names and correlation/timing/status fields]
    FILTER --> CASE[Correlate cases by request, upload, tool-call, then session ID]
    CASE --> DISCOVER[Discover variants and directly-follows transitions]
    DISCOVER --> MEASURE[Measure failure rate, mean and p95 latency, incomplete cases, and replays]
    MEASURE --> PRIORITY{Optimization signal}
    PRIORITY -->|Failure or incomplete case| RELIABILITY[High: stabilize and guarantee terminal outcomes]
    PRIORITY -->|High p95 latency| PERFORMANCE[Medium: remove waits and duplicate work]
    PRIORITY -->|Replay activity| RETRY[Low: retain exact-once protection and inspect retry source]
    RELIABILITY --> CHANGE[Change one governed workflow step]
    PERFORMANCE --> CHANGE
    RETRY --> CHANGE
    CHANGE --> TEST[Run CI and controlled rollout]
    TEST --> RUN
```

### Optimization targets by feature

| Feature workflow | Process-mining signal | Safe optimization | Guardrail retained |
| --- | --- | --- | --- |
| Responses and history | Continuation rejection, failed turns, incomplete cases | Validate continuation earlier and ensure leases/checkpoints close on every terminal path | Durable continuation and exclusive turn lease |
| Local tools | Tool failure rate, p95 duration, replay frequency | Fix the highest-failure tool first; profile slow dispatch; remove duplicate upstream submissions | Approval policy, timeout, and exact-once replay |
| Uploads | Rejection/failure variants and request p95 | Improve preflight feedback and isolate slow scanner/extraction stages | Per-file skip, credential/executable refusal, narrow parsing |
| Documents and presentations | Export tool p95 and failure variants | Cache deterministic intermediate rendering and surface validation issues before conversion | Immutable versions, finalization gate, and artifact verification |
| Images | Generation failures and long-tail duration | Fail faster on enablement, idempotency, and budget checks before provider work | Paid-generation gate and lineage |
| Code and delegation | Validation/delegation failures and latency | Prefer read-only analysis, narrow task scope, and run cheap validation before expensive checks | Approval, daily cap, sandbox, and network restrictions |
| Skills and strategy | Repeated skill failures and uncommon variants | Deprecate unreliable generated skills and revise candidates from observed telemetry | Candidate lifecycle and human review |
| Knowledge and MCP | Connector omission, approval loops, provider latency | Report unavailable connectors early and favor ready sources while preserving citations | Secret resolution and MCP approval |
| Realtime and edge | Signaling fallback variants, bridge failures, request p95 | Target the dominant failing boundary and avoid unnecessary fallback attempts | Access checks, schema reconciliation, and bounded recovery |

Run the miner against an operator-provided structured log:

```bash
python scripts/process_mining.py /path/to/pj.jsonl --output process-report.json
```

Optimization is evidence-driven: establish a baseline, change one step, rerun
the same checks, and compare the next report. The miner deliberately recommends
no automatic policy relaxation, side-effect replay, secret collection, or
payload logging.
