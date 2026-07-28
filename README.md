# PJ

PJ is a Python-first AI operations platform for building and running specialized “ops” workflows (code, docs, images, prompts, chat logs, strategy, presentations, and skills), with realtime voice/chat interfaces and optional MCP/tool integrations.

This repository appears to power an orchestrated assistant runtime where:
- Core behavior is implemented in Python modules (`*ops.py`, runtime/server files).
- Realtime/web interaction is supported through WebRTC + backend worker components.
- Configuration and policy are driven by JSON/TOML/text instruction assets.
- Tests, schemas, scripts, and generated skills support development and runtime behavior.

---

## Repository at a glance

**Repo:** `LetsVenture2021/PJ`  
**Description:** PJ project  
**Primary language:** Python (86.2%)  
**Secondary languages:** JavaScript, HTML, Shell

---

## Project structure

```text
.github/
  workflows/
    ci.yml                       # CI workflow

assets/                          # Static assets used by UI/runtime

docs/                            # Documentation files
documents/                       # Content/document sources

generated_skills/                # Auto/generated skill artifacts
schemas/                         # JSON/schema contracts
scripts/                         # Utility and automation scripts
tests/                           # Test suite

chatlog.py                       # Chat log handling + related ops
chiefops.py                      # Orchestration/“chief” operations logic
codeops.py                       # Code-focused operations
docops.py                        # Document-focused operations
imageops.py                      # Image-focused operations
presentationops.py               # Presentation-focused operations
promptops.py                     # Prompt-focused operations
skillops.py                      # Skill management/generation orchestration
strategyops.py                   # Strategy/planning operations
skills.py                        # Skill model/registry helpers

pj.py                            # Main Python entrypoint (CLI/runtime)
pj                               # Shell launcher/helper script
pj_contract.py                   # Contract/interface definitions
pj_instructions.txt              # System instruction corpus/config text

responses_runtime.py             # Response/runtime execution layer
realtime_server.py               # Realtime server (Python)
realtime_config.py               # Realtime runtime config
pj_realtime_backend_worker.js    # Realtime backend worker (JavaScript)

voice.py                         # Voice processing/runtime
webrtc_client.html               # WebRTC browser client UI

huggingface_mcp_server.py        # MCP server integration (Hugging Face)
mcp_servers.json                 # MCP server registry/config
tool_policy.json                 # Tool usage policy config

config.json                      # Main app/runtime configuration
requirements.txt                 # Python dependencies
wrangler.toml.example            # Cloudflare Worker/Wrangler example config
.gitignore                       # Git ignore rules
```

---

## Core architecture

PJ is organized around **domain-specific ops modules**:
- `codeops.py`, `docops.py`, `imageops.py`, `presentationops.py`, `promptops.py`, `strategyops.py`
- `skillops.py` + `skills.py` for skill lifecycle and orchestration
- `chiefops.py` as high-level coordinator

Runtime and interfaces are layered on top:
- `responses_runtime.py` for response orchestration
- `realtime_server.py` + `realtime_config.py` for realtime services
- `pj_realtime_backend_worker.js` and `webrtc_client.html` for web realtime interaction
- `voice.py` for voice-specific workflows

Configuration and guardrails are centralized in:
- `config.json`
- `tool_policy.json`
- `mcp_servers.json`
- `pj_instructions.txt`
- `wrangler.toml.example` (deploy/runtime example)

---

## Prerequisites

- Python 3.10+ (recommended)
- `pip` / virtualenv
- (Optional) Node.js if working with JS worker/dev tooling
- (Optional) Cloudflare Wrangler if deploying worker components

---

## Setup

```bash
# 1) Clone
git clone https://github.com/LetsVenture2021/PJ.git
cd PJ

# 2) Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows (PowerShell)

# 3) Install Python dependencies
pip install -r requirements.txt
```

---

## Configuration

Start by reviewing and customizing:

- `config.json`
- `tool_policy.json`
- `mcp_servers.json`
- `pj_instructions.txt`
- `wrangler.toml.example` (copy to `wrangler.toml` when needed)

If your environment requires API keys/secrets (LLM, STT/TTS, MCP tools, cloud deploy), set them as environment variables or in your secure runtime config layer (do not commit secrets).

---

## Running

Because this repo contains multiple runtime surfaces, common starting points are:

```bash
# Main runtime / orchestrator
python pj.py

# Realtime server
python realtime_server.py
```

If your setup uses the backend worker + client:
- backend: `pj_realtime_backend_worker.js`
- browser client: `webrtc_client.html`

For MCP/Hugging Face integration:
```bash
python huggingface_mcp_server.py
```

---

## Testing

```bash
# Run tests
pytest -q
```

CI is configured in:
- `.github/workflows/ci.yml`

---

## Development notes

- Add new capability domains as dedicated `*ops.py` modules.
- Keep orchestration concerns in `chiefops.py` / `responses_runtime.py`.
- Keep policy and tool constraints in JSON policy/config files.
- Place reusable automation in `scripts/`.
- Place contracts/schemas in `schemas/` and keep tests updated in `tests/`.

---

## Suggested next improvements

1. Add module-level docs for each `*ops.py` file.
2. Document required environment variables in a dedicated “Configuration Reference”.
3. Add a single canonical local-dev start command (Makefile or task runner).
4. Add architecture diagrams for runtime flow (request → orchestration → tool/policy → response).

---

## License

No license file was detected in the repository root.  
If this project is intended for open-source use, add a `LICENSE` file.
