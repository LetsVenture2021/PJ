"""Governed, repository-contained CodeOps knowledge and tools.

CodeOps is read-only by default.  The sole repository mutation is a prepared,
scoped file edit that must pass PJ's central approval policy and is recorded in
an audit table.  Validation execution accepts only identifiers discovered from
existing project files; callers cannot supply commands or shell fragments.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_DB_PATH = _ROOT / "pj_data.sqlite3"

CORPUS_VERSION = "1.0.0"
CORPUS_BUILD_DATE = "2026-07-28"
CORPUS_FRESHNESS_RULE = (
    "Current official documentation overrides this corpus for production "
    "configuration. Historical model names must remain historical and must "
    "not be silently translated into current model names."
)
TASK_WORKFLOW = (
    "inspect", "plan", "execute", "validate", "review", "release", "learn"
)
_MAX_EDIT_BYTES = 500_000
_MAX_SEARCH_FILE_BYTES = 1_000_000
_SKIP_SEARCH_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__"}
_VALIDATION_SCRIPT = re.compile(
    r"^(test|lint|build|check|typecheck|verify|validate)"
    r"(?:(?:[:_-])[a-z0-9._-]+)?$",
    re.IGNORECASE,
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _record(
    doc_id: str,
    title: str,
    source_url: str,
    content_sha256: str,
    surface: str,
    version_scope: str,
    teaches: str,
    aliases: list[str],
    topics: list[str],
    workflow: list[str],
    controls: list[str],
) -> dict:
    historical = version_scope.startswith("historical")
    return {
        "doc_id": doc_id,
        "title": title,
        "aliases": aliases,
        "topics": topics,
        "source": {
            "source_record_id": doc_id,
            "source_page_url": source_url,
            "corpus_version": CORPUS_VERSION,
            "corpus_build_date": CORPUS_BUILD_DATE,
            "content_sha256": content_sha256,
            "version_scope": version_scope,
            "corpus_status": "training_ready_current_docs_override",
            "requires_current_docs_check": True,
        },
        "citation": {
            "label": f"{doc_id} — {title}",
            "url": source_url,
            "retrieved_from_corpus_version": CORPUS_VERSION,
        },
        "surface": surface,
        "guidance": teaches,
        "workflow": workflow,
        "safety_controls": controls,
        "historical_model_caveat": (
            CORPUS_FRESHNESS_RULE if historical else
            "Interface details can drift; verify current official Codex "
            "documentation before production use."
        ),
    }


CODEOPS_RECORDS = (
    _record(
        "DOC-415",
        "Codex Cloud - OpenAI Coding Agent Documentation",
        "https://app.notion.com/p/Codex-Cloud-OpenAI-Coding-Agent-Documentation-60e74d48ba7c4318843908d64ec6e510",
        "e321e08b31d80b85e6601ed9e1b7d6b457303cc49092cdee15ea753f682ddbee",
        "Cloud / web coding agent",
        "verify_current",
        "Codex cloud tasks, repository delegation, parallel background work, "
        "pull requests, and account security.",
        ["codex cloud", "cloud coding agent", "repository delegation"],
        ["cloud", "delegation", "parallel tasks", "pull requests", "MFA", "SSO"],
        [
            "Connect only an authorized repository and branch.",
            "Provide objective, acceptance criteria, relevant paths, constraints, and tests.",
            "Run the bounded task in its isolated environment.",
            "Review logs, diff, tests, security impact, and pull request before merge.",
        ],
        [
            "Use least-privilege repository access.",
            "Do not provide production secrets.",
            "Treat generated pull requests as untrusted until reviewed.",
            "Require MFA/SSO and organization policy where applicable.",
        ],
    ),
    _record(
        "DOC-43",
        "Codex IDE extension",
        "https://app.notion.com/p/Codex-IDE-extension-faf1b3cb2a1b496586d07e5a070ea0ab",
        "6211686a810059255b0ede6e69d91dda00d6f6a178d833d58c62f7f8d6c7207b",
        "IDE extension",
        "verify_current",
        "VS Code and compatible forks, editor-context chat, agent modes, and "
        "model and reasoning controls.",
        ["codex ide", "ide extension", "vscode extension", "editor agent"],
        ["IDE", "VS Code", "surgical edits", "reasoning effort", "agent mode"],
        [
            "Install from an official marketplace and authenticate.",
            "Open the smallest relevant files or selection.",
            "Choose chat or agent mode for the required autonomy.",
            "Set reasoning effort and approval mode deliberately.",
            "Create a Git checkpoint, review edits, and run tests before accepting.",
        ],
        [
            "Prefer explicit approval for file writes and commands.",
            "Use full access only in disposable or tightly controlled environments.",
            "Verify compatibility details against current official documentation.",
            "Review extension updates and organization settings.",
        ],
    ),
    _record(
        "DOC-407",
        "Codex SDK",
        "https://app.notion.com/p/Codex-SDK-2b84c554bc2a80b287cbc2ebf6458824",
        "cc4818d94464281536f407e5fc7934019453f74900424a0d8c159a01a64d5c91",
        "SDK / programmatic and CI integration",
        "verify_current",
        "TypeScript SDK, non-interactive execution, structured output, GitHub "
        "Actions, and CI/CD automation.",
        ["codex sdk", "codex ci", "CI integration", "github actions"],
        ["SDK", "CI", "CI/CD", "structured output", "automation"],
        [
            "Define a dedicated working directory and repository state.",
            "Set sandbox, approval, network, and authentication policies explicitly.",
            "Provide a bounded task and machine-verifiable output schema.",
            "Capture events and final output.",
            "Run independent validation and gate writes, deployment, and merge.",
        ],
        [
            "Run server-side in an isolated worker.",
            "Pin SDK and action versions.",
            "Restrict tokens, network, command privileges, and secrets.",
            "Log run IDs, prompts, tools, diffs, tests, and approvals.",
        ],
    ),
    _record(
        "DOC-405",
        "GPT5.1 Codex",
        "https://app.notion.com/p/GPT5-1-Codex-2b84c554bc2a80348c2fde03b64dda95",
        "b9557a5443b2a31df2449dc5f3531b35f9107285d67e372eba5e1870e8dac583",
        "CLI and shared configuration",
        "historical_2025_era",
        "Historical config.toml, model/provider selection, approval policies, "
        "sandboxing, reasoning, MCP, telemetry, and IDE personalization.",
        ["codex cli", "codex configuration", "config.toml", "gpt5.1 codex"],
        ["CLI", "configuration", "sandbox", "approval", "MCP", "telemetry"],
        [
            "Start with defaults and add only required overrides.",
            "Set model, provider, approval, sandbox, environment, and features.",
            "Configure project instructions and trusted MCP servers.",
            "Test configuration in a disposable repository.",
            "Track approved project configuration without secrets.",
        ],
        [
            "Never store API keys or secrets in tracked configuration.",
            "Treat command, network, MCP, and full-access settings as privileged.",
            "Enable telemetry only when consistent with privacy policy.",
            "Review names and defaults after upgrades.",
        ],
    ),
    _record(
        "DOC-400",
        "GPT-5.1-Codex-Max System Card",
        "https://app.notion.com/p/GPT-5-1-Codex-Max-System-Card-2b74c554bc2a804bbafafe15a4fed343",
        "47cb071201f27a3f29cefbee7df96874c394d838e6bfd0e3fe759af32336033c",
        "GPT-5.1-Codex-Max safety and model profile",
        "historical_2025_era",
        "Historical model capabilities, compaction, long-running agentic "
        "coding, sandbox and network mitigations, and cybersecurity risk.",
        ["codex system card", "gpt-5.1-codex-max", "model safety card"],
        ["safety", "system card", "cybersecurity", "sandbox", "network controls"],
        [
            "Confirm whether the historical model is actually being used.",
            "Apply sandbox and network controls before autonomous work.",
            "Bound the task, resources, duration, and permitted targets.",
            "Monitor actions and preserve an audit trail.",
            "Require human approval for security-sensitive or high-impact operations.",
        ],
        [
            "Do not use capability claims as authorization.",
            "Prohibit offensive cyber activity and unauthorized targets.",
            "Use product and model safety controls together.",
            "Prefer current official model documentation for deployment decisions.",
        ],
    ),
    _record(
        "DOC-393",
        "Guide — Using GPT-5.1 — AI Engineering",
        "https://app.notion.com/p/Guide-Using-GPT-5-1-AI-Engineering-c29b37293f2b4630b565b686c41e4be4",
        "d756741ef2bb18733b2db9be35d5bbd3400e35e9775eb5b49a1b5a00c52be1ce",
        "API engineering guide",
        "historical_2025_era",
        "Historical GPT-5.1 reasoning controls, verbosity, apply_patch, shell, "
        "custom tools, allowed tools, preambles, and migration.",
        ["gpt-5.1 engineering", "AI engineering guide", "apply patch guidance"],
        ["reasoning", "verbosity", "apply_patch", "tools", "migration", "API"],
        [
            "Define the task, desired output, and allowed tools.",
            "Select reasoning effort and verbosity for latency and quality.",
            "Use structured or constrained tools.",
            "Preserve relevant context according to supported API behavior.",
            "Validate patches, commands, and outputs in a sandbox.",
        ],
        [
            "Treat parameters and model names as version-specific.",
            "Allowlist tools rather than exposing every tool.",
            "Validate freeform tool inputs.",
            "Follow current API docs when historical examples conflict.",
        ],
    ),
    _record(
        "DOC-397",
        "Front End Coding with GPT5",
        "https://app.notion.com/p/Front-End-Coding-with-GPT5-2b74c554bc2a802e805fd777d7e7ca63",
        "3c2549f0f6312bd44ee951011708f687045be85fffc656f54f9969cbc09aa3a9",
        "Front-end coding workflow",
        "historical_2025_era",
        "Prompting and evaluation patterns for frontend and full-stack "
        "generation, refactoring, and surgical edits.",
        ["frontend coding", "front end coding", "GPT5 frontend"],
        ["frontend", "full-stack", "accessibility", "visual QA", "responsive UI"],
        [
            "Specify framework, design system, breakpoints, states, and accessibility.",
            "Request a plan and file map before broad changes.",
            "Generate the smallest coherent implementation.",
            "Run type checks, tests, lint, and build.",
            "Inspect desktop and mobile renders and fix visual defects.",
        ],
        [
            "Require accessible semantics and keyboard behavior.",
            "Avoid fabricated assets, dependencies, APIs, or design tokens.",
            "Test loading, empty, error, and permission states.",
            "Do not accept screenshots as proof that behavior is correct.",
        ],
    ),
    _record(
        "DOC-399",
        "5.1 For Developers- Blog",
        "https://app.notion.com/p/5-1-For-Developers-Blog-2b74c554bc2a809ba0a4fc1d54b36a04",
        "839c0d7bd38412fcc19cbdf159705b435ddd6b25bff860456c4eb813ed674f3c",
        "Developer release overview",
        "historical_2025_era",
        "Historical GPT-5.1 speed/reasoning tradeoffs, coding capabilities, "
        "tool support, pricing, and evaluation context.",
        ["gpt-5.1 developer overview", "5.1 for developers", "developer blog"],
        ["developer overview", "pricing", "benchmarks", "evaluation", "latency"],
        [
            "Identify the exact model and API version.",
            "Choose reasoning settings from measured task requirements.",
            "Use representative repository tests rather than benchmark claims alone.",
            "Measure latency, tokens, tool calls, quality, and failure rate.",
            "Re-evaluate when models, prices, or APIs change.",
        ],
        [
            "Treat release claims and benchmarks as time-bound.",
            "Do not infer current availability or pricing from this record.",
            "Use controlled evaluations on the target codebase.",
            "Prefer current first-party documentation for production selection.",
        ],
    ),
)
_RECORDS_BY_ID = {record["doc_id"]: record for record in CODEOPS_RECORDS}


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def get_codeops_guidance(
    action: str = "list",
    doc_id: str = "",
    query: str = "",
    topic: str = "",
    limit: int = 8,
) -> dict:
    """List or inspect citation-ready CodeOps corpus records."""
    if action not in {"list", "inspect"}:
        return {"error": "action must be 'list' or 'inspect'"}
    try:
        limit = max(1, min(8, int(limit)))
    except (TypeError, ValueError):
        return {"error": "limit must be an integer"}

    if doc_id:
        record = _RECORDS_BY_ID.get(str(doc_id).strip().upper())
        matches = [record] if record else []
    else:
        terms = [_normalize(value) for value in (query, topic) if _normalize(value)]
        matches = []
        for record in CODEOPS_RECORDS:
            haystack = _normalize(" ".join([
                record["doc_id"], record["title"], record["guidance"],
                *record["aliases"], *record["topics"],
            ]))
            if all(term in haystack for term in terms):
                matches.append(record)

    if not matches:
        return {
            "error": "No CodeOps guidance matched the requested ID, title, alias, or topic.",
            "query": query or topic or doc_id,
        }
    matches = matches[:limit]
    if action == "inspect":
        return {
            "count": len(matches),
            "records": matches,
            "freshness_rule": CORPUS_FRESHNESS_RULE,
        }
    summaries = [{
        "doc_id": record["doc_id"],
        "title": record["title"],
        "surface": record["surface"],
        "topics": record["topics"],
        "version_scope": record["source"]["version_scope"],
        "citation": record["citation"],
    } for record in matches]
    return {
        "count": len(summaries),
        "records": summaries,
        "corpus_version": CORPUS_VERSION,
        "corpus_build_date": CORPUS_BUILD_DATE,
        "freshness_rule": CORPUS_FRESHNESS_RULE,
    }


def _repository(repository: str) -> Path:
    if not isinstance(repository, str) or not repository.strip():
        raise ValueError("repository must be a non-empty path")
    supplied = Path(repository).expanduser()
    if supplied.is_symlink():
        raise ValueError("repository root must not be a symlink")
    try:
        root = supplied.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("repository does not exist") from exc
    if not root.is_dir():
        raise ValueError("repository must be a directory")
    return root


def _contained_path(
    root: Path,
    relative_path: str,
    *,
    must_exist: bool,
    allow_root: bool = False,
) -> Path:
    if not isinstance(relative_path, str):
        raise ValueError("path must be a string")
    relative_path = relative_path.strip()
    if not relative_path:
        if allow_root:
            return root
        raise ValueError("path must not be empty")
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("path traversal and absolute paths are not allowed")
    candidate = root.joinpath(rel)
    current = root
    for component in rel.parts:
        current = current / component
        if current.exists() or current.is_symlink():
            if current.is_symlink():
                raise ValueError("symlink paths are not allowed")
    try:
        resolved = candidate.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise ValueError("path must remain inside the repository") from exc
    return resolved


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _run(
    root: Path,
    command: list[str],
    *,
    timeout: int = 30,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        env=env,
    )


def _git(root: Path, arguments: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return _run(root, ["git", "-c", "color.ui=false", *arguments], timeout=timeout)


def create_codeops_task_contract(
    repository: str,
    objective: str,
    acceptance_criteria: str = "",
    constraints: str = "",
    prohibited_changes: str = "",
    required_validation: str = "",
    risk: str = "medium",
) -> dict:
    """Create the governed seven-phase contract used by CodeOps work."""
    root = _repository(repository)
    objective = str(objective or "").strip()
    if not objective:
        return {"error": "objective is required"}
    if risk not in {"low", "medium", "high"}:
        return {"error": "risk must be low, medium, or high"}
    seed = f"{root}\0{objective}\0{datetime.now(timezone.utc).isoformat()}"
    contract_id = "codeops-" + hashlib.sha256(seed.encode()).hexdigest()[:12]
    phase_details = {
        "inspect": "Read repository instructions, manifests, tests, relevant code, history, and Git state.",
        "plan": "Define the smallest coherent change, files, assumptions, rollback, approvals, and validation.",
        "execute": "Use bounded tools; remain read-only until an approved scoped edit is prepared.",
        "validate": "Discover and run only existing allowlisted project validation commands.",
        "review": "Review the complete diff for behavior, edge cases, security, accessibility, and unrelated changes.",
        "release": "Require human approval before merge, deployment, migration, or other high-impact action.",
        "learn": "Record outcomes, failed approaches, accepted edits, validation evidence, and guidance updates.",
    }
    return {
        "status": "planned",
        "contract_id": contract_id,
        "repository": str(root),
        "objective": objective,
        "acceptance_criteria": str(acceptance_criteria or "").strip(),
        "constraints": str(constraints or "").strip(),
        "prohibited_changes": str(prohibited_changes or "").strip(),
        "required_validation": str(required_validation or "").strip(),
        "risk": risk,
        "default_access": "read_only",
        "workflow": [
            {
                "order": index,
                "phase": phase,
                "guidance": phase_details[phase],
                "approval_required": phase in {"execute", "release"},
            }
            for index, phase in enumerate(TASK_WORKFLOW, start=1)
        ],
        "traceability": [
            "contract_id", "source_commit", "files_inspected", "files_changed",
            "tool_calls", "validation_results", "approvals", "final_outcome",
        ],
        "freshness_rule": CORPUS_FRESHNESS_RULE,
    }


def inspect_codeops_repository(repository: str) -> dict:
    """Return bounded, read-only Git repository status."""
    root = _repository(repository)
    inside = _git(root, ["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return {"error": "path is not a Git worktree", "repository": str(root)}
    status = _git(root, ["status", "--short", "--branch", "--untracked-files=normal"])
    branch = _git(root, ["branch", "--show-current"])
    head = _git(root, ["rev-parse", "HEAD"])
    lines = status.stdout.splitlines()
    changes = lines[1:] if lines and lines[0].startswith("##") else lines
    return {
        "repository": str(root),
        "branch": branch.stdout.strip() or None,
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "clean": not changes,
        "status": lines[:500],
        "change_count": len(changes),
        "read_only": True,
    }


def search_codeops_repository(
    repository: str,
    query: str,
    path: str = "",
    file_glob: str = "*",
    max_results: int = 50,
) -> dict:
    """Search text literally without leaving the repository or following links."""
    root = _repository(repository)
    query = str(query or "")
    if not query:
        return {"error": "query is required"}
    try:
        max_results = max(1, min(200, int(max_results)))
    except (TypeError, ValueError):
        return {"error": "max_results must be an integer"}
    start = _contained_path(root, path, must_exist=True, allow_root=True)
    if not start.is_dir() and not start.is_file():
        return {"error": "search path must be a regular file or directory"}
    pattern = str(file_glob or "*")
    candidates: list[Path] = []
    if start.is_file():
        candidates = [start]
    else:
        for directory, dirs, files in os.walk(start, followlinks=False):
            base = Path(directory)
            dirs[:] = [
                name for name in dirs
                if name not in _SKIP_SEARCH_DIRS and not (base / name).is_symlink()
            ]
            for name in files:
                candidate = base / name
                if candidate.is_symlink():
                    continue
                rel = _relative(root, candidate)
                if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
                    candidates.append(candidate)
            if len(candidates) > 5000:
                break

    needle = query.casefold()
    matches = []
    skipped = {"binary": 0, "large": 0, "unreadable": 0}
    for candidate in sorted(candidates):
        try:
            if candidate.stat().st_size > _MAX_SEARCH_FILE_BYTES:
                skipped["large"] += 1
                continue
            data = candidate.read_bytes()
            if b"\0" in data:
                skipped["binary"] += 1
                continue
            text = data.decode("utf-8")
        except (OSError, UnicodeError):
            skipped["unreadable"] += 1
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if needle in line.casefold():
                matches.append({
                    "path": _relative(root, candidate),
                    "line": line_number,
                    "excerpt": line[:500],
                })
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break
    return {
        "repository": str(root),
        "query": query,
        "count": len(matches),
        "matches": matches,
        "truncated": len(matches) >= max_results,
        "skipped": skipped,
        "read_only": True,
    }


def _package_runner(root: Path) -> str:
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    if (root / "bun.lockb").is_file() or (root / "bun.lock").is_file():
        return "bun"
    return "npm"


def _validation_commands(root: Path) -> list[dict]:
    commands: list[dict] = []
    tests = root / "tests"
    if tests.is_dir() and any(tests.rglob("test*.py")):
        commands.append({
            "id": "python-unittest",
            "label": "Python stdlib unittest discovery",
            "source": "tests/test*.py",
            "command": [sys.executable, "-m", "unittest", "discover", "tests", "-v"],
        })

    package_json = root / "package.json"
    if package_json.is_file() and not package_json.is_symlink():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
            scripts = package.get("scripts", {})
        except (OSError, UnicodeError, json.JSONDecodeError):
            scripts = {}
        if isinstance(scripts, dict):
            runner = _package_runner(root)
            for name in sorted(scripts):
                if isinstance(name, str) and _VALIDATION_SCRIPT.fullmatch(name):
                    command = (
                        [runner, "run", name] if runner in {"npm", "pnpm", "bun"}
                        else [runner, name]
                    )
                    commands.append({
                        "id": f"{runner}-{name}",
                        "label": f"{runner} project script: {name}",
                        "source": f"package.json#scripts.{name}",
                        "command": command,
                    })

    makefile = next(
        (path for path in (root / "Makefile", root / "makefile")
         if path.is_file() and not path.is_symlink()),
        None,
    )
    if makefile:
        try:
            lines = makefile.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            lines = []
        targets = set()
        for line in lines:
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*):(?:\s|$)", line)
            if match and _VALIDATION_SCRIPT.fullmatch(match.group(1)):
                targets.add(match.group(1))
        for target in sorted(targets):
            commands.append({
                "id": f"make-{target}",
                "label": f"Make validation target: {target}",
                "source": f"{makefile.name}#{target}",
                "command": ["make", target],
            })
    return commands


def discover_codeops_validation(repository: str) -> dict:
    """Discover a closed set of existing validation entry points."""
    root = _repository(repository)
    commands = _validation_commands(root)
    return {
        "repository": str(root),
        "count": len(commands),
        "validations": commands,
        "execution_policy": (
            "Only a returned validation id may be executed. Command text and "
            "arguments cannot be supplied by the caller; shell=False is enforced."
        ),
        "read_only": True,
    }


def _validation_environment() -> dict[str, str]:
    allowed = {
        "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "VIRTUAL_ENV",
        "SYSTEMROOT", "TMPDIR", "TEMP", "TMP",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env["PYTHONUNBUFFERED"] = "1"
    env["CI"] = "1"
    return env


def run_codeops_validation(
    repository: str,
    validation_id: str,
    timeout_seconds: int = 300,
) -> dict:
    """Run one currently discovered project validation, never caller commands."""
    root = _repository(repository)
    try:
        timeout_seconds = max(1, min(600, int(timeout_seconds)))
    except (TypeError, ValueError):
        return {"error": "timeout_seconds must be an integer"}
    commands = {item["id"]: item for item in _validation_commands(root)}
    selected = commands.get(str(validation_id or ""))
    if selected is None:
        return {
            "error": "validation_id is not in the repository's discovered allowlist",
            "allowed_validation_ids": sorted(commands),
        }
    executable = selected["command"][0]
    if not Path(executable).is_absolute() and shutil.which(executable) is None:
        return {"error": f"validation executable is unavailable: {executable}"}
    started = datetime.now(timezone.utc)
    try:
        completed = _run(
            root,
            selected["command"],
            timeout=timeout_seconds,
            env=_validation_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timed_out",
            "validation_id": selected["id"],
            "timeout_seconds": timeout_seconds,
            "stdout": (exc.stdout or "")[-20_000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-20_000:] if isinstance(exc.stderr, str) else "",
        }
    duration_ms = int(
        (datetime.now(timezone.utc) - started).total_seconds() * 1000
    )
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "validation_id": selected["id"],
        "source": selected["source"],
        "command": selected["command"],
        "exit_code": completed.returncode,
        "duration_ms": duration_ms,
        "stdout": completed.stdout[-20_000:],
        "stderr": completed.stderr[-20_000:],
        "allowlisted": True,
    }


def review_codeops_changes(repository: str, max_diff_chars: int = 100_000) -> dict:
    """Produce a read-only Git change review with deterministic risk flags."""
    root = _repository(repository)
    try:
        max_diff_chars = max(1_000, min(500_000, int(max_diff_chars)))
    except (TypeError, ValueError):
        return {"error": "max_diff_chars must be an integer"}
    if _git(root, ["rev-parse", "--is-inside-work-tree"]).returncode != 0:
        return {"error": "path is not a Git worktree"}
    status = _git(root, ["status", "--porcelain=v1", "--untracked-files=normal"])
    diff = _git(
        root,
        ["diff", "--no-ext-diff", "--no-textconv", "--find-renames", "HEAD", "--"],
    )
    if diff.returncode != 0:
        diff = _git(root, ["diff", "--no-ext-diff", "--no-textconv", "--"])
    stat_result = _git(root, ["diff", "--stat", "HEAD", "--"])
    check = _git(root, ["diff", "--check", "HEAD", "--"])
    changed = []
    for line in status.stdout.splitlines():
        raw_path = line[3:] if len(line) > 3 else ""
        changed.append(raw_path.split(" -> ")[-1])
    findings = []
    if check.stdout.strip():
        findings.append({
            "severity": "warning",
            "code": "whitespace_errors",
            "detail": check.stdout.strip()[:4000],
        })
    sensitive = [path for path in changed if _is_sensitive_path(Path(path))]
    if sensitive:
        findings.append({
            "severity": "blocking",
            "code": "sensitive_paths_changed",
            "paths": sensitive,
        })
    full_diff = diff.stdout
    return {
        "repository": str(root),
        "read_only": True,
        "changed_files": changed,
        "change_count": len(changed),
        "diff_stat": stat_result.stdout.strip(),
        "diff": full_diff[:max_diff_chars],
        "diff_truncated": len(full_diff) > max_diff_chars,
        "findings": findings,
        "release_gate": (
            "Human review and approval required before merge or deployment."
        ),
    }


def _is_sensitive_path(relative: Path) -> bool:
    parts = [part.casefold() for part in relative.parts]
    protected_components = {
        ".git", ".ssh", ".aws", ".gnupg", ".kube", "credentials", "secrets",
    }
    if any(part in protected_components for part in parts):
        return True
    name = relative.name.casefold()
    if name == ".env" or name.startswith(".env."):
        return True
    if name in {"id_rsa", "id_ed25519", "authorized_keys", "known_hosts"}:
        return True
    return relative.suffix.casefold() in {
        ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore",
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _db():
    conn = sqlite3.connect(_DB_PATH)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS codeops_edit_approvals (
            token_hash TEXT PRIMARY KEY,
            repository TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            before_sha256 TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS codeops_edit_audit (
            audit_id TEXT PRIMARY KEY,
            event TEXT NOT NULL,
            outcome TEXT NOT NULL,
            repository TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            before_sha256 TEXT,
            after_sha256 TEXT,
            token_hash TEXT,
            detail TEXT,
            created_at TEXT NOT NULL
        )""")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _audit(
    conn: sqlite3.Connection,
    event: str,
    outcome: str,
    root: Path,
    relative_path: str,
    before_sha256: str = "",
    after_sha256: str = "",
    token_hash: str = "",
    detail: str = "",
) -> str:
    audit_id = "coa-" + secrets.token_hex(8)
    conn.execute(
        "INSERT INTO codeops_edit_audit "
        "(audit_id,event,outcome,repository,relative_path,before_sha256,"
        "after_sha256,token_hash,detail,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            audit_id, event, outcome, str(root), relative_path, before_sha256,
            after_sha256, token_hash, detail[:1000],
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    return audit_id


def prepare_codeops_file_edit(
    repository: str,
    path: str,
    content: str,
    expected_sha256: str = "",
    approval_ttl_minutes: int = 30,
) -> dict:
    """Prepare a content-bound approval token without changing the repository."""
    root = _repository(repository)
    if not isinstance(content, str):
        return {"error": "content must be a string"}
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > _MAX_EDIT_BYTES:
        return {"error": f"content exceeds {_MAX_EDIT_BYTES} UTF-8 bytes"}
    try:
        approval_ttl_minutes = max(1, min(60, int(approval_ttl_minutes)))
    except (TypeError, ValueError):
        return {"error": "approval_ttl_minutes must be an integer"}
    target = _contained_path(root, path, must_exist=False)
    relative_path = _relative(root, target)
    if _is_sensitive_path(Path(relative_path)):
        return {"error": "edits to sensitive or repository-control paths are prohibited"}
    if not target.parent.is_dir():
        return {"error": "parent directory must already exist"}
    if target.exists() and not target.is_file():
        return {"error": "edit target must be a regular file or a new file"}
    expected_sha256 = str(expected_sha256 or "").lower()
    if expected_sha256 and not _SHA256.fullmatch(expected_sha256):
        return {"error": "expected_sha256 must be a lowercase SHA-256 digest"}
    before_sha256 = _file_sha256(target) if target.exists() else ""
    if expected_sha256 and expected_sha256 != before_sha256:
        return {
            "error": "expected_sha256 does not match the current file",
            "actual_sha256": before_sha256,
        }
    after_sha256 = hashlib.sha256(content_bytes).hexdigest()
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=approval_ttl_minutes)
    with _db() as conn:
        conn.execute(
            "INSERT INTO codeops_edit_approvals "
            "(token_hash,repository,relative_path,content_sha256,before_sha256,"
            "expires_at,created_at) VALUES (?,?,?,?,?,?,?)",
            (
                token_hash, str(root), relative_path, after_sha256,
                before_sha256, expires_at.isoformat(), now.isoformat(),
            ),
        )
        audit_id = _audit(
            conn, "prepare_file_edit", "pending_approval", root, relative_path,
            before_sha256, after_sha256, token_hash,
            "Content-bound token prepared; repository unchanged.",
        )
    return {
        "status": "pending_approval",
        "approval_token": token,
        "audit_id": audit_id,
        "repository": str(root),
        "path": relative_path,
        "before_sha256": before_sha256 or None,
        "after_sha256": after_sha256,
        "expires_at": expires_at.isoformat(),
        "next": (
            "After explicit human approval, call apply_codeops_file_edit with "
            "this token, identical content, and _approved=true."
        ),
        "repository_changed": False,
    }


def apply_codeops_file_edit(
    repository: str,
    path: str,
    content: str,
    approval_token: str,
    _approval_granted: bool = False,
) -> dict:
    """Apply one prepared edit after central policy approval and audit it."""
    root = _repository(repository)
    if not isinstance(content, str) or not isinstance(approval_token, str):
        return {"error": "content and approval_token must be strings"}
    if not _approval_granted:
        return {"error": "explicit central approval is required"}
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > _MAX_EDIT_BYTES:
        return {"error": f"content exceeds {_MAX_EDIT_BYTES} UTF-8 bytes"}
    target = _contained_path(root, path, must_exist=False)
    relative_path = _relative(root, target)
    if _is_sensitive_path(Path(relative_path)):
        return {"error": "edits to sensitive or repository-control paths are prohibited"}
    token_hash = hashlib.sha256(approval_token.encode()).hexdigest()
    after_sha256 = hashlib.sha256(content_bytes).hexdigest()

    with _db() as conn:
        row = conn.execute(
            "SELECT repository,relative_path,content_sha256,before_sha256,"
            "expires_at,used_at FROM codeops_edit_approvals WHERE token_hash=?",
            (token_hash,),
        ).fetchone()
        if not row:
            return {"error": "approval token is invalid"}
        if row[5]:
            return {"error": "approval token has already been used"}
        try:
            expires_at = datetime.fromisoformat(row[4])
        except ValueError:
            return {"error": "approval token has invalid expiry metadata"}
        if expires_at <= datetime.now(timezone.utc):
            return {"error": "approval token has expired"}
        if row[0] != str(root) or row[1] != relative_path or row[2] != after_sha256:
            return {"error": "approval token does not match repository, path, or content"}
        before_sha256 = _file_sha256(target) if target.exists() else ""
        if before_sha256 != row[3]:
            audit_id = _audit(
                conn, "apply_file_edit", "rejected_stale", root, relative_path,
                before_sha256, after_sha256, token_hash,
                "File changed after token preparation.",
            )
            return {
                "error": "file changed after approval token preparation",
                "audit_id": audit_id,
                "actual_sha256": before_sha256 or None,
            }
        if not target.parent.is_dir():
            return {"error": "parent directory must already exist"}

        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
        temp_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".codeops-edit-",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temp_name = temporary.name
                temporary.write(content_bytes)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temp_name, mode)
            os.replace(temp_name, target)
        finally:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)
        applied_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE codeops_edit_approvals SET used_at=? "
            "WHERE token_hash=? AND used_at IS NULL",
            (applied_at, token_hash),
        )
        audit_id = _audit(
            conn, "apply_file_edit", "applied", root, relative_path,
            before_sha256, after_sha256, token_hash,
            f"Applied {len(content_bytes)} UTF-8 bytes after central approval.",
        )
    return {
        "status": "applied",
        "audit_id": audit_id,
        "repository": str(root),
        "path": relative_path,
        "before_sha256": before_sha256 or None,
        "after_sha256": after_sha256,
        "bytes_written": len(content_bytes),
        "approval_verified": True,
    }


CODEOPS_SCHEMAS = [
    {"type": "function", "name": "get_codeops_guidance",
     "description": "List or inspect citation-ready governed CodeOps records by DOC ID, title, alias, or topic.",
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "inspect"]},
         "doc_id": {"type": "string"},
         "query": {"type": "string"},
         "topic": {"type": "string"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 8},
     }, "required": []}},
    {"type": "function", "name": "create_codeops_task_contract",
     "description": "Create a governed inspect-plan-execute-validate-review-release-learn repository task contract.",
     "parameters": {"type": "object", "properties": {
         "repository": {"type": "string"},
         "objective": {"type": "string"},
         "acceptance_criteria": {"type": "string"},
         "constraints": {"type": "string"},
         "prohibited_changes": {"type": "string"},
         "required_validation": {"type": "string"},
         "risk": {"type": "string", "enum": ["low", "medium", "high"]},
     }, "required": ["repository", "objective"]}},
    {"type": "function", "name": "inspect_codeops_repository",
     "description": "Inspect bounded read-only Git branch, commit, and worktree status.",
     "parameters": {"type": "object", "properties": {
         "repository": {"type": "string"},
     }, "required": ["repository"]}},
    {"type": "function", "name": "search_codeops_repository",
     "description": "Perform a literal, contained text search without following symlinks.",
     "parameters": {"type": "object", "properties": {
         "repository": {"type": "string"},
         "query": {"type": "string"},
         "path": {"type": "string"},
         "file_glob": {"type": "string"},
         "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
     }, "required": ["repository", "query"]}},
    {"type": "function", "name": "discover_codeops_validation",
     "description": "Discover existing project validation entry points and return their allowlisted IDs.",
     "parameters": {"type": "object", "properties": {
         "repository": {"type": "string"},
     }, "required": ["repository"]}},
    {"type": "function", "name": "run_codeops_validation",
     "description": "Execute one discovered validation ID; caller-provided commands and arguments are prohibited.",
     "parameters": {"type": "object", "properties": {
         "repository": {"type": "string"},
         "validation_id": {"type": "string"},
         "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
     }, "required": ["repository", "validation_id"]}},
    {"type": "function", "name": "review_codeops_changes",
     "description": "Read-only review of repository changes, diff, whitespace, and sensitive-path risks.",
     "parameters": {"type": "object", "properties": {
         "repository": {"type": "string"},
         "max_diff_chars": {"type": "integer", "minimum": 1000, "maximum": 500000},
     }, "required": ["repository"]}},
    {"type": "function", "name": "prepare_codeops_file_edit",
     "description": "Prepare a content-bound, expiring approval token for one contained non-sensitive file edit; does not edit the repository.",
     "parameters": {"type": "object", "properties": {
         "repository": {"type": "string"},
         "path": {"type": "string"},
         "content": {"type": "string"},
         "expected_sha256": {"type": "string"},
         "approval_ttl_minutes": {"type": "integer", "minimum": 1, "maximum": 60},
     }, "required": ["repository", "path", "content"]}},
    {"type": "function", "name": "apply_codeops_file_edit",
     "description": "Apply exactly one prepared contained file edit. Requires a matching approval token and explicit central _approved=true gate; returns a durable audit ID.",
     "parameters": {"type": "object", "properties": {
         "repository": {"type": "string"},
         "path": {"type": "string"},
         "content": {"type": "string"},
         "approval_token": {"type": "string"},
         "_approved": {"type": "boolean"},
     }, "required": ["repository", "path", "content", "approval_token", "_approved"]}},
]

CODEOPS_DISPATCH = {
    "get_codeops_guidance": get_codeops_guidance,
    "create_codeops_task_contract": create_codeops_task_contract,
    "inspect_codeops_repository": inspect_codeops_repository,
    "search_codeops_repository": search_codeops_repository,
    "discover_codeops_validation": discover_codeops_validation,
    "run_codeops_validation": run_codeops_validation,
    "review_codeops_changes": review_codeops_changes,
    "prepare_codeops_file_edit": prepare_codeops_file_edit,
    "apply_codeops_file_edit": apply_codeops_file_edit,
}
