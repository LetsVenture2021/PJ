"""
chatlog.py — persistent chat sessions and the "/" command palette for PJ.

Every interactive exchange is stored in SQLite (pj_data.sqlite3):
  chat_sessions  — one row per conversation (title from first message,
                   last_response_id so a session can be resumed with full
                   model-side context).
  chat_messages  — every user/assistant turn, searchable.

Slash commands (type "/" then Tab for completion):
  /            or /tools   command + skill palette
  /help                    same as /
  /chats                   list previous chats
  /resume <n|id>           resume a previous chat (full context)
  /new [title]             start a fresh chat
  /history [n]             show the last n turns of this chat
  /search <keyword>        search across all chat history
  /exit                    quit
"""
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent / "pj_data.sqlite3"


@contextmanager
def _db():
    conn = sqlite3.connect(_DB_PATH)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            title TEXT DEFAULT '',
            last_response_id TEXT,
            channel TEXT DEFAULT 'terminal',
            active_turn_token TEXT,
            active_turn_started_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(chat_sessions)")
        }
        if "channel" not in columns:
            conn.execute(
                "ALTER TABLE chat_sessions ADD COLUMN channel TEXT "
                "DEFAULT 'terminal'"
            )
        if "active_turn_token" not in columns:
            conn.execute(
                "ALTER TABLE chat_sessions ADD COLUMN active_turn_token TEXT"
            )
        if "active_turn_started_at" not in columns:
            conn.execute(
                "ALTER TABLE chat_sessions ADD COLUMN active_turn_started_at TEXT"
            )
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            ts TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_pending_approvals (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            approval_kind TEXT NOT NULL,
            provider_response_id TEXT NOT NULL,
            provider_item_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            server_label TEXT,
            arguments_json TEXT NOT NULL,
            text_format_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            decided_at TEXT)""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_pending_approvals_session "
            "ON chat_pending_approvals(session_id, status, expires_at)"
        )
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------- sessions
def new_session(title: str = "", channel: str = "terminal") -> dict:
    if channel not in ("terminal", "web"):
        raise ValueError("channel must be terminal or web")
    sid = secrets.token_urlsafe(24)
    with _db() as conn:
        conn.execute(
            "INSERT INTO chat_sessions (id, title, channel) VALUES (?,?,?)",
            (sid, title, channel),
        )
    return {
        "id": sid,
        "title": title,
        "last_response_id": None,
        "channel": channel,
    }


def get_session(sid: str) -> dict:
    with _db() as conn:
        row = conn.execute(
            "SELECT id, title, last_response_id, channel, created_at, updated_at "
            "FROM chat_sessions "
            "WHERE id=?", (sid,)).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "title": row[1],
        "last_response_id": row[2],
        "channel": row[3] or "terminal",
        "created_at": row[4],
        "updated_at": row[5],
    }


def latest_session() -> dict:
    with _db() as conn:
        row = conn.execute(
            "SELECT id, title, last_response_id, channel FROM chat_sessions "
            "ORDER BY updated_at DESC LIMIT 1").fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "title": row[1],
        "last_response_id": row[2],
        "channel": row[3] or "terminal",
    }


def list_sessions(limit: int = 15) -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT s.id, s.title, s.created_at, s.updated_at, "
            "(SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id), "
            "s.channel "
            " FROM chat_sessions s ORDER BY s.updated_at DESC LIMIT ?",
            (limit,)).fetchall()
    return [{"id": r[0], "title": r[1] or "(untitled)", "created_at": r[2],
             "updated_at": r[3], "messages": r[4],
             "channel": r[5] or "terminal"} for r in rows]


def record_turn(session: dict, role: str, content: str,
                response_id: str = None):
    with _db() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content) "
            "VALUES (?,?,?)", (session["id"], role, content[:20000]))
        sets = ["updated_at=?"]
        vals = [_now()]
        if response_id:
            sets.append("last_response_id=?")
            vals.append(response_id)
            session["last_response_id"] = response_id
        if role == "user" and not session.get("title"):
            title = content.strip().replace("\n", " ")[:60]
            sets.append("title=?")
            vals.append(title)
            session["title"] = title
        vals.append(session["id"])
        conn.execute(f"UPDATE chat_sessions SET {', '.join(sets)} WHERE id=?",
                     vals)


def history(sid: str, limit: int = 10) -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT role, content, ts FROM chat_messages WHERE session_id=? "
            "ORDER BY id DESC LIMIT ?", (sid, limit)).fetchall()
    return [{"role": r[0], "content": r[1], "ts": r[2]}
            for r in reversed(rows)]


def session_detail(sid: str, message_limit: int = 50) -> dict:
    session = get_session(sid)
    if not session:
        return None
    public = dict(session)
    public.pop("last_response_id", None)
    public["history"] = history(sid, message_limit)
    public["pending_approvals"] = list_pending_approvals(sid)
    return public


def _expire_pending_approvals(conn, sid: str = None):
    params = [_now()]
    where = "status='pending' AND expires_at<=?"
    if sid:
        where += " AND session_id=?"
        params.append(sid)
    conn.execute(
        f"UPDATE chat_pending_approvals SET status='expired', decided_at=? "
        f"WHERE {where}",
        [_now(), *params],
    )


def _approval_from_row(row: tuple, *, include_provider: bool = False) -> dict:
    approval = {
        "approval_id": row[0],
        "session_id": row[1],
        "approval_kind": row[2],
        "name": row[5],
        "server_label": row[6],
        "arguments": json.loads(row[7] or "{}"),
        "status": row[9],
        "created_at": row[10],
        "expires_at": row[11],
    }
    if include_provider:
        approval.update({
            "provider_response_id": row[3],
            "provider_item_id": row[4],
            "text_format": (
                json.loads(row[8]) if row[8] else None
            ),
        })
    return approval


def list_pending_approvals(sid: str) -> list:
    with _db() as conn:
        _expire_pending_approvals(conn, sid)
        rows = conn.execute(
            "SELECT id, session_id, approval_kind, provider_response_id, "
            "provider_item_id, tool_name, server_label, arguments_json, "
            "text_format_json, status, created_at, expires_at "
            "FROM chat_pending_approvals "
            "WHERE session_id=? AND status='pending' "
            "ORDER BY created_at",
            (sid,),
        ).fetchall()
    return [_approval_from_row(row) for row in rows]


def get_pending_approval(sid: str, approval_id: str) -> dict:
    with _db() as conn:
        _expire_pending_approvals(conn, sid)
        row = conn.execute(
            "SELECT id, session_id, approval_kind, provider_response_id, "
            "provider_item_id, tool_name, server_label, arguments_json, "
            "text_format_json, status, created_at, expires_at "
            "FROM chat_pending_approvals "
            "WHERE id=? AND session_id=? AND status='pending'",
            (approval_id, sid),
        ).fetchone()
    return _approval_from_row(row, include_provider=True) if row else None


def pause_session_turn_for_approval(
        session: dict,
        token: str,
        *,
        approval_kind: str,
        provider_response_id: str,
        provider_item_id: str,
        tool_name: str,
        arguments: dict,
        server_label: str = "",
        text_format: dict = None,
        ttl_seconds: int = 900) -> dict:
    if approval_kind not in ("local_function", "mcp"):
        raise ValueError("unsupported approval kind")
    approval_id = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=max(60, min(ttl_seconds, 3600)))
    arguments_json = json.dumps(arguments or {}, default=str)
    text_format_json = (
        json.dumps(text_format, default=str) if text_format else None
    )
    if len(arguments_json) > 50000 or (
        text_format_json and len(text_format_json) > 50000
    ):
        raise ValueError("approval state exceeds the persistence limit")
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT active_turn_token FROM chat_sessions WHERE id=?",
            (session["id"],),
        ).fetchone()
        if not row or not hmac_compare(row[0], token):
            return None
        conn.execute(
            "INSERT INTO chat_pending_approvals "
            "(id, session_id, approval_kind, provider_response_id, "
            "provider_item_id, tool_name, server_label, arguments_json, "
            "text_format_json, created_at, expires_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                approval_id,
                session["id"],
                approval_kind,
                provider_response_id,
                provider_item_id,
                tool_name[:200],
                (server_label or "")[:200] or None,
                arguments_json,
                text_format_json,
                now.isoformat(),
                expires_at.isoformat(),
            ),
        )
        conn.execute(
            "UPDATE chat_sessions SET active_turn_token=NULL, "
            "active_turn_started_at=NULL, updated_at=? WHERE id=?",
            (now.isoformat(), session["id"]),
        )
    return get_pending_approval(session["id"], approval_id)


def decide_pending_approval(
        sid: str, approval_id: str, approve: bool) -> dict:
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _expire_pending_approvals(conn, sid)
        row = conn.execute(
            "SELECT id, session_id, approval_kind, provider_response_id, "
            "provider_item_id, tool_name, server_label, arguments_json, "
            "text_format_json, status, created_at, expires_at "
            "FROM chat_pending_approvals "
            "WHERE id=? AND session_id=? AND status='pending'",
            (approval_id, sid),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE chat_pending_approvals SET status=?, decided_at=? "
            "WHERE id=? AND status='pending'",
            ("approved" if approve else "rejected", _now(), approval_id),
        )
    approval = _approval_from_row(row, include_provider=True)
    approval["status"] = "approved" if approve else "rejected"
    return approval


def claim_session_turn(
        sid: str,
        lease_seconds: int = 600,
        pending_approval_id: str = None) -> str:
    token = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=lease_seconds)
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _expire_pending_approvals(conn, sid)
        pending = conn.execute(
            "SELECT id FROM chat_pending_approvals "
            "WHERE session_id=? AND status='pending'",
            (sid,),
        ).fetchall()
        if pending and (
            not pending_approval_id
            or pending_approval_id not in {row[0] for row in pending}
        ):
            return None
        row = conn.execute(
            "SELECT active_turn_token, active_turn_started_at "
            "FROM chat_sessions WHERE id=?",
            (sid,),
        ).fetchone()
        if not row:
            return None
        started_at = None
        if row[1]:
            try:
                started_at = datetime.fromisoformat(row[1])
            except ValueError:
                started_at = now
        if row[0] and (started_at is None or started_at > stale_before):
            return None
        conn.execute(
            "UPDATE chat_sessions SET active_turn_token=?, "
            "active_turn_started_at=? WHERE id=?",
            (token, now.isoformat(), sid),
        )
    return token


def finish_session_turn(
        session: dict, token: str, content: str, response_id: str) -> bool:
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT active_turn_token FROM chat_sessions WHERE id=?",
            (session["id"],),
        ).fetchone()
        if not row or not hmac_compare(row[0], token):
            return False
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content) "
            "VALUES (?,?,?)",
            (session["id"], "assistant", content[:20000]),
        )
        conn.execute(
            "UPDATE chat_sessions SET last_response_id=?, updated_at=?, "
            "active_turn_token=NULL, active_turn_started_at=NULL WHERE id=?",
            (response_id, _now(), session["id"]),
        )
        session["last_response_id"] = response_id
    return True


def release_session_turn(sid: str, token: str):
    with _db() as conn:
        conn.execute(
            "UPDATE chat_sessions SET active_turn_token=NULL, "
            "active_turn_started_at=NULL WHERE id=? AND active_turn_token=?",
            (sid, token),
        )


def hmac_compare(left, right):
    return bool(left and right and secrets.compare_digest(left, right))


def search(keyword: str, limit: int = 15) -> list:
    like = f"%{keyword}%"
    with _db() as conn:
        rows = conn.execute(
            "SELECT m.session_id, s.title, m.role, m.content, m.ts "
            "FROM chat_messages m JOIN chat_sessions s ON s.id=m.session_id "
            "WHERE m.content LIKE ? ORDER BY m.id DESC LIMIT ?",
            (like, limit)).fetchall()
    return [{"session_id": r[0], "title": r[1] or "(untitled)",
             "role": r[2], "content": r[3], "ts": r[4]} for r in rows]


# -------------------------------------------------------- command palette
_COMMANDS = {
    "/": "Show commands",
    "/chats": "List previous chats",
    "/resume": "/resume <n|id> — resume a previous chat with full context",
    "/new": "/new [title] — start a fresh chat",
    "/history": "/history [n] — show recent turns of this chat",
    "/search": "/search <keyword> — search all chat history",
    "/exit": "Quit",
}

_PALETTE_KEYS = {
    "/": "commands", "#": "tools", "%": "features", "$": "skills",
}

_GROUPS = [
    ("Tasks & Notes", ("add_task", "list_tasks", "complete_task",
                       "save_note", "search_notes", "get_current_time")),
    ("Mac & Browser", ("get_active_browser_tab", "run_shortcut",
                       "list_calendar_events", "create_calendar_event",
                       "list_reminders", "create_reminder")),
    ("Email & People", ("draft_email", "list_recent_emails",
                        "log_contact_interaction", "search_contact_history")),
    ("Portfolio & Pipeline", ("create_project", "update_project",
                              "portfolio_review", "log_opportunity",
                              "update_opportunity", "pipeline_review")),
    ("Commitments & Memory", ("log_commitment", "list_commitments",
                              "complete_commitment", "log_decision",
                              "search_decisions", "log_risk")),
    ("Web & Briefing", ("fetch_url", "check_website", "daily_brief")),
    ("Documents (DocOps)", ("list_doc_templates", "create_doc_template",
                            "draft_document", "revise_document",
                            "finalize_document", "export_document",
                            "list_documents", "get_document")),
    ("Self-Improvement (SkillOps)", ("observe_pattern", "list_observations",
                                     "create_skill", "activate_skill",
                                     "review_skills", "deprecate_skill")),
]


def _fmt_ts(ts: str) -> str:
    return (ts or "")[:16].replace("T", " ")


def _desc(t):
    return t["description"].split(". ")[0][:74]


# Numbered items from the last palette shown, so "#3" / "%2" / "$1" deploy.
_LAST = {"tools": [], "features": [], "skills": [], "commands": []}


def render_commands() -> str:
    items = list(_COMMANDS.items())
    _LAST["commands"] = [c for c, _ in items]
    lines = ["", "  COMMANDS   (select: /<n> or type the command)"]
    for i, (cmd, desc) in enumerate(items, 1):
        lines.append(f"   {i:>2}. {cmd:<10} {desc}")
    lines.append("\n  Palettes:  /  commands   #  tools   %  features   "
                 "$  skills\n")
    return "\n".join(lines)


def render_tools(tool_schemas: list) -> str:
    by_name = {t["name"]: t for t in tool_schemas
               if t.get("type") == "function"}
    ordered, lines = [], ["", "  TOOLS   (deploy: #<n> or #<name> — "
                             "PJ prompts for any arguments)"]
    seen = set()
    for group, names in _GROUPS:
        members = [n for n in names if n in by_name]
        if not members:
            continue
        lines.append(f"\n  {group.upper()}")
        for n in members:
            seen.add(n)
            ordered.append(n)
            lines.append(f"   {len(ordered):>2}. {n:<26} {_desc(by_name[n])}")
    extra = sorted(set(by_name) - seen)
    if extra:
        lines.append("\n  OTHER / GENERATED")
        for n in extra:
            ordered.append(n)
            lines.append(f"   {len(ordered):>2}. {n:<26} {_desc(by_name[n])}")
    _LAST["tools"] = ordered
    lines.append("")
    return "\n".join(lines)


def _feature_defs(cfg):
    """Feature registry: (key, label, kind, is_on, detail)."""
    import pathlib
    mcp_path = pathlib.Path(__file__).resolve().parent / "mcp_servers.json"
    feats = [
        ("streaming", "Streaming responses", "info", True,
         "text, function calls, and structured output always stream"),
        ("structured_output", "Structured output (JSON schema)", "info", True,
         'one-shot: pj.py --json schemas/<schema>.json "msg"'),
        ("web_search", "Web search", "info", True,
         "live internet lookups, always on"),
        ("code_interpreter_enabled", "Code interpreter (Codex sandbox)",
         "toggle", bool(cfg.get("code_interpreter_enabled")),
         "sandboxed python/shell execution; adds latency per call"),
        ("image_generation_enabled", "Image generation", "toggle",
         bool(cfg.get("image_generation_enabled")),
         "create/edit images in responses"),
        ("file_search", "File search (vector store)", "info",
         bool(cfg.get("vector_store_id")),
         f"vector store {cfg.get('vector_store_id', 'not configured')[:28]}"),
        ("tool_search_enabled", "Tool search", "toggle",
         bool(cfg.get("tool_search_enabled")),
         "dynamic tool discovery across the full catalog"),
        ("computer_use_enabled", "Computer use", "toggle",
         bool(cfg.get("computer_use_enabled")),
         "GUI/browser automation (activates when model supports it)"),
        ("reasoning_effort", f"Reasoning effort: "
         f"{cfg.get('reasoning_effort', 'medium')}", "cycle", True,
         "select to cycle low → medium → high"),
    ]
    if mcp_path.exists():
        servers = json.loads(mcp_path.read_text())
        for s in servers:
            feats.append((f"mcp:{s['label']}", f"MCP connector: {s['label']}",
                          "mcp", bool(s.get("enabled")),
                          s.get("url", "")[:44]))
    return feats


def render_features(cfg) -> str:
    feats = _feature_defs(cfg)
    _LAST["features"] = [f[0] for f in feats]
    lines = ["", "  FEATURES   (deploy/toggle: %<n> or %<name>)"]
    for i, (key, label, kind, on, detail) in enumerate(feats, 1):
        mark = "🟢" if on else "⚪"
        suffix = {"info": "", "toggle": "  [toggleable]",
                  "cycle": "  [selectable]", "mcp": "  [toggleable]"}[kind]
        lines.append(f"   {i:>2}. {mark} {label:<36} {detail}{suffix}")
    lines.append("\n  Toggles persist to config.json / mcp_servers.json and "
                 "apply to the next message.\n")
    return "\n".join(lines)


def render_skills() -> str:
    import skillops
    with skillops._db() as conn:
        rows = conn.execute(
            "SELECT name, version, status, description FROM skillops_registry "
            "ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'candidate' "
            "THEN 1 ELSE 2 END, name").fetchall()
    obs = skillops.list_observations("open")
    _LAST["skills"] = [r[0] for r in rows]
    lines = ["", "  SKILLS   (SkillOps registry — deploy: $<n> or $<name>)"]
    if not rows:
        lines.append("   (no generated skills yet — ask PJ to create one, "
                     "or record patterns with observe_pattern)")
    for i, (name, ver, status, desc) in enumerate(rows, 1):
        mark = {"active": "🟢", "candidate": "🟡",
                "deprecated": "⚪"}.get(status, "⚪")
        action = {"active": "select to run", "candidate":
                  "select to activate", "deprecated":
                  "select to re-activate"}.get(status, "")
        lines.append(f"   {i:>2}. {mark} {name:<26} v{ver} [{status}] "
                     f"{desc[:44]}  ({action})")
    lines.append(f"\n  Open observations: {obs['count']}   ·   "
                 "$review runs the lifecycle review\n")
    return "\n".join(lines)


# ---------------------------------------------------------- deployment
def _patch_config(base, updates: dict):
    """Persist config toggles to config.json (in-memory cfg already set)."""
    cfg_path = base / "config.json"
    data = json.loads(cfg_path.read_text())
    data.update(updates)
    cfg_path.write_text(json.dumps(data, indent=2))


def _prompt_args(schema: dict) -> dict:
    """Interactively collect arguments for a tool from its JSON schema."""
    params = schema.get("parameters", {})
    props = params.get("properties", {})
    required = set(params.get("required", []))
    if not props:
        return {}
    print("   (press Enter to skip optional fields)")
    args = {}
    for name, spec in props.items():
        req = " (required)" if name in required else ""
        desc = spec.get("description", "")
        enum = f" one of {spec['enum']}" if "enum" in spec else ""
        while True:
            raw = input(f"   {name}{req}{enum}"
                        f"{' — ' + desc if desc else ''}: ").strip()
            if not raw:
                if name in required:
                    print("   this field is required.")
                    continue
                break
            typ = spec.get("type", "string")
            try:
                if typ == "integer":
                    args[name] = int(raw)
                elif typ == "number":
                    args[name] = float(raw)
                elif typ == "boolean":
                    args[name] = raw.lower() in ("y", "yes", "true", "1")
                else:
                    args[name] = raw
            except ValueError:
                print(f"   expected {typ}, try again.")
                continue
            break
    return args


def _deploy_tool(name: str, tool_schemas: list):
    import skills as _skills
    schema = next((t for t in tool_schemas
                   if t.get("type") == "function" and t["name"] == name), None)
    if not schema:
        print(f"  Unknown tool '{name}'. Press # for the list.")
        return
    print(f"\n  Deploying {name} — {_desc(schema)}")
    try:
        args = _prompt_args(schema)
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return
    result = _skills.dispatch(name, args)
    print("  → " + json.dumps(result, indent=2, default=str)[:3000] + "\n")


def _deploy_feature(key: str, cfg):
    import pathlib
    feats = {f[0]: f for f in _feature_defs(cfg)}
    if key not in feats:
        print(f"  Unknown feature '{key}'. Press % for the list.")
        return
    _, label, kind, on, _ = feats[key]
    base = pathlib.Path(__file__).resolve().parent
    if kind == "info":
        state = "enabled" if on else "not configured"
        print(f"  {label}: {state} (informational — nothing to toggle).")
        return
    if kind == "cycle":
        order = ["low", "medium", "high"]
        cur = cfg.get("reasoning_effort", "medium")
        nxt = order[(order.index(cur) + 1) % 3] if cur in order else "low"
        cfg["reasoning_effort"] = nxt
        _patch_config(base, {"reasoning_effort": nxt})
        print(f"  Reasoning effort → {nxt} (applies to the next message).")
        return
    if kind == "toggle":
        cfg[key] = not on
        _patch_config(base, {key: cfg[key]})
        print(f"  {label} → {'ON' if cfg[key] else 'OFF'} "
              f"(applies to the next message).")
        return
    if kind == "mcp":
        label_name = key.split(":", 1)[1]
        mcp_path = base / "mcp_servers.json"
        servers = json.loads(mcp_path.read_text())
        for s in servers:
            if s["label"] == label_name:
                s["enabled"] = not s.get("enabled", False)
                mcp_path.write_text(json.dumps(servers, indent=2))
                if s["enabled"] and any(
                        "$" in str(v) for v in s.get("headers", {}).values()):
                    print(f"  MCP {label_name} → ON, but its auth env var "
                          "looks unset in ~/.env — it may not authenticate.")
                else:
                    print(f"  MCP {label_name} → "
                          f"{'ON' if s['enabled'] else 'OFF'}.")
                return
        print(f"  MCP server '{label_name}' not found.")


def _deploy_skill(name: str, tool_schemas: list):
    import skillops
    if name == "review":
        print("  Running lifecycle review...")
        print("  → " + json.dumps(skillops.review_skills(), indent=2)[:2500]
              + "\n")
        return
    with skillops._db() as conn:
        row = conn.execute("SELECT status FROM skillops_registry "
                           "WHERE name=?", (name,)).fetchone()
    if not row:
        print(f"  Unknown skill '{name}'. Press $ for the registry.")
        return
    status = row[0]
    if status in ("candidate", "deprecated"):
        r = skillops.activate_skill(name)
        print(f"  → {json.dumps(r)}")
        print("  Restart PJ (or /exit and relaunch) to load it as a tool.")
        return
    # Active: run it directly.
    schemas, dispatch_map = skillops.load_generated_skills()
    schema = next((s for s in schemas if s["name"] == name), None)
    if not schema or name not in dispatch_map:
        print(f"  '{name}' is active but failed to load; check its file in "
              "generated_skills/.")
        return
    print(f"\n  Deploying {name} — {_desc(schema)}")
    try:
        args = _prompt_args(schema)
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return
    try:
        result = dispatch_map[name](**args)
    except Exception as exc:
        result = {"error": str(exc)}
    print("  → " + json.dumps(result, indent=2, default=str)[:3000] + "\n")


def _resolve(sel: str, kind: str) -> str:
    """Resolve '3' or a name against the last-shown palette of a kind."""
    sel = sel.strip()
    items = _LAST.get(kind, [])
    if sel.isdigit():
        idx = int(sel) - 1
        return items[idx] if 0 <= idx < len(items) else None
    return sel


def setup_readline(tool_schemas: list):
    """Tab completion for palette prefixes, commands, and names."""
    try:
        import readline
    except ImportError:
        return
    fn_names = [t["name"] for t in tool_schemas
                if t.get("type") == "function"]
    vocab = (list(_COMMANDS) + ["#" + n for n in fn_names] + fn_names)

    def completer(text, state_i):
        matches = [w for w in vocab if w.startswith(text)]
        return matches[state_i] if state_i < len(matches) else None

    readline.set_completer(completer)
    readline.set_completer_delims(" \t\n")
    readline.parse_and_bind("tab: complete")
    try:  # macOS libedit
        readline.parse_and_bind("bind ^I rl_complete")
    except Exception:
        pass


def handle_command(line: str, session: dict, tool_schemas: list, cfg=None):
    """Handle palette input (/ # % $). Returns (handled, new_session)."""
    stripped = line.strip()
    cfg = cfg if cfg is not None else {}

    # --- palette openers ---
    if stripped == "/":
        print(render_commands())
        return True, None
    if stripped == "#":
        print(render_tools(tool_schemas))
        return True, None
    if stripped == "%":
        print(render_features(cfg))
        return True, None
    if stripped == "$":
        print(render_skills())
        return True, None

    # --- palette selections: #3 / #tool_name / %2 / $1 / $review ---
    if stripped.startswith("#") and len(stripped) > 1:
        name = _resolve(stripped[1:], "tools")
        if name is None:
            print("  No such item. Press # to list tools.")
        else:
            _deploy_tool(name, tool_schemas)
        return True, None
    if stripped.startswith("%") and len(stripped) > 1:
        key = _resolve(stripped[1:], "features")
        if key is None:
            print("  No such item. Press % to list features.")
        else:
            _deploy_feature(key, cfg)
        return True, None
    if stripped.startswith("$") and len(stripped) > 1:
        name = _resolve(stripped[1:], "skills")
        if name is None and stripped[1:].strip() == "review":
            name = "review"
        if name is None:
            print("  No such item. Press $ to list skills.")
        else:
            _deploy_skill(name, tool_schemas)
        return True, None

    # --- slash commands ---
    parts = stripped.split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/help", "/commands", "/tools", "/skills"):
        print(render_commands())
        return True, None

    if cmd.startswith("/") and cmd[1:].isdigit():
        # numeric selection from the commands palette
        target = _resolve(cmd[1:], "commands")
        if target is None:
            print("  No such command number. Press / to list commands.")
            return True, None
        if target in ("/resume", "/search", "/new", "/history"):
            print(f"  {target}: {_COMMANDS[target]}")
            return True, None
        return handle_command(target, session, tool_schemas, cfg)

    if cmd == "/chats":
        sessions = list_sessions()
        if not sessions:
            print("  No previous chats yet.")
            return True, None
        print("\n  #  UPDATED           MSGS  TITLE")
        for i, s in enumerate(sessions, 1):
            marker = "→" if s["id"] == session["id"] else " "
            print(f" {marker}{i:>2}  {_fmt_ts(s['updated_at'])}  "
                  f"{s['messages']:>4}  {s['title'][:52]}  ({s['id']})")
        print("\n  /resume <#> or /resume <id> to continue one.\n")
        return True, None

    if cmd == "/resume":
        if not arg:
            print("  Usage: /resume <#|id>   (see /chats)")
            return True, None
        target = None
        if arg.isdigit():
            sessions = list_sessions()
            idx = int(arg) - 1
            if 0 <= idx < len(sessions):
                target = get_session(sessions[idx]["id"])
        else:
            target = get_session(arg)
        if not target:
            print(f"  No chat found for '{arg}'.")
            return True, None
        print(f"\n  Resumed: {target['title'] or target['id']}")
        for m in history(target["id"], 6):
            who = "You" if m["role"] == "user" else "PJ"
            text = m["content"][:110].replace("\n", " ")
            print(f"    [{_fmt_ts(m['ts'])}] {who}: {text}")
        print()
        return True, target

    if cmd == "/new":
        s = new_session(arg)
        print(f"  Started a new chat{' — ' + arg if arg else ''}.\n")
        return True, s

    if cmd == "/history":
        n = int(arg) if arg.isdigit() else 10
        msgs = history(session["id"], n)
        if not msgs:
            print("  Nothing in this chat yet.")
            return True, None
        print()
        for m in msgs:
            who = "You" if m["role"] == "user" else "PJ"
            print(f"  [{_fmt_ts(m['ts'])}] {who}: "
                  f"{m['content'][:160].replace(chr(10), ' ')}")
        print()
        return True, None

    if cmd == "/search":
        if not arg:
            print("  Usage: /search <keyword>")
            return True, None
        hits = search(arg)
        if not hits:
            print(f"  No matches for '{arg}'.")
            return True, None
        print()
        for h in hits:
            who = "You" if h["role"] == "user" else "PJ"
            print(f"  [{_fmt_ts(h['ts'])}] ({h['title'][:28]}) {who}: "
                  f"{h['content'][:120].replace(chr(10), ' ')}")
        print("\n  /resume <id> to reopen a chat.\n")
        return True, None

    if cmd in ("/exit", "/quit", "/q"):
        raise EOFError

    if line.startswith("/"):
        print(f"  Unknown command '{cmd}'. Type / for the palette.")
        return True, None

    return False, None
