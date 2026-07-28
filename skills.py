"""
skills.py — local Python functions exposed to PJ as function-calling tools.

Add a new skill by:
  1. Writing a plain Python function below.
  2. Adding its JSON schema to TOOL_SCHEMAS.
  3. Registering it in DISPATCH_TABLE.

PJ (the model) decides when to call a skill; dispatch() runs the matching
Python function and returns the result, which is fed back into the
conversation.
"""
import json
import os
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from runtime_config import ConfigError, load_tool_policy

_DB_PATH = Path(__file__).resolve().parent / "pj_data.sqlite3"
_TOOL_POLICY_PATH = Path(
    os.getenv("PJ_TOOL_POLICY_PATH",
              str(Path(__file__).resolve().parent / "tool_policy.json"))
)


@contextmanager
def _db():
    conn = sqlite3.connect(_DB_PATH)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                notes TEXT DEFAULT '',
                priority TEXT DEFAULT 'P2',
                status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_current_time(timezone: str = "America/Chicago") -> dict:
    """Return the current date/time in the given IANA timezone."""
    now = datetime.now(ZoneInfo(timezone))
    return {"timezone": timezone, "iso8601": now.isoformat()}


def add_task(title: str, notes: str = "", priority: str = "P2") -> dict:
    """Log a task/commitment to the local SQLite task store."""
    task_id = str(uuid.uuid4())[:8]
    with _db() as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, notes, priority) VALUES (?,?,?,?)",
            (task_id, title, notes, priority),
        )
    return {"status": "logged", "task_id": task_id, "title": title,
            "priority": priority}


def list_tasks(status: str = "open") -> dict:
    """List tasks from the local store, optionally filtered by status."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, title, notes, priority, status, created_at FROM tasks "
            "WHERE status = ? OR ? = 'all' ORDER BY priority, created_at",
            (status, status),
        ).fetchall()
    return {"count": len(rows), "tasks": [
        {"id": r[0], "title": r[1], "notes": r[2], "priority": r[3],
         "status": r[4], "created_at": r[5]} for r in rows]}


def complete_task(task_id: str) -> dict:
    """Mark a task as done by its id."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='done' WHERE id=?", (task_id,))
    return {"task_id": task_id,
            "status": "done" if cur.rowcount else "not_found"}


def save_note(topic: str, content: str) -> dict:
    """Persist a note/memory under a topic for later recall."""
    note_id = str(uuid.uuid4())[:8]
    with _db() as conn:
        conn.execute("INSERT INTO notes (id, topic, content) VALUES (?,?,?)",
                     (note_id, topic, content))
    return {"status": "saved", "note_id": note_id, "topic": topic}


def search_notes(query: str) -> dict:
    """Search saved notes by keyword across topic and content."""
    like = f"%{query}%"
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, topic, content, created_at FROM notes "
            "WHERE topic LIKE ? OR content LIKE ? ORDER BY created_at DESC "
            "LIMIT 20", (like, like)).fetchall()
    return {"count": len(rows), "notes": [
        {"id": r[0], "topic": r[1], "content": r[2], "created_at": r[3]}
        for r in rows]}


def get_active_browser_tab() -> dict:
    """Read the URL and title of the active Google Chrome tab (macOS)."""
    script = ('tell application "Google Chrome" to get '
              '{URL, title} of active tab of front window')
    try:
        out = subprocess.run(["osascript", "-e", script],
                             capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return {"error": out.stderr.strip() or "Chrome not available"}
        url, _, title = out.stdout.strip().partition(", ")
        return {"url": url, "title": title}
    except Exception as exc:
        return {"error": str(exc)}


def run_shortcut(name: str, input_text: str = "") -> dict:
    """Run a macOS Shortcuts automation by name, optionally passing input."""
    cmd = ["shortcuts", "run", name]
    try:
        out = subprocess.run(cmd, input=input_text, capture_output=True,
                             text=True, timeout=120)
        return {"shortcut": name, "exit_code": out.returncode,
                "output": out.stdout.strip()[:2000],
                "error": out.stderr.strip()[:500] or None}
    except Exception as exc:
        return {"error": str(exc)}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "get_current_time",
        "description": "Get the current date and time in a given IANA timezone.",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone name, e.g. America/Chicago",
                }
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "add_task",
        "description": "Log a task or commitment for later triage and follow-up.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short task title"},
                "notes": {"type": "string", "description": "Additional context"},
                "priority": {
                    "type": "string",
                    "enum": ["P0", "P1", "P2", "P3"],
                    "description": "Priority level",
                },
            },
            "required": ["title"],
        },
    },
    {
        "type": "function",
        "name": "list_tasks",
        "description": "List logged tasks, filtered by status (open, done, or all).",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "done", "all"],
                            "description": "Which tasks to list"},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "complete_task",
        "description": "Mark a previously logged task as done.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task id from list_tasks"},
            },
            "required": ["task_id"],
        },
    },
    {
        "type": "function",
        "name": "save_note",
        "description": "Persist a note or memory under a topic so it can be recalled later.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Short topic/category"},
                "content": {"type": "string", "description": "The note content"},
            },
            "required": ["topic", "content"],
        },
    },
    {
        "type": "function",
        "name": "search_notes",
        "description": "Search previously saved notes by keyword.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword to search for"},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "get_active_browser_tab",
        "description": "Get the URL and title of the user's active Google Chrome tab.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "run_shortcut",
        "description": "Run a macOS Shortcuts automation by name, optionally passing text input.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact Shortcut name"},
                "input_text": {"type": "string", "description": "Optional input passed to the shortcut"},
            },
            "required": ["name"],
        },
    },
]

DISPATCH_TABLE = {
    "get_current_time": get_current_time,
    "add_task": add_task,
    "list_tasks": list_tasks,
    "complete_task": complete_task,
    "save_note": save_note,
    "search_notes": search_notes,
    "get_active_browser_tab": get_active_browser_tab,
    "run_shortcut": run_shortcut,
}

# --- SkillOps: PJ's self-improvement engine ---------------------------------
# Adds observe/create/review/deprecate meta-skills and dynamically loads any
# PJ-generated skills that have been activated. See skillops.py.
import time as _time
import skillops as _skillops

TOOL_SCHEMAS.extend(_skillops.SKILLOPS_SCHEMAS)
DISPATCH_TABLE.update(_skillops.SKILLOPS_DISPATCH)

# --- DocOps: mission-critical document engine --------------------------------
# Versioned templates, validated drafts, hash-sealed finals with review gates
# and supersession lineage. See docops.py.
import docops as _docops

TOOL_SCHEMAS.extend(_docops.DOCOPS_SCHEMAS)
DISPATCH_TABLE.update(_docops.DOCOPS_DISPATCH)

# --- ChiefOps: executive operations toolkit ----------------------------------
# Calendar, reminders, mail drafts, relationship memory, portfolio,
# commitments, revenue pipeline, decision journal, risk register, web
# checks, daily brief. See chiefops.py.
import chiefops as _chiefops

TOOL_SCHEMAS.extend(_chiefops.CHIEFOPS_SCHEMAS)
DISPATCH_TABLE.update(_chiefops.CHIEFOPS_DISPATCH)

# --- StrategyOps: goal contracts + evidence + memory replay ------------------
import strategyops as _strategyops

TOOL_SCHEMAS.extend(_strategyops.STRATEGYOPS_SCHEMAS)
DISPATCH_TABLE.update(_strategyops.STRATEGYOPS_DISPATCH)

# --- CodeOps: governed coding task and repository toolkit --------------------
import codeops as _codeops

TOOL_SCHEMAS.extend(_codeops.CODEOPS_SCHEMAS)
DISPATCH_TABLE.update(_codeops.CODEOPS_DISPATCH)


# --- ImageOps: governed image assets and opt-in generation -------------------
import imageops as _imageops

TOOL_SCHEMAS.extend(_imageops.IMAGEOPS_SCHEMAS)
DISPATCH_TABLE.update(_imageops.IMAGEOPS_DISPATCH)


def get_pj_capability_snapshot() -> dict:
    """Return a bounded, secret-safe inventory for evidence-grounded work."""
    import responses_runtime as _responses_runtime
    from pj_contract import CONTRACT_VERSION
    from realtime_config import realtime_tool_schemas

    manifest = _responses_runtime.capability_manifest(
        _responses_runtime.load_config()
    )
    docops_inventory = _docops.docops_inventory_summary()
    coding = _skillops.list_coding_capabilities(limit=100)
    n8n = _skillops.get_n8n_corpus_status(include_census=False)
    guides = _codeops.list_codeops_guides(limit=50)
    sync = _skillops.get_vector_sync_status(limit=1)
    latest_sync = sync.get("runs", [{}])[0] if sync.get("runs") else None
    return {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verification_scope": "local_runtime_and_durable_registry",
        "contract_version": CONTRACT_VERSION,
        "model": manifest["model"],
        "native_capabilities": manifest["native"],
        "local_function_count": manifest["local_functions"]["count"],
        "realtime_function_count": len(realtime_tool_schemas()),
        "mcp_servers": [
            {
                "label": server["label"],
                "status": server["status"],
                "runtime_enabled": server["runtime_enabled"],
                "approval_flow": server["approval_flow"],
            }
            for server in manifest["mcp_servers"]
        ],
        "docops": docops_inventory,
        "coding_capabilities": coding.get("count", 0),
        "n8n_capabilities": {
            "count": n8n.get("capability_count", 0),
            "registry_version": n8n.get("registry_version", 0),
            "status": n8n.get("status", "blocked"),
            "production_ready": bool(n8n.get("production_ready")),
            "blocked_reasons": n8n.get("blocked_reasons", []),
            "release_gates": n8n.get("release_gates", {}),
        },
        "codeops_guides": guides.get("count", 0),
        "imageops": _imageops.get_image_capability_status(),
        "vector_sync": (
            {
                key: latest_sync.get(key)
                for key in (
                    "run_id",
                    "status",
                    "files_seen",
                    "files_processed",
                    "files_skipped_unchanged",
                    "files_failed",
                    "started_at",
                    "finished_at",
                )
            }
            if latest_sync
            else {"status": "no_recorded_sync"}
        ),
        "secret_values_included": False,
    }


TOOL_SCHEMAS.append({
    "type": "function",
    "name": "get_pj_capability_snapshot",
    "description": (
        "Return a current, bounded, secret-safe PJ capability and corpus "
        "inventory for evidence-grounded briefs and presentations. Use this "
        "instead of inferring production facts from the protected homepage."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
})
DISPATCH_TABLE["get_pj_capability_snapshot"] = get_pj_capability_snapshot

_gen_schemas, _gen_dispatch = _skillops.load_generated_skills()
TOOL_SCHEMAS.extend(_gen_schemas)
DISPATCH_TABLE.update(_gen_dispatch)

_POLICY_MODES = {"allow", "deny", "approval"}
_BUILTIN_APPROVAL_TOOLS = {
    "approve_codeops_task",
    "create_skill",
    "learn_from_vector_store",
    "run_codeops_validation",
    "run_shortcut",
    "sync_vector_store",
    "generate_image_asset",
    "edit_image_asset",
    "create_image_variation",
    "delete_image_asset",
}


def _parse_tool_csv(env_name: str) -> set:
    raw = os.getenv(env_name, "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _load_tool_policy() -> dict:
    policy = {
        "default": "allow",
        "tools": {name: "approval" for name in _BUILTIN_APPROVAL_TOOLS},
    }
    try:
        loaded = load_tool_policy(_TOOL_POLICY_PATH)
    except ConfigError:
        if _TOOL_POLICY_PATH.exists():
            raise
        loaded = {"default": "allow", "tools": {}}
    policy["default"] = loaded["default"]
    policy["tools"].update(loaded["tools"])
    return policy


def _tool_policy_mode(tool_name: str) -> str:
    policy = _load_tool_policy()
    mode = policy["tools"].get(tool_name, policy["default"])
    return mode if mode in _POLICY_MODES else "allow"


def tool_policy_mode(tool_name: str) -> str:
    return _tool_policy_mode(tool_name)


def dispatch(name: str, arguments: dict, *, approval_granted: bool = False):
    fn = DISPATCH_TABLE.get(name)
    if fn is None:
        return {"error": f"Unknown skill: {name}"}
    if not isinstance(arguments, dict):
        return {"error": f"Invalid arguments for skill '{name}': expected object"}
    args = dict(arguments)
    untrusted_approval = bool(args.pop("_approved", False))
    if untrusted_approval:
        _skillops.record_invocation(
            name, False, 0, "blocked_untrusted_approval_argument"
        )
        return {
            "error": (
                "Tool approval cannot be supplied through model or HTTP "
                "arguments; a trusted server-side approval is required."
            )
        }
    approval_granted = bool(approval_granted)
    policy_mode = _tool_policy_mode(name)
    if policy_mode == "deny":
        _skillops.record_invocation(name, False, 0, "blocked_by_policy_deny")
        return {"error": f"Tool '{name}' is blocked by policy (deny)."}
    if policy_mode == "approval" and not approval_granted:
        _skillops.record_invocation(name, False, 0, "blocked_by_policy_approval")
        return {
            "error": (
                f"Tool '{name}' requires explicit approval from a trusted "
                "server-side or local human flow."
            )
        }
    start = _time.monotonic()
    try:
        result = fn(**args)
    except TypeError as exc:
        result = {"error": f"tool_argument_error: {exc}"}
    except ValueError as exc:
        result = {"error": f"tool_value_error: {exc}"}
    except RuntimeError as exc:
        result = {"error": f"tool_runtime_error: {exc}"}
    except Exception as exc:  # keep PJ running even if a skill errors
        result = {"error": f"tool_unhandled_error: {exc}"}
    latency_ms = int((_time.monotonic() - start) * 1000)
    err = result.get("error") if isinstance(result, dict) else None
    _skillops.record_invocation(name, err is None, latency_ms, err)
    return result
