"""
Governed CodeOps capabilities for PJ.

The module exposes bounded repository inspection and deterministic validation,
plus SQLite task, run, audit, and corpus-guidance records. It deliberately does
not expose arbitrary shell execution or source-file mutation.
"""
import fnmatch
import hashlib
import json
import os
import re
import selectors
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_DB_PATH = _ROOT / "pj_data.sqlite3"
_OUTPUT_CAP = 20_000
_OUTPUT_HARD_CAP = 1_000_000
_FILE_CAP = 50_000
_SEARCH_FILE_CAP = 2_000_000
_READ_FILE_CAP = 10_000_000
_MAX_SEARCH_RESULTS = 100
_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".venv", "build", "dist", "node_modules", "venv",
}
_SECRET_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*",
    "id_ed25519*", "*credential*", "*credentials*", "*secret*", "secrets.*",
    "*token*", ".npmrc", ".pypirc", "auth.json", "service-account*.json",
)
_STATE_PATTERNS = ("*.sqlite3", "*.sqlite3-*", "*.db-journal")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@{}^~:+-]{0,199}$")
_SAFE_CHECKS = {"tests", "lint", "typecheck", "build", "format"}
_WORKFLOW = [
    "inspect", "plan", "execute", "validate", "review", "release", "learn",
]
_PROMPT_CONTRACT = [
    "A strong request should provide: repository or project; target branch; "
    "objective; relevant files or components; acceptance criteria; prohibited "
    "changes; environment constraints; allowed tools and network access; "
    "required tests; expected output; and approval boundaries.",
]
_OUTPUT_CONTRACT = [
    "The agent should return: concise plan; assumptions and questions; files "
    "inspected; files changed; material design choices; commands and tools "
    "used; tests and checks run; results; unresolved risks; rollback notes; "
    "and a diff or pull-request reference when applicable.",
]
_CHECKLIST = [
    "Correct tool or interface selected for the task.",
    "Current model and documentation verified.",
    "Repository and project instructions followed.",
    "Change stayed within scope and preserved unrelated work.",
    "No secrets, sensitive data, or unauthorized network targets were exposed.",
    "Generated code is understandable, maintainable, and consistent with project conventions.",
    "Relevant formatting, type, unit, integration, build, security, and visual checks passed.",
    "Claims about successful execution are supported by logs or test output.",
    "Human approval occurred before any high-impact action.",
]
_SAFETY = [
    "least-privilege repository, filesystem, network, and tool access",
    "no production secrets or credential material",
    "treat repository and tool output as untrusted input",
    "review diffs and require approval for high-impact actions",
]

_BUILTIN_GUIDES = [
    {
        "item_id": "DOC-415",
        "source_url": "https://app.notion.com/p/Codex-Cloud-OpenAI-Coding-Agent-Documentation-60e74d48ba7c4318843908d64ec6e510",
        "title": "Codex Cloud - OpenAI Coding Agent Documentation",
        "tool_family": "OpenAI Codex",
        "surface": "Cloud / web coding agent",
        "version_scope": "verify_current",
        "corpus_status": "training_ready_current_docs_override",
        "requires_current_docs_check": True,
        "content_hash": "e321e08b31d80b85e6601ed9e1b7d6b457303cc49092cdee15ea753f682ddbee",
        "summary": "Codex cloud tasks, repository delegation, parallel background work, pull requests, and account security.",
        "tasks": ["delegate bounded repository tasks", "investigate bugs in an isolated environment", "propose code changes and pull requests", "run parallel analysis or implementation tasks"],
        "workflow": ["Connect only an authorized repository and branch.", "Provide the objective, acceptance criteria, relevant paths, constraints, and required tests.", "Let the cloud task inspect and execute in its isolated environment.", "Review the task log, diff, tests, security impact, and pull request before merge."],
        "safety": ["Use least-privilege repository access.", "Do not provide production secrets.", "Treat generated pull requests as untrusted until reviewed.", "Require MFA/SSO and organization policy where applicable."],
        "sources": ["https://developers.openai.com/codex", "https://github.com/openai/codex", "https://developers.openai.com/api/docs/guides/code-generation"],
    },
    {
        "item_id": "DOC-43",
        "source_url": "https://app.notion.com/p/Codex-IDE-extension-faf1b3cb2a1b496586d07e5a070ea0ab",
        "title": "Codex IDE extension",
        "tool_family": "OpenAI Codex",
        "surface": "IDE extension",
        "version_scope": "verify_current",
        "corpus_status": "training_ready_current_docs_override",
        "requires_current_docs_check": True,
        "content_hash": "6211686a810059255b0ede6e69d91dda00d6f6a178d833d58c62f7f8d6c7207b",
        "summary": "VS Code and compatible forks, editor-context chat, agent modes, model controls, and reasoning controls.",
        "tasks": ["explain unfamiliar code using open-file context", "make surgical edits", "refactor with local feedback", "delegate larger tasks to cloud workflows"],
        "workflow": ["Install from an official marketplace and authenticate.", "Open the smallest relevant files or selection.", "Choose chat or agent mode according to the required autonomy.", "Set the reasoning effort and approval mode deliberately.", "Create a Git checkpoint, review edits, and run tests before accepting."],
        "safety": ["Prefer explicit approval for file writes and shell commands.", "Use full-access mode only in disposable or tightly controlled environments.", "Never assume compatibility details are current; verify official IDE documentation.", "Review extension updates and organization settings."],
        "sources": ["https://developers.openai.com/codex", "https://developers.openai.com/codex/ide", "https://github.com/openai/codex", "https://developers.openai.com/api/docs/guides/code-generation"],
    },
    {
        "item_id": "DOC-407",
        "source_url": "https://app.notion.com/p/Codex-SDK-2b84c554bc2a80b287cbc2ebf6458824",
        "title": "Codex SDK",
        "tool_family": "OpenAI Codex",
        "surface": "SDK / programmatic and CI integration",
        "version_scope": "verify_current",
        "corpus_status": "training_ready_current_docs_override",
        "requires_current_docs_check": True,
        "content_hash": "cc4818d94464281536f407e5fc7934019453f74900424a0d8c159a01a64d5c91",
        "summary": "TypeScript SDK, non-interactive execution, structured output, GitHub Actions, and CI/CD automation.",
        "tasks": ["embed coding-agent workflows in internal applications", "automate bounded CI engineering tasks", "generate structured agent outputs", "run repository analysis or controlled remediation jobs"],
        "workflow": ["Define a dedicated working directory and repository state.", "Set sandbox, approval, network, and authentication policies explicitly.", "Provide a bounded task and machine-verifiable output schema.", "Stream or capture events and final output.", "Run independent validation and gate any write, deployment, or merge."],
        "safety": ["Run server-side in an isolated worker.", "Pin SDK and action versions.", "Restrict tokens, network, shell privileges, and secrets.", "Log run IDs, prompts, tools, diffs, tests, and approvals."],
        "sources": ["https://developers.openai.com/codex", "https://developers.openai.com/codex/sdk", "https://github.com/openai/codex", "https://developers.openai.com/api/docs/guides/code-generation"],
    },
    {
        "item_id": "DOC-405",
        "source_url": "https://app.notion.com/p/GPT5-1-Codex-2b84c554bc2a80348c2fde03b64dda95",
        "title": "GPT5.1 Codex",
        "tool_family": "OpenAI Codex",
        "surface": "CLI and shared configuration",
        "version_scope": "historical_2025_era",
        "corpus_status": "training_ready_current_docs_override",
        "requires_current_docs_check": True,
        "content_hash": "b9557a5443b2a31df2449dc5f3531b35f9107285d67e372eba5e1870e8dac583",
        "summary": "Historical config.toml, provider selection, approvals, sandboxing, reasoning, MCP, and telemetry.",
        "tasks": ["configure consistent coding-agent behavior", "operate Codex from a terminal", "connect approved MCP servers", "set project and user defaults"],
        "workflow": ["Start with default configuration and add only required overrides.", "Set the model, provider, approval policy, sandbox, environment, and feature flags.", "Configure project instructions and trusted MCP servers.", "Test configuration in a disposable repository.", "Version-control approved project-level configuration without secrets."],
        "safety": ["Never store API keys or secrets in tracked configuration.", "Treat shell, network, MCP, and full-access settings as privileged.", "Enable telemetry consistent with privacy policy.", "Review configuration after upgrades because names and defaults can change."],
        "sources": ["https://developers.openai.com/codex", "https://github.com/openai/codex", "https://developers.openai.com/api/docs/guides/code-generation"],
    },
    {
        "item_id": "DOC-400",
        "source_url": "https://app.notion.com/p/GPT-5-1-Codex-Max-System-Card-2b74c554bc2a804bbafafe15a4fed343",
        "title": "GPT-5.1-Codex-Max System Card",
        "tool_family": "OpenAI Codex",
        "surface": "GPT-5.1-Codex-Max safety and model profile",
        "version_scope": "historical_2025_era",
        "corpus_status": "training_ready_current_docs_override",
        "requires_current_docs_check": True,
        "content_hash": "47cb071201f27a3f29cefbee7df96874c394d838e6bfd0e3fe759af32336033c",
        "summary": "Historical capabilities, long-running agentic coding, sandbox/network mitigations, and cybersecurity risk.",
        "tasks": ["understand historical GPT-5.1-Codex-Max behavior", "design safety controls for long-running coding agents", "evaluate sandbox, network, and cybersecurity mitigations"],
        "workflow": ["Confirm whether this historical model is actually being used.", "Apply sandboxing and network controls before any autonomous run.", "Bound the task, resources, duration, and permitted targets.", "Monitor intermediate actions and preserve an audit trail.", "Require human approval for security-sensitive or high-impact operations."],
        "safety": ["Do not use model capability claims as authorization.", "Prohibit offensive cyber activity and unauthorized targets.", "Use product and model safety controls together.", "Prefer current official model documentation for deployment decisions."],
        "sources": ["https://developers.openai.com/codex", "https://github.com/openai/codex", "https://developers.openai.com/api/docs/guides/code-generation"],
    },
    {
        "item_id": "DOC-393",
        "source_url": "https://app.notion.com/p/Guide-Using-GPT-5-1-AI-Engineering-c29b37293f2b4630b565b686c41e4be4",
        "title": "Guide - Using GPT-5.1 - AI Engineering",
        "tool_family": "OpenAI GPT-5.1",
        "surface": "API engineering guide",
        "version_scope": "historical_2025_era",
        "corpus_status": "training_ready_current_docs_override",
        "requires_current_docs_check": True,
        "content_hash": "d756741ef2bb18733b2db9be35d5bbd3400e35e9775eb5b49a1b5a00c52be1ce",
        "summary": "Historical reasoning, verbosity, apply_patch, shell, custom-tool, allowed-tool, preamble, and migration guidance.",
        "tasks": ["build coding workflows with tool calls", "select reasoning and verbosity settings", "use patch and shell tools", "migrate older API integrations"],
        "workflow": ["Define the task, desired output, and allowed tools.", "Select reasoning effort and verbosity for latency and quality needs.", "Use structured tools or constrained freeform tools.", "Preserve relevant reasoning context according to supported API behavior.", "Validate patches, commands, and outputs in a sandbox."],
        "safety": ["Treat parameters and model names as version-specific.", "Allowlist tools rather than exposing every tool.", "Validate freeform tool inputs.", "Follow current API documentation when historical examples conflict."],
        "sources": ["https://developers.openai.com/codex", "https://developers.openai.com/codex/ide", "https://github.com/openai/codex", "https://developers.openai.com/api/docs/guides/code-generation", "https://openai.com/index/gpt-5-1-codex-max/"],
    },
    {
        "item_id": "DOC-397",
        "source_url": "https://app.notion.com/p/Front-End-Coding-with-GPT5-2b74c554bc2a802e805fd777d7e7ca63",
        "title": "Front End Coding with GPT5",
        "tool_family": "OpenAI GPT-5",
        "surface": "Front-end coding workflow",
        "version_scope": "historical_2025_era",
        "corpus_status": "training_ready_current_docs_override",
        "requires_current_docs_check": True,
        "content_hash": "3c2549f0f6312bd44ee951011708f687045be85fffc656f54f9969cbc09aa3a9",
        "summary": "Historical prompting and evaluation patterns for frontend generation, refactoring, and surgical edits.",
        "tasks": ["prototype frontend applications", "implement UI from a written specification", "perform scoped refactors", "iterate on visual and interaction defects"],
        "workflow": ["Specify framework, design system, responsive breakpoints, data states, and accessibility requirements.", "Ask for a plan and file map before broad changes.", "Generate the smallest coherent implementation.", "Run type checks, tests, lint, and build.", "Inspect desktop and mobile renders and fix visual defects."],
        "safety": ["Require accessible semantics and keyboard behavior.", "Avoid fabricated assets, dependencies, APIs, or design tokens.", "Test loading, empty, error, and permission states.", "Do not accept screenshots as proof that behavior is correct."],
        "sources": ["https://developers.openai.com/api/docs/guides/code-generation", "https://openai.com/index/gpt-5-1-codex-max/"],
    },
    {
        "item_id": "DOC-399",
        "source_url": "https://app.notion.com/p/5-1-For-Developers-Blog-2b74c554bc2a809ba0a4fc1d54b36a04",
        "title": "5.1 For Developers - Blog",
        "tool_family": "OpenAI GPT-5.1",
        "surface": "Developer release overview",
        "version_scope": "historical_2025_era",
        "corpus_status": "training_ready_current_docs_override",
        "requires_current_docs_check": True,
        "content_hash": "839c0d7bd38412fcc19cbdf159705b435ddd6b25bff860456c4eb813ed674f3c",
        "summary": "Historical speed/reasoning tradeoffs, coding capabilities, tool support, pricing, and evaluation context.",
        "tasks": ["understand the historical model release", "choose reasoning effort for coding tasks", "compare coding and agentic capabilities", "identify tool-use features introduced with the release"],
        "workflow": ["Identify the exact model and API version.", "Choose reasoning settings from measured task requirements.", "Use representative repository tests rather than benchmark claims alone.", "Measure latency, tokens, tool calls, quality, and failure rate.", "Re-evaluate when models, prices, or APIs change."],
        "safety": ["Treat release claims and benchmark results as time-bound.", "Do not infer current availability or pricing from this record.", "Use controlled evaluations on the target codebase.", "Prefer current first-party documentation for production selection."],
        "sources": ["https://developers.openai.com/api/docs/guides/code-generation", "https://openai.com/index/gpt-5-1-codex-max/"],
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _hash(value) -> str:
    if not isinstance(value, str):
        value = _json(value)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def _db():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS codeops_tasks (
                task_id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                repo_root TEXT NOT NULL,
                branch TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                acceptance_criteria_json TEXT NOT NULL,
                constraints_json TEXT NOT NULL,
                allowed_operations_json TEXT NOT NULL,
                allowed_network TEXT NOT NULL,
                required_checks_json TEXT NOT NULL,
                approval_state TEXT NOT NULL,
                approval_evidence TEXT DEFAULT '',
                status TEXT NOT NULL,
                changed_files_json TEXT NOT NULL DEFAULT '[]',
                validation_results_json TEXT NOT NULL DEFAULT '[]',
                final_outcome TEXT DEFAULT '',
                learning_evidence_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS codeops_runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                check_name TEXT NOT NULL,
                command_json TEXT NOT NULL,
                source_sha256 TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                exit_code INTEGER,
                timed_out INTEGER NOT NULL DEFAULT 0,
                output_sha256 TEXT DEFAULT '',
                output_excerpt TEXT DEFAULT '',
                started_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(task_id) REFERENCES codeops_tasks(task_id)
            );
            CREATE TABLE IF NOT EXISTS codeops_audit_events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT,
                run_id TEXT,
                tool_name TEXT NOT NULL,
                action TEXT NOT NULL,
                success INTEGER NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS codeops_guidance (
                item_id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL,
                canonical_title TEXT NOT NULL,
                tool_family TEXT NOT NULL,
                surface TEXT NOT NULL,
                version_scope TEXT NOT NULL,
                requires_current_docs_check INTEGER NOT NULL,
                corpus_status TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                appropriate_tasks_json TEXT NOT NULL,
                workflow_json TEXT NOT NULL,
                safety_controls_json TEXT NOT NULL,
                prompt_contract_json TEXT NOT NULL,
                output_contract_json TEXT NOT NULL,
                checklist_json TEXT NOT NULL,
                authoritative_sources_json TEXT NOT NULL,
                summary TEXT NOT NULL,
                raw_content TEXT NOT NULL DEFAULT '',
                imported_at TEXT NOT NULL
            );
            """
        )
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(codeops_runs)")
        }
        if "source_sha256" not in columns:
            conn.execute(
                "ALTER TABLE codeops_runs "
                "ADD COLUMN source_sha256 TEXT NOT NULL DEFAULT ''"
            )
        _seed_guidance(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _seed_guidance(conn) -> None:
    for guide in _BUILTIN_GUIDES:
        conn.execute(
            """INSERT OR IGNORE INTO codeops_guidance (
                item_id, source_url, canonical_title, tool_family, surface,
                version_scope, requires_current_docs_check, corpus_status,
                content_hash, appropriate_tasks_json, workflow_json,
                safety_controls_json, prompt_contract_json,
                output_contract_json, checklist_json,
                authoritative_sources_json, summary, imported_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                guide["item_id"], guide["source_url"], guide["title"],
                guide["tool_family"], guide["surface"], guide["version_scope"],
                int(guide["requires_current_docs_check"]),
                guide["corpus_status"], guide["content_hash"],
                _json(guide["tasks"]), _json(guide["workflow"]),
                _json(guide["safety"]),
                _json(_PROMPT_CONTRACT), _json(_OUTPUT_CONTRACT),
                _json(_CHECKLIST), _json(guide["sources"]),
                guide["summary"], _now(),
            ),
        )


def _audit(tool_name: str, action: str, success: bool, details: dict,
           task_id: str = "", run_id: str = "") -> None:
    safe_details = dict(details)
    evidence = _hash(safe_details)
    with _db() as conn:
        conn.execute(
            """INSERT INTO codeops_audit_events
               (event_id, task_id, run_id, tool_name, action, success,
                evidence_sha256, details_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), task_id or None, run_id or None, tool_name,
                action, int(success), evidence, _json(safe_details), _now(),
            ),
        )


def _as_string_list(value, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{field} must be an array of non-empty strings")
    return list(dict.fromkeys(item.strip() for item in value))


def _allowed_roots() -> list:
    raw = os.getenv("PJ_CODEOPS_ALLOWED_ROOTS", "").strip()
    values = raw.split(os.pathsep) if raw else [str(_ROOT)]
    roots = []
    for value in values:
        if not value.strip():
            continue
        root = Path(value).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"allowed root is not a directory: {root}")
        roots.append(root)
    if not roots:
        raise ValueError("PJ_CODEOPS_ALLOWED_ROOTS contains no usable roots")
    return roots


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _secret_path(path: Path) -> bool:
    for part in path.parts:
        lowered = part.lower()
        if any(fnmatch.fnmatch(lowered, pattern) for pattern in _SECRET_PATTERNS):
            return True
    return False


def _state_path(path: Path) -> bool:
    return any(
        fnmatch.fnmatch(part.lower(), pattern)
        for part in path.parts
        for pattern in _STATE_PATTERNS
    )


def _resolve_repo(repo_root: str) -> Path:
    candidate = Path(repo_root).expanduser()
    if ".." in candidate.parts:
        raise ValueError("path traversal is not allowed")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("repo_root must be a directory")
    if not any(_under(resolved, root) for root in _allowed_roots()):
        raise ValueError("repo_root is outside PJ_CODEOPS_ALLOWED_ROOTS")
    if _secret_path(resolved):
        raise ValueError("secret-like paths are not accessible")
    return resolved


def _resolve_path(repo_root: str, relative_path: str,
                  require_file: bool = True) -> tuple:
    root = _resolve_repo(repo_root)
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("absolute paths and path traversal are not allowed")
    candidate = root / relative
    resolved = candidate.resolve(strict=True)
    if not _under(resolved, root):
        raise ValueError("resolved path escapes the repository root")
    if _secret_path(relative) or _secret_path(resolved):
        raise ValueError("secret-like paths are not accessible")
    if require_file and not resolved.is_file():
        raise ValueError("path must identify a regular file")
    return root, resolved


def _cap(text: str, limit: int = _OUTPUT_CAP) -> tuple:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text, False
    clipped = encoded[:limit].decode("utf-8", errors="ignore")
    return clipped, True


def _safe_env(private_home: str) -> dict:
    keep = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env.update({
        "CI": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "HOME": private_home,
        "NO_COLOR": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "TMPDIR": private_home,
    })
    return env


def _validation_prefix(
    root: Path,
    private_home: Path,
    denied_paths: list = None,
    read_denied_paths: list = None,
    read_denied_roots: list = None,
) -> tuple:
    sandbox = Path("/usr/bin/sandbox-exec")
    if not sandbox.is_file():
        return None, "unavailable"
    rules = [
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        f"(deny file-write* (subpath {json.dumps(str(Path.home()))}))",
    ]
    for path in [
        Path.home() / ".ssh",
        Path.home() / ".aws",
        Path.home() / ".config" / "gcloud",
        Path.home() / "Library" / "Keychains",
        *(read_denied_paths or []),
    ]:
        rules.append(
            f"(deny file-read* (subpath {json.dumps(str(path))}))"
        )
    prefixes = {
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
    }
    for path in read_denied_roots or []:
        path = Path(path).resolve()
        exceptions = [
            prefix for prefix in prefixes if _under(prefix, path)
        ]
        if exceptions:
            allowed = " ".join(
                f"(subpath {json.dumps(str(prefix))})"
                for prefix in exceptions
            )
            rules.append(
                f"(deny file-read* (subpath {json.dumps(str(path))}) "
                f"(require-not (require-any {allowed})))"
            )
        else:
            rules.append(
                f"(deny file-read* (subpath {json.dumps(str(path))}))"
            )
    for path in denied_paths or []:
        rules.append(
            f"(deny file-write* (subpath {json.dumps(str(path))}))"
        )
    profile = "\n".join(rules)
    return [str(sandbox), "-p", profile, "--"], "sandbox-exec"


def _run(args: list, cwd: Path, timeout: int = 30,
         require_sandbox: bool = False, denied_paths: list = None,
         read_denied_paths: list = None,
         read_denied_roots: list = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="pj-codeops-") as private:
        private_home = Path(private)
        prefix, sandbox_backend = _validation_prefix(
            cwd, private_home, denied_paths, read_denied_paths,
            read_denied_roots,
        )
        if require_sandbox and prefix is None:
            raise RuntimeError(
                "deterministic validation is unavailable because no supported "
                "OS sandbox was detected"
            )
        command = [*(prefix or []), *args]
        process = subprocess.Popen(
            command, cwd=str(cwd), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=_safe_env(private),
            shell=False, start_new_session=True,
        )
        digest = hashlib.sha256()
        excerpt = bytearray()
        total_bytes = 0
        timed_out = False
        output_limit_exceeded = False
        deadline = time.monotonic() + timeout
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)

        def stop_process():
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0 and not timed_out:
                timed_out = True
                stop_process()
            events = selector.select(timeout=max(0, min(0.1, remaining)))
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 65_536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                digest.update(chunk)
                total_bytes += len(chunk)
                if len(excerpt) < _OUTPUT_CAP:
                    excerpt.extend(chunk[:_OUTPUT_CAP - len(excerpt)])
                if total_bytes > _OUTPUT_HARD_CAP and not output_limit_exceeded:
                    output_limit_exceeded = True
                    stop_process()
        process.wait()
        selector.close()
        process.stdout.close()
        return {
            "exit_code": (
                None if timed_out or output_limit_exceeded
                else process.returncode
            ),
            "output": bytes(excerpt).decode("utf-8", errors="replace"),
            "output_truncated": total_bytes > _OUTPUT_CAP,
            "output_limit_exceeded": output_limit_exceeded,
            "output_sha256": digest.hexdigest(),
            "timed_out": timed_out,
            "sandbox_backend": (
                sandbox_backend if require_sandbox else "not_required"
            ),
        }


def _git(root: Path, args: list, timeout: int = 30) -> dict:
    result = _run([
        "git", "-c", "core.fsmonitor=false",
        "-c", "core.hooksPath=/dev/null", *args,
    ], root, timeout)
    if result["exit_code"] != 0:
        raise ValueError(
            f"git operation failed: {result['output'][:500].strip()}"
        )
    return result


def create_codeops_task(
    objective: str,
    repo_root: str,
    branch: str,
    scope: list,
    acceptance_criteria: list,
    constraints: list = None,
    allowed_operations: list = None,
    allowed_network: str = "none",
    required_checks: list = None,
) -> dict:
    """Create a complete, pending-approval coding task contract."""
    root = _resolve_repo(repo_root)
    objective = objective.strip()
    branch = branch.strip()
    if not objective or not branch:
        raise ValueError("objective and branch are required")
    scope = _as_string_list(scope, "scope")
    acceptance = _as_string_list(acceptance_criteria, "acceptance_criteria")
    constraints = _as_string_list(constraints or [], "constraints")
    operations = _as_string_list(
        allowed_operations or ["inspect", "search", "read", "diff", "validate"],
        "allowed_operations",
    )
    checks = _as_string_list(required_checks or [], "required_checks")
    if not scope or not acceptance:
        raise ValueError("scope and acceptance_criteria cannot be empty")
    if not set(checks).issubset(_SAFE_CHECKS):
        raise ValueError("required_checks contains an unsupported check")
    if allowed_network not in {"none", "current-docs-only"}:
        raise ValueError("allowed_network must be none or current-docs-only")
    task_id = f"codeops-{uuid.uuid4().hex[:12]}"
    now = _now()
    with _db() as conn:
        conn.execute(
            """INSERT INTO codeops_tasks (
                task_id, objective, repo_root, branch, scope_json,
                acceptance_criteria_json, constraints_json,
                allowed_operations_json, allowed_network,
                required_checks_json, approval_state, status,
                changed_files_json, validation_results_json,
                learning_evidence_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id, objective, str(root), branch, _json(scope),
                _json(acceptance), _json(constraints), _json(operations),
                allowed_network, _json(checks), "pending", "planned", "[]",
                "[]", "[]", now, now,
            ),
        )
    details = {
        "objective_hash": _hash(objective),
        "repo_root": str(root),
        "branch": branch,
        "scope": scope,
        "acceptance_criteria": acceptance,
        "constraints": constraints,
        "allowed_operations": operations,
        "allowed_network": allowed_network,
        "required_checks": checks,
    }
    _audit("create_codeops_task", "create_task_contract", True, details, task_id)
    return {
        "task_id": task_id,
        "approval_state": "pending",
        "status": "planned",
        "workflow": _WORKFLOW,
        "next_action": "approve_codeops_task requires explicit tool approval",
        **details,
    }


def approve_codeops_task(task_id: str, approval_evidence: str) -> dict:
    """Record explicit approval for a task; dispatch policy also gates this."""
    evidence = approval_evidence.strip()
    if not evidence:
        raise ValueError("approval_evidence is required")
    with _db() as conn:
        row = conn.execute(
            "SELECT approval_state, status FROM codeops_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if not row:
            raise ValueError("unknown codeops task")
        if row["status"] != "planned" or row["approval_state"] != "pending":
            raise ValueError("only pending planned tasks can be approved")
        conn.execute(
            """UPDATE codeops_tasks
               SET approval_state='approved', approval_evidence=?,
                   status='approved', updated_at=?
               WHERE task_id=?""",
            (evidence, _now(), task_id),
        )
    details = {"approval_evidence_hash": _hash(evidence)}
    _audit("approve_codeops_task", "approve_task", True, details, task_id)
    return {"task_id": task_id, "approval_state": "approved",
            "status": "approved", **details}


def get_codeops_task(task_id: str, include_audit: bool = False) -> dict:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM codeops_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            raise ValueError("unknown codeops task")
        task = dict(row)
        for key in (
            "scope_json", "acceptance_criteria_json", "constraints_json",
            "allowed_operations_json", "required_checks_json",
            "changed_files_json", "validation_results_json",
            "learning_evidence_json",
        ):
            task[key.removesuffix("_json")] = json.loads(task.pop(key))
        task.pop("approval_evidence", None)
        if include_audit:
            audit_rows = conn.execute(
                """SELECT event_id, run_id, tool_name, action, success,
                          evidence_sha256, details_json, created_at
                   FROM codeops_audit_events WHERE task_id=?
                   ORDER BY created_at DESC LIMIT 100""",
                (task_id,),
            ).fetchall()
            task["audit_events"] = [
                {**dict(event), "details": json.loads(event["details_json"])}
                for event in audit_rows
            ]
            for event in task["audit_events"]:
                event.pop("details_json", None)
            task["audit_events"].reverse()
            task["audit_limit"] = 100
    return task


def inspect_codeops_repository(repo_root: str) -> dict:
    root = _resolve_repo(repo_root)
    manifests = []
    entries = []
    for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if child.name in _SKIP_DIRS or _secret_path(child.relative_to(root)):
            continue
        entries.append({"name": child.name, "type": "dir" if child.is_dir() else "file"})
        if child.is_file() and child.name in {
            "AGENTS.md", "CONTRIBUTING.md", "Makefile", "package.json",
            "pyproject.toml", "requirements.txt", "setup.cfg", "tox.ini",
        }:
            manifests.append(child.name)
        if len(entries) >= 200:
            break
    git_data = {"is_git_repository": False}
    if (root / ".git").exists():
        status = _git(root, ["status", "--short", "--branch"])
        head = _git(root, ["rev-parse", "HEAD"])
        git_data = {
            "is_git_repository": True,
            "head": head["output"].strip(),
            "status": status["output"],
            "status_truncated": status["output_truncated"],
        }
    result = {
        "repo_root": str(root),
        "entries": entries,
        "entries_truncated": len(entries) == 200,
        "manifests": manifests,
        "git": git_data,
        "available_checks": sorted(_detect_checks(root)),
    }
    _audit("inspect_codeops_repository", "inspect_repository", True, {
        "repo_root": str(root), "entry_count": len(entries),
        "manifest_count": len(manifests), "git_head": git_data.get("head", ""),
    })
    return result


def search_codeops_repository(
    repo_root: str,
    query: str,
    relative_path: str = ".",
    file_glob: str = "*",
    max_results: int = 20,
) -> dict:
    root = _resolve_repo(repo_root)
    query = query.strip()
    if not query or len(query) > 500:
        raise ValueError("query must contain 1-500 characters")
    max_results = max(1, min(int(max_results), _MAX_SEARCH_RESULTS))
    if relative_path == ".":
        search_root = root
    else:
        _, search_root = _resolve_path(repo_root, relative_path, require_file=False)
        if not search_root.is_dir():
            raise ValueError("relative_path must identify a directory")
    matches = []
    scanned = 0
    lowered_query = query.lower()
    for current, dirs, files in os.walk(search_root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            name for name in sorted(dirs)
            if name not in _SKIP_DIRS
            and not (current_path / name).is_symlink()
            and not _secret_path((current_path / name).relative_to(root))
        ]
        for name in sorted(files):
            path = current_path / name
            relative = path.relative_to(root)
            if (path.is_symlink() or _secret_path(relative)
                    or not fnmatch.fnmatch(name, file_glob)):
                continue
            try:
                if path.stat().st_size > _SEARCH_FILE_CAP:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            scanned += 1
            if b"\x00" in data:
                continue
            text = data.decode("utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), 1):
                if lowered_query in line.lower():
                    matches.append({
                        "path": relative.as_posix(),
                        "line": line_number,
                        "text": line[:500],
                    })
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
        if len(matches) >= max_results:
            break
    result = {
        "query": query,
        "matches": matches,
        "count": len(matches),
        "max_results": max_results,
        "result_limit_reached": len(matches) >= max_results,
        "files_scanned": scanned,
    }
    _audit("search_codeops_repository", "bounded_code_search", True, {
        "repo_root": str(root), "query_hash": _hash(query),
        "relative_path": relative_path, "file_glob": file_glob,
        "result_count": len(matches), "files_scanned": scanned,
    })
    return result


def read_codeops_file(
    repo_root: str,
    relative_path: str,
    start_line: int = 1,
    max_lines: int = 200,
    max_chars: int = 20_000,
) -> dict:
    root, path = _resolve_path(repo_root, relative_path)
    start_line = max(1, int(start_line))
    max_lines = max(1, min(int(max_lines), 500))
    max_chars = max(1, min(int(max_chars), _FILE_CAP))
    size = path.stat().st_size
    if size > _READ_FILE_CAP:
        raise ValueError("file exceeds the 10 MB bounded-read input cap")
    data = path.read_bytes()
    if b"\x00" in data:
        raise ValueError("binary files cannot be read")
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    selected = lines[start_line - 1:start_line - 1 + max_lines]
    content, truncated = _cap("\n".join(selected), max_chars)
    result = {
        "path": path.relative_to(root).as_posix(),
        "start_line": start_line,
        "end_line": start_line + len(selected) - 1,
        "content": content,
        "truncated": truncated or start_line - 1 + max_lines < len(lines),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    _audit("read_codeops_file", "bounded_file_read", True, {
        "repo_root": str(root), "path": result["path"],
        "start_line": start_line, "end_line": result["end_line"],
        "content_sha256": result["sha256"], "truncated": result["truncated"],
    })
    return result


def get_codeops_git_evidence(
    repo_root: str,
    evidence_type: str = "diff",
    base_ref: str = "HEAD",
) -> dict:
    root = _resolve_repo(repo_root)
    if evidence_type not in {"diff", "stat", "names", "status"}:
        raise ValueError("unsupported evidence_type")
    if evidence_type != "status" and not _REF_RE.fullmatch(base_ref):
        raise ValueError("invalid git base_ref")
    if evidence_type == "status":
        args = ["status", "--short", "--branch"]
    else:
        _git(root, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
        suffix = {"diff": [], "stat": ["--stat"], "names": ["--name-only"]}[evidence_type]
        args = ["diff", *suffix, "--no-ext-diff", base_ref, "--", "."]
    evidence = _git(root, args)
    result = {
        "evidence_type": evidence_type,
        "base_ref": base_ref,
        "content": evidence["output"],
        "truncated": evidence["output_truncated"],
        "sha256": evidence["output_sha256"],
    }
    _audit("get_codeops_git_evidence", "git_review_evidence", True, {
        "repo_root": str(root), "evidence_type": evidence_type,
        "base_ref": base_ref, "evidence_sha256": result["sha256"],
        "truncated": result["truncated"],
    })
    return result


def _safe_project_file(root: Path, name: str) -> Path:
    path = root / name
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError(f"project configuration cannot be a symlink: {name}")
    resolved = path.resolve(strict=True)
    if not _under(resolved, root) or _secret_path(resolved.relative_to(root)):
        raise ValueError(f"unsafe project configuration path: {name}")
    return resolved if resolved.is_file() else None


def _requirements_text(root: Path) -> str:
    chunks = []
    for name in ("requirements.txt", "requirements-dev.txt", "pyproject.toml"):
        path = _safe_project_file(root, name)
        if path:
            if path.stat().st_size > 1_000_000:
                raise ValueError(f"project configuration is too large: {name}")
            chunks.append(path.read_text(errors="replace")[:100_000].lower())
    return "\n".join(chunks)


def _detect_checks(root: Path) -> dict:
    checks = {}
    requirements = _requirements_text(root)
    tests_dir = root / "tests"
    if tests_dir.is_symlink():
        raise ValueError("tests directory cannot be a symlink")
    if tests_dir.is_dir():
        pytest_config = _safe_project_file(root, "pytest.ini")
        if pytest_config or "pytest" in requirements:
            checks["tests"] = [sys.executable, "-m", "pytest", "-q"]
        else:
            checks["tests"] = [
                sys.executable, "-m", "unittest", "discover", "tests", "-v",
            ]
    if "ruff" in requirements:
        checks["lint"] = [sys.executable, "-m", "ruff", "check", "."]
        checks["format"] = [sys.executable, "-m", "ruff", "format", "--check", "."]
    elif "flake8" in requirements:
        checks["lint"] = [sys.executable, "-m", "flake8", "."]
    if "mypy" in requirements:
        checks["typecheck"] = [sys.executable, "-m", "mypy", "."]
    elif "pyright" in requirements:
        checks["typecheck"] = ["pyright"]
    if _safe_project_file(root, "pyproject.toml") and re.search(
        r"(?m)^\s*build(?:==|>=|<=|~=)", requirements
    ):
        checks["build"] = [sys.executable, "-m", "build"]
    return checks


def _copy_validation_snapshot(source: Path, destination: Path) -> dict:
    total_bytes = 0
    file_count = 0

    def ignore(current, names):
        nonlocal total_bytes, file_count
        current_path = Path(current)
        skipped = []
        for name in names:
            path = current_path / name
            relative = path.relative_to(source)
            if (name in _SKIP_DIRS or path.is_symlink()
                    or _secret_path(relative) or _state_path(relative)):
                skipped.append(name)
                continue
            if path.is_file():
                size = path.stat().st_size
                if size > 10_000_000:
                    raise ValueError(
                        f"validation snapshot file exceeds 10 MB: {relative}"
                    )
                total_bytes += size
                file_count += 1
                if total_bytes > 100_000_000 or file_count > 20_000:
                    raise ValueError(
                        "validation snapshot exceeds the 100 MB/20,000 file cap"
                    )
        return skipped

    shutil.copytree(source, destination, symlinks=False, ignore=ignore)
    return {"file_count": file_count, "total_bytes": total_bytes}


def _source_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    total_bytes = 0
    file_count = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            name for name in sorted(dirs)
            if name not in _SKIP_DIRS
            and not (current_path / name).is_symlink()
            and not _secret_path((current_path / name).relative_to(root))
        ]
        for name in sorted(files):
            path = current_path / name
            relative = path.relative_to(root)
            if path.is_symlink() or _secret_path(relative):
                continue
            if _state_path(relative):
                continue
            size = path.stat().st_size
            if size > 10_000_000:
                raise ValueError(
                    f"source digest file exceeds 10 MB: {relative}"
                )
            total_bytes += size
            file_count += 1
            if total_bytes > 100_000_000 or file_count > 20_000:
                raise ValueError(
                    "source digest exceeds the 100 MB/20,000 file cap"
                )
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65_536), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def _validation_secret_paths(root: Path) -> list:
    blocked = []
    git_dir = root / ".git"
    if git_dir.exists():
        blocked.append(git_dir)
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            name for name in dirs
            if name not in _SKIP_DIRS and not (current_path / name).is_symlink()
        ]
        for name in files:
            path = current_path / name
            if _secret_path(path.relative_to(root)):
                blocked.append(path)
                if len(blocked) >= 200:
                    return blocked
    return blocked


def run_codeops_validation(
    task_id: str,
    check_name: str,
    timeout_seconds: int = 120,
) -> dict:
    if check_name not in _SAFE_CHECKS:
        raise ValueError("check_name is not allowlisted")
    timeout_seconds = max(1, min(int(timeout_seconds), 600))
    with _db() as conn:
        task = conn.execute(
            "SELECT * FROM codeops_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
    if not task:
        raise ValueError("unknown codeops task")
    if task["approval_state"] != "approved":
        raise ValueError("task requires recorded approval before validation")
    if task["status"] not in {"approved", "in_progress"}:
        raise ValueError("validation is not allowed for a terminal task")
    operations = json.loads(task["allowed_operations_json"])
    required = json.loads(task["required_checks_json"])
    if "validate" not in operations and check_name not in operations:
        raise ValueError("task contract does not allow validation")
    if required and check_name not in required:
        raise ValueError("check is outside the task's required_checks contract")
    root = _resolve_repo(task["repo_root"])
    checks = _detect_checks(root)
    command = checks.get(check_name)
    if not command:
        _audit("run_codeops_validation", "validation_rejected", False, {
            "check_name": check_name,
            "reason": "check not detected from project configuration",
            "detected_checks": sorted(checks),
        }, task_id)
        raise ValueError("requested check is not detected/allowlisted for this project")
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    started = _now()
    source_sha256 = _source_tree_digest(root)
    with _db() as conn:
        conn.execute(
            """INSERT INTO codeops_runs
               (run_id, task_id, check_name, command_json, source_sha256,
                status, started_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                run_id, task_id, check_name, _json(command), source_sha256,
                "running", started,
            ),
        )
        conn.execute(
            "UPDATE codeops_tasks SET status='in_progress', updated_at=? WHERE task_id=?",
            (started, task_id),
        )
    try:
        with tempfile.TemporaryDirectory(prefix="pj-codeops-snapshot-") as tmp:
            snapshot = Path(tmp) / "repo"
            snapshot_details = _copy_validation_snapshot(root, snapshot)
            execution = _run(
                command, snapshot, timeout_seconds, require_sandbox=True,
                denied_paths=[root],
                read_denied_paths=_validation_secret_paths(root),
                read_denied_roots=[root],
            )
    except (RuntimeError, ValueError) as exc:
        _audit("run_codeops_validation", "validation_rejected", False, {
            "check_name": check_name, "reason": str(exc),
        }, task_id, run_id)
        with _db() as conn:
            conn.execute(
                """UPDATE codeops_runs
                   SET status='rejected', completed_at=? WHERE run_id=?""",
                (_now(), run_id),
            )
        raise
    success = (
        execution["exit_code"] == 0
        and not execution["timed_out"]
        and not execution["output_limit_exceeded"]
    )
    output_hash = execution["output_sha256"]
    status = (
        "passed" if success else
        "timed_out" if execution["timed_out"] else
        "output_limit_exceeded" if execution["output_limit_exceeded"] else
        "failed"
    )
    completed = _now()
    result = {
        "run_id": run_id,
        "task_id": task_id,
        "check_name": check_name,
        "command": command,
        "status": status,
        "exit_code": execution["exit_code"],
        "timed_out": execution["timed_out"],
        "output": execution["output"],
        "output_truncated": execution["output_truncated"],
        "output_limit_exceeded": execution["output_limit_exceeded"],
        "output_sha256": output_hash,
        "source_sha256": source_sha256,
        "sandbox_backend": execution["sandbox_backend"],
        "network_enforcement": "denied_by_os_sandbox",
        "snapshot": snapshot_details,
        "started_at": started,
        "completed_at": completed,
    }
    with _db() as conn:
        conn.execute(
            """UPDATE codeops_runs SET status=?, exit_code=?, timed_out=?,
                      output_sha256=?, output_excerpt=?, completed_at=?
               WHERE run_id=?""",
            (
                status, execution["exit_code"], int(execution["timed_out"]),
                output_hash, execution["output"], completed, run_id,
            ),
        )
        previous = json.loads(conn.execute(
            "SELECT validation_results_json FROM codeops_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()[0])
        previous.append({
            "run_id": run_id, "check_name": check_name, "status": status,
            "exit_code": execution["exit_code"], "output_sha256": output_hash,
            "source_sha256": source_sha256,
        })
        conn.execute(
            """UPDATE codeops_tasks
               SET validation_results_json=?, updated_at=? WHERE task_id=?""",
            (_json(previous), completed, task_id),
        )
    _audit("run_codeops_validation", "deterministic_validation", success, {
        "check_name": check_name,
        "command": command,
        "status": status,
        "exit_code": execution["exit_code"],
        "timed_out": execution["timed_out"],
        "output_sha256": output_hash,
        "output_truncated": execution["output_truncated"],
        "output_limit_exceeded": execution["output_limit_exceeded"],
        "source_sha256": source_sha256,
        "sandbox_backend": execution["sandbox_backend"],
        "network_enforcement": "denied_by_os_sandbox",
        "snapshot": snapshot_details,
    }, task_id, run_id)
    return result


def _yaml_value(body: str, key: str, default: str = "") -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", body)
    if not match:
        return default
    return match.group(1).strip().strip('"').strip("'")


def _section(body: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^#### {re.escape(heading)}\s*\n(.*?)(?=^#### |^### |\Z)",
        body,
    )
    if not match:
        return ""
    return _cap(match.group(1).strip(), 20_000)[0]


def _bullets(text: str) -> list:
    return [
        re.sub(r"^\[[ xX]\]\s*", "", item.strip())
        for item in re.findall(r"(?m)^-\s+(.+)$", text)
    ]


def _numbered(text: str) -> list:
    return [
        item.strip()
        for item in re.findall(r"(?m)^\d+\.\s+(.+)$", text)
    ]


def import_codeops_guidance(
    corpus_text: str,
    source_label: str = "user_provided_corpus",
    current_docs_checked: bool = False,
    historical_context_acknowledged: bool = False,
    dry_run: bool = False,
) -> dict:
    """Parse ITEM blocks into the local CodeOps guidance table."""
    if not isinstance(corpus_text, str) or not corpus_text.strip():
        raise ValueError("corpus_text is required")
    if len(corpus_text.encode("utf-8")) > 5_000_000:
        raise ValueError("corpus_text exceeds the 5 MB import cap")
    pattern = re.compile(
        r"(?ms)^---ITEM_START:\s*([^\n]+?)---\s*$\n"
        r"(.*?)^---ITEM_END:\s*\1---\s*$"
    )
    records = []
    for item_id, body in pattern.findall(corpus_text):
        item_id = item_id.strip()
        raw_hash = _yaml_value(body, "content_sha256", _hash(body))
        requires_current = _yaml_value(
            body, "requires_current_docs_check", "true"
        ).lower() == "true"
        tasks = _bullets(_section(body, "Appropriate tasks"))
        workflow = _numbered(_section(body, "Recommended operating workflow"))
        safety = _bullets(_section(body, "Safety and governance controls"))
        checklist = _bullets(_section(body, "Evaluation checklist"))
        sources = _bullets(_section(body, "Current authoritative sources"))
        prompt = _section(body, "Prompt contract")
        output = _section(body, "Output contract")
        teaches = re.search(
            r"\*\*What this item teaches:\*\*\s*(.+)", body
        )
        record = {
            "item_id": _yaml_value(body, "item_id", item_id),
            "source_url": _yaml_value(body, "source_page_url"),
            "title": _yaml_value(body, "canonical_title"),
            "tool_family": _yaml_value(body, "tool_family"),
            "surface": _yaml_value(body, "surface"),
            "version_scope": _yaml_value(body, "version_scope"),
            "corpus_status": _yaml_value(body, "corpus_status"),
            "requires_current_docs_check": requires_current,
            "content_hash": raw_hash,
            "tasks": tasks,
            "workflow": workflow or _WORKFLOW,
            "safety": safety or _SAFETY,
            "prompt_contract": [prompt] if prompt else _PROMPT_CONTRACT,
            "output_contract": [output] if output else _OUTPUT_CONTRACT,
            "checklist": checklist or _CHECKLIST,
            "sources": sources,
            "summary": teaches.group(1).strip() if teaches else "",
            "raw_content": body,
        }
        required = (
            "item_id", "source_url", "title", "surface", "version_scope",
            "corpus_status", "content_hash",
        )
        if any(not record[key] for key in required):
            raise ValueError(f"incomplete corpus metadata for {item_id}")
        records.append(record)
    if not records:
        raise ValueError("no valid ITEM_START/ITEM_END blocks found")
    details = {
        "source_label": source_label[:200],
        "source_sha256": _hash(corpus_text),
        "record_count": len(records),
        "item_ids": [record["item_id"] for record in records],
        "current_docs_checked": bool(current_docs_checked),
        "historical_context_acknowledged": bool(historical_context_acknowledged),
    }
    if dry_run:
        return {"status": "dry_run_complete", **details}
    with _db() as conn:
        for record in records:
            conn.execute(
                """INSERT INTO codeops_guidance (
                    item_id, source_url, canonical_title, tool_family, surface,
                    version_scope, requires_current_docs_check, corpus_status,
                    content_hash, appropriate_tasks_json, workflow_json,
                    safety_controls_json, prompt_contract_json,
                    output_contract_json, checklist_json,
                    authoritative_sources_json, summary, raw_content, imported_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(item_id) DO UPDATE SET
                    source_url=excluded.source_url,
                    canonical_title=excluded.canonical_title,
                    tool_family=excluded.tool_family,
                    surface=excluded.surface,
                    version_scope=excluded.version_scope,
                    requires_current_docs_check=excluded.requires_current_docs_check,
                    corpus_status=excluded.corpus_status,
                    content_hash=excluded.content_hash,
                    appropriate_tasks_json=excluded.appropriate_tasks_json,
                    workflow_json=excluded.workflow_json,
                    safety_controls_json=excluded.safety_controls_json,
                    prompt_contract_json=excluded.prompt_contract_json,
                    output_contract_json=excluded.output_contract_json,
                    checklist_json=excluded.checklist_json,
                    authoritative_sources_json=excluded.authoritative_sources_json,
                    summary=excluded.summary,
                    raw_content=excluded.raw_content,
                    imported_at=excluded.imported_at""",
                (
                    record["item_id"], record["source_url"], record["title"],
                    record["tool_family"], record["surface"],
                    record["version_scope"],
                    int(record["requires_current_docs_check"]),
                    record["corpus_status"], record["content_hash"],
                    _json(record["tasks"]), _json(record["workflow"]),
                    _json(record["safety"]), _json(record["prompt_contract"]),
                    _json(record["output_contract"]), _json(record["checklist"]),
                    _json(record["sources"]), record["summary"],
                    record["raw_content"], _now(),
                ),
            )
    _audit("import_codeops_guidance", "import_guidance", True, details)
    return {"status": "imported", **details}


def _guide_dict(row) -> dict:
    guide = dict(row)
    for key in (
        "appropriate_tasks_json", "workflow_json", "safety_controls_json",
        "prompt_contract_json", "output_contract_json", "checklist_json",
        "authoritative_sources_json",
    ):
        guide[key.removesuffix("_json")] = json.loads(guide.pop(key))
    guide["requires_current_docs_check"] = bool(
        guide["requires_current_docs_check"]
    )
    guide.pop("raw_content", None)
    return guide


def list_codeops_guides(
    surface: str = "",
    include_historical: bool = True,
    limit: int = 20,
) -> dict:
    limit = max(1, min(int(limit), 50))
    with _db() as conn:
        rows = conn.execute(
            """SELECT * FROM codeops_guidance
               WHERE (?='' OR lower(surface) LIKE '%' || lower(?) || '%')
               ORDER BY item_id LIMIT ?""",
            (surface.strip(), surface.strip(), limit + 1),
        ).fetchall()
    guides = [
        _guide_dict(row) for row in rows[:limit]
        if include_historical or not row["version_scope"].startswith("historical")
    ]
    return {"count": len(guides), "guides": guides,
            "limit": limit, "truncated": len(rows) > limit,
            "current_docs_override_rule": "Current official documentation overrides this corpus."}


def retrieve_codeops_guidance(
    task: str,
    surface: str = "",
    limit: int = 3,
    current_docs_checked: bool = False,
    allow_historical: bool = False,
) -> dict:
    task = task.strip()
    if not task:
        raise ValueError("task is required")
    limit = max(1, min(int(limit), 8))
    query_terms = set(re.findall(r"[a-z0-9]+", f"{task} {surface}".lower()))
    with _db() as conn:
        rows = conn.execute("SELECT * FROM codeops_guidance").fetchall()
    ranked = []
    for row in rows:
        guide = _guide_dict(row)
        historical = guide["version_scope"].startswith("historical")
        if historical and not allow_historical:
            continue
        haystack = " ".join([
            guide["canonical_title"], guide["tool_family"], guide["surface"],
            guide["summary"], *guide["appropriate_tasks"],
        ]).lower()
        terms = set(re.findall(r"[a-z0-9]+", haystack))
        score = len(query_terms & terms)
        if surface and surface.lower() in guide["surface"].lower():
            score += 5
        ranked.append((score, guide))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["item_id"]))
    results = []
    for score, guide in ranked[:limit]:
        requires_override = guide["requires_current_docs_check"]
        guide["relevance_score"] = score
        guide["current_docs_override_required"] = requires_override
        guide["caller_asserted_current_docs_check"] = bool(current_docs_checked)
        guide["verified_current_docs_check"] = False
        guide["production_configuration_allowed"] = not requires_override
        results.append(guide)
    details = {
        "task_hash": _hash(task), "surface": surface,
        "current_docs_checked": bool(current_docs_checked),
        "allow_historical": bool(allow_historical),
        "result_item_ids": [result["item_id"] for result in results],
    }
    _audit("retrieve_codeops_guidance", "retrieve_guidance", True, details)
    return {
        "count": len(results),
        "guides": results,
        "current_docs_checked": bool(current_docs_checked),
        "historical_material_allowed": bool(allow_historical),
        "warning": (
            "Historical model names and production configuration must not be "
            "silently translated; current official documentation controls."
        ),
    }


def record_codeops_completion(
    task_id: str,
    final_outcome: str,
    changed_files: list = None,
    learning_evidence: list = None,
    status: str = "completed",
) -> dict:
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError("status must be completed, failed, or cancelled")
    outcome = final_outcome.strip()
    if not outcome:
        raise ValueError("final_outcome is required")
    changed = _as_string_list(changed_files or [], "changed_files")
    learning = _as_string_list(learning_evidence or [], "learning_evidence")
    if any(_secret_path(Path(path)) or ".." in Path(path).parts for path in changed):
        raise ValueError("changed_files contains an unsafe path")
    with _db() as conn:
        row = conn.execute(
            """SELECT validation_results_json, approval_state, status,
                      required_checks_json
               FROM codeops_tasks WHERE task_id=?""",
            (task_id,),
        ).fetchone()
        if not row:
            raise ValueError("unknown codeops task")
        if row["status"] in {"completed", "failed", "cancelled"}:
            raise ValueError("terminal tasks cannot be completed again")
        if status == "completed" and row["approval_state"] != "approved":
            raise ValueError("completed tasks require recorded approval")
        combined_results = json.loads(row["validation_results_json"])
        required_checks = json.loads(row["required_checks_json"])
        current_source_sha256 = _source_tree_digest(
            _resolve_repo(conn.execute(
                "SELECT repo_root FROM codeops_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()[0])
        )
        latest_by_check = {}
        for result in combined_results:
            if result.get("source_sha256") == current_source_sha256:
                latest_by_check[result.get("check_name")] = result
        missing = sorted(
            check for check in required_checks
            if latest_by_check.get(check, {}).get("status") != "passed"
        )
        if status == "completed" and missing:
            raise ValueError(
                "required checks lack passing persisted runs: "
                + ", ".join(missing)
            )
        conn.execute(
            """UPDATE codeops_tasks
               SET status=?, changed_files_json=?, validation_results_json=?,
                   final_outcome=?, learning_evidence_json=?, updated_at=?
               WHERE task_id=?""",
            (
                status, _json(changed), _json(combined_results), outcome,
                _json(learning), _now(), task_id,
            ),
        )
    details = {
        "status": status,
        "final_outcome_sha256": _hash(outcome),
        "changed_files": changed,
        "validation_result_count": len(combined_results),
        "source_sha256": current_source_sha256,
        "learning_evidence": learning,
        "approval_state": row["approval_state"],
    }
    _audit("record_codeops_completion", "record_completion_and_learning",
           status == "completed", details, task_id)
    return {"task_id": task_id, **details,
            "delegated_to_codex_cloud": False}


_ARRAY_STRINGS = {"type": "array", "items": {"type": "string"}}

CODEOPS_SCHEMAS = [
    {"type": "function", "name": "create_codeops_task",
     "description": "Create a governed coding task contract. This records work; it does not delegate to Codex Cloud.",
     "parameters": {"type": "object", "properties": {
         "objective": {"type": "string"}, "repo_root": {"type": "string"},
         "branch": {"type": "string"}, "scope": _ARRAY_STRINGS,
         "acceptance_criteria": _ARRAY_STRINGS,
         "constraints": _ARRAY_STRINGS,
         "allowed_operations": _ARRAY_STRINGS,
         "allowed_network": {"type": "string", "enum": ["none", "current-docs-only"]},
         "required_checks": {"type": "array", "items": {
             "type": "string", "enum": sorted(_SAFE_CHECKS)}}},
         "required": ["objective", "repo_root", "branch", "scope", "acceptance_criteria"]}},
    {"type": "function", "name": "approve_codeops_task",
     "description": "Approval-gated tool that records explicit human approval for a CodeOps task.",
     "parameters": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "approval_evidence": {"type": "string"}},
         "required": ["task_id", "approval_evidence"]}},
    {"type": "function", "name": "get_codeops_task",
     "description": "Get a CodeOps task contract, status, evidence, and optionally its audit trail.",
     "parameters": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "include_audit": {"type": "boolean"}}, "required": ["task_id"]}},
    {"type": "function", "name": "inspect_codeops_repository",
     "description": "Read-only bounded repository inspection under PJ_CODEOPS_ALLOWED_ROOTS.",
     "parameters": {"type": "object", "properties": {
         "repo_root": {"type": "string"}}, "required": ["repo_root"]}},
    {"type": "function", "name": "search_codeops_repository",
     "description": "Literal, bounded, secret-aware source search under an allowed repository.",
     "parameters": {"type": "object", "properties": {
         "repo_root": {"type": "string"}, "query": {"type": "string"},
         "relative_path": {"type": "string"}, "file_glob": {"type": "string"},
         "max_results": {"type": "integer", "minimum": 1, "maximum": _MAX_SEARCH_RESULTS}},
         "required": ["repo_root", "query"]}},
    {"type": "function", "name": "read_codeops_file",
     "description": "Read a bounded range from a non-secret text file under an allowed repository.",
     "parameters": {"type": "object", "properties": {
         "repo_root": {"type": "string"}, "relative_path": {"type": "string"},
         "start_line": {"type": "integer", "minimum": 1},
         "max_lines": {"type": "integer", "minimum": 1, "maximum": 500},
         "max_chars": {"type": "integer", "minimum": 1, "maximum": _FILE_CAP}},
         "required": ["repo_root", "relative_path"]}},
    {"type": "function", "name": "get_codeops_git_evidence",
     "description": "Get bounded git status/diff/stat/name evidence without running arbitrary commands.",
     "parameters": {"type": "object", "properties": {
         "repo_root": {"type": "string"},
         "evidence_type": {"type": "string", "enum": ["diff", "stat", "names", "status"]},
         "base_ref": {"type": "string"}}, "required": ["repo_root"]}},
    {"type": "function", "name": "run_codeops_validation",
     "description": "Approval-gated deterministic project validation. Only detected tests, lint, typecheck, build, or format checks are allowed; arbitrary commands are rejected.",
     "parameters": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "check_name": {"type": "string", "enum": sorted(_SAFE_CHECKS)},
         "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600}},
         "required": ["task_id", "check_name"]}},
    {"type": "function", "name": "import_codeops_guidance",
     "description": "Parse bounded ITEM_START/ITEM_END corpus text into the local CodeOps knowledge table with explicit freshness flags.",
     "parameters": {"type": "object", "properties": {
         "corpus_text": {"type": "string"}, "source_label": {"type": "string"},
         "current_docs_checked": {"type": "boolean"},
         "historical_context_acknowledged": {"type": "boolean"}},
         "required": ["corpus_text"]}},
    {"type": "function", "name": "list_codeops_guides",
     "description": "List corpus-grounded CodeOps guides and freshness metadata.",
     "parameters": {"type": "object", "properties": {
         "surface": {"type": "string"},
         "include_historical": {"type": "boolean"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
         "required": []}},
    {"type": "function", "name": "retrieve_codeops_guidance",
     "description": "Rank CodeOps guidance by task/surface with explicit historical and current-doc override controls.",
     "parameters": {"type": "object", "properties": {
         "task": {"type": "string"}, "surface": {"type": "string"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 8},
         "current_docs_checked": {"type": "boolean"},
         "allow_historical": {"type": "boolean"}}, "required": ["task"]}},
    {"type": "function", "name": "record_codeops_completion",
     "description": "Record final outcome, changed files, validation, and learning evidence for a CodeOps task.",
     "parameters": {"type": "object", "properties": {
         "task_id": {"type": "string"}, "final_outcome": {"type": "string"},
         "changed_files": _ARRAY_STRINGS,
         "learning_evidence": _ARRAY_STRINGS,
         "status": {"type": "string", "enum": ["completed", "failed", "cancelled"]}},
         "required": ["task_id", "final_outcome"]}},
]

CODEOPS_DISPATCH = {
    "create_codeops_task": create_codeops_task,
    "approve_codeops_task": approve_codeops_task,
    "get_codeops_task": get_codeops_task,
    "inspect_codeops_repository": inspect_codeops_repository,
    "search_codeops_repository": search_codeops_repository,
    "read_codeops_file": read_codeops_file,
    "get_codeops_git_evidence": get_codeops_git_evidence,
    "run_codeops_validation": run_codeops_validation,
    "import_codeops_guidance": import_codeops_guidance,
    "list_codeops_guides": list_codeops_guides,
    "retrieve_codeops_guidance": retrieve_codeops_guidance,
    "record_codeops_completion": record_codeops_completion,
}
