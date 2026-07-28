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
import hashlib
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
            external_id TEXT,
            source TEXT DEFAULT 'chat',
            response_id TEXT,
            status TEXT DEFAULT 'completed',
            interrupted_at TEXT,
            playback_ms INTEGER,
            metadata_json TEXT DEFAULT '{}',
            ts TEXT DEFAULT CURRENT_TIMESTAMP)""")
        message_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(chat_messages)")
        }
        for name, definition in (
            ("external_id", "TEXT"),
            ("source", "TEXT DEFAULT 'chat'"),
            ("response_id", "TEXT"),
            ("status", "TEXT DEFAULT 'completed'"),
            ("interrupted_at", "TEXT"),
            ("playback_ms", "INTEGER"),
            ("metadata_json", "TEXT DEFAULT '{}'"),
        ):
            if name not in message_columns:
                conn.execute(
                    f"ALTER TABLE chat_messages ADD COLUMN {name} {definition}"
                )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_chat_messages_external_id "
            "ON chat_messages(session_id, external_id) "
            "WHERE external_id IS NOT NULL"
        )
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
            deliverable_format TEXT,
            artifact_ids_json TEXT,
            artifact_hashes_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            decided_at TEXT,
            execution_result_json TEXT)""")
        approval_columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(chat_pending_approvals)"
            )
        }
        if "deliverable_format" not in approval_columns:
            conn.execute(
                "ALTER TABLE chat_pending_approvals "
                "ADD COLUMN deliverable_format TEXT"
            )
        if "artifact_ids_json" not in approval_columns:
            conn.execute(
                "ALTER TABLE chat_pending_approvals "
                "ADD COLUMN artifact_ids_json TEXT"
            )
        if "artifact_hashes_json" not in approval_columns:
            conn.execute(
                "ALTER TABLE chat_pending_approvals "
                "ADD COLUMN artifact_hashes_json TEXT"
            )
        if "execution_result_json" not in approval_columns:
            conn.execute(
                "ALTER TABLE chat_pending_approvals "
                "ADD COLUMN execution_result_json TEXT"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_pending_approvals_session "
            "ON chat_pending_approvals(session_id, status, expires_at)"
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_session_artifacts (
            session_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            linked_at TEXT NOT NULL,
            PRIMARY KEY (session_id, artifact_id)
        )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_session_artifacts_session "
            "ON chat_session_artifacts(session_id, linked_at)"
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_tool_executions (
            session_id TEXT NOT NULL,
            execution_key TEXT NOT NULL,
            approval_id TEXT,
            tool_name TEXT NOT NULL,
            arguments_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            execution_token TEXT NOT NULL,
            result_json TEXT,
            artifact_ids_json TEXT NOT NULL DEFAULT '[]',
            artifact_hashes_json TEXT NOT NULL DEFAULT '{}',
            started_at TEXT NOT NULL,
            completed_at TEXT,
            PRIMARY KEY (session_id, execution_key)
        )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_tool_executions_approval "
            "ON chat_tool_executions(session_id, approval_id)"
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_provider_checkpoints (
            session_id TEXT NOT NULL,
            operation_key TEXT NOT NULL,
            response_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, operation_key)
        )""")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------- sessions
def new_session(title: str = "", channel: str = "terminal") -> dict:
    if channel not in ("terminal", "web", "realtime"):
        raise ValueError("channel must be terminal, web, or realtime")
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


def record_external_turn(
        session: dict,
        role: str,
        content: str,
        *,
        external_id: str,
        source: str,
        response_id: str = None,
        status: str = "completed",
        playback_ms: int = None,
        metadata: dict = None) -> dict:
    if role not in {"user", "assistant"}:
        raise ValueError("external turn role must be user or assistant")
    if source not in {"typed", "input_audio", "output_audio", "output_text"}:
        raise ValueError("external turn source is invalid")
    if (
        role == "user" and source not in {"typed", "input_audio"}
    ) or (
        role == "assistant" and source not in {"output_audio", "output_text"}
    ):
        raise ValueError("external turn role and source are incompatible")
    if status not in {"completed", "interrupted", "failed"}:
        raise ValueError("external turn status is invalid")
    if role == "user" and status != "completed":
        raise ValueError("user external turns must be completed")
    if (
        not isinstance(external_id, str)
        or not external_id
        or len(external_id) > 200
    ):
        raise ValueError("external_id is invalid")
    if (
        not isinstance(content, str)
        or not content.strip()
        or len(content) > 20000
    ):
        raise ValueError("external turn content is invalid")
    if response_id is not None and (
        not isinstance(response_id, str) or len(response_id) > 200
    ):
        raise ValueError("response_id is invalid")
    if playback_ms is not None and (
        isinstance(playback_ms, bool)
        or not isinstance(playback_ms, int)
        or not 0 <= playback_ms <= 86_400_000
    ):
        raise ValueError("playback_ms is invalid")
    if playback_ms is not None and not (
        role == "assistant" and status == "interrupted"
    ):
        raise ValueError("playback_ms is only valid for interrupted assistant turns")
    metadata = metadata or {}
    if not isinstance(metadata, dict):
        raise ValueError("external turn metadata must be an object")
    metadata_json = json.dumps(
        metadata, sort_keys=True, separators=(",", ":"), default=str
    )
    if len(metadata_json) > 10000:
        raise ValueError("external turn metadata exceeds the persistence limit")
    now = _now()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT role, content, source, response_id, status, playback_ms, "
            "metadata_json FROM chat_messages "
            "WHERE session_id=? AND external_id=?",
            (session["id"], external_id),
        ).fetchone()
        expected = (
            role,
            content,
            source,
            response_id,
            status,
            playback_ms,
            metadata_json,
        )
        if existing:
            if tuple(existing) != expected:
                raise ValueError(
                    "external_id is already bound to a different terminal message"
                )
            return {
                "external_id": external_id,
                "role": role,
                "content": content,
                "source": source,
                "response_id": response_id,
                "status": status,
                "playback_ms": playback_ms,
                "metadata": metadata,
            }
        conn.execute(
            "INSERT INTO chat_messages "
            "(session_id, role, content, external_id, source, response_id, "
            "status, interrupted_at, playback_ms, metadata_json, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                session["id"],
                role,
                content,
                external_id,
                source,
                response_id,
                status,
                now if status == "interrupted" else None,
                playback_ms,
                metadata_json,
                now,
            ),
        )
        sets = ["updated_at=?"]
        values = [now]
        if role == "user" and not session.get("title") and content.strip():
            session["title"] = content.strip().replace("\n", " ")[:60]
            sets.append("title=?")
            values.append(session["title"])
        values.append(session["id"])
        conn.execute(
            f"UPDATE chat_sessions SET {', '.join(sets)} WHERE id=?",
            values,
        )
    return {
        "external_id": external_id,
        "role": role,
        "content": content,
        "source": source,
        "response_id": response_id,
        "status": status,
        "playback_ms": playback_ms,
        "metadata": metadata,
    }


def history(sid: str, limit: int = 10) -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT role, content, ts, external_id, source, response_id, "
            "status, interrupted_at, playback_ms, metadata_json "
            "FROM chat_messages WHERE session_id=? "
            "ORDER BY id DESC LIMIT ?", (sid, limit)).fetchall()
    return [
        {
            "role": row[0],
            "content": row[1],
            "ts": row[2],
            "external_id": row[3],
            "source": row[4] or "chat",
            "response_id": row[5],
            "status": row[6] or "completed",
            "interrupted_at": row[7],
            "playback_ms": row[8],
            "metadata": json.loads(row[9] or "{}"),
        }
        for row in reversed(rows)
    ]


def session_detail(sid: str, message_limit: int = 50) -> dict:
    session = get_session(sid)
    if not session:
        return None
    public = dict(session)
    public.pop("last_response_id", None)
    public["history"] = history(sid, message_limit)
    public["pending_approvals"] = list_pending_approvals(sid)
    public["artifact_ids"] = list_session_artifact_ids(sid)
    return public


def link_session_artifact(sid: str, artifact_id: str) -> bool:
    """Persist an artifact relationship only for an existing chat session."""
    with _db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM chat_sessions WHERE id=?", (sid,)
        ).fetchone()
        if not exists:
            return False
        conn.execute(
            "INSERT INTO chat_session_artifacts "
            "(session_id, artifact_id, linked_at) VALUES (?,?,?) "
            "ON CONFLICT(session_id, artifact_id) DO NOTHING",
            (sid, artifact_id, _now()),
        )
    return True


def list_session_artifact_ids(sid: str) -> list[str]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT artifact_id FROM chat_session_artifacts "
            "WHERE session_id=? ORDER BY linked_at",
            (sid,),
        ).fetchall()
    return [row[0] for row in rows]


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
    if row[9] in ("executing_approved", "executing_rejected"):
        approval["execution_decision"] = row[9] == "executing_approved"
    if include_provider:
        approval.update({
            "provider_response_id": row[3],
            "provider_item_id": row[4],
            "text_format": (
                json.loads(row[8]) if row[8] else None
            ),
            "deliverable_format": row[12],
            "artifact_ids": json.loads(row[13] or "[]"),
            "artifact_hashes": json.loads(row[14] or "{}"),
            "execution_result_recorded": row[15] is not None,
            "execution_result": (
                json.loads(row[15]) if row[15] is not None else None
            ),
        })
    return approval


def list_pending_approvals(sid: str) -> list:
    with _db() as conn:
        _expire_pending_approvals(conn, sid)
        rows = conn.execute(
            "SELECT id, session_id, approval_kind, provider_response_id, "
            "provider_item_id, tool_name, server_label, arguments_json, "
            "text_format_json, status, created_at, expires_at, "
            "deliverable_format, artifact_ids_json, artifact_hashes_json, "
            "execution_result_json "
            "FROM chat_pending_approvals "
            "WHERE session_id=? AND status IN "
            "('pending','executing_approved','executing_rejected') "
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
            "text_format_json, status, created_at, expires_at, "
            "deliverable_format, artifact_ids_json, artifact_hashes_json, "
            "execution_result_json "
            "FROM chat_pending_approvals "
            "WHERE id=? AND session_id=? AND status IN "
            "('pending','executing_approved','executing_rejected')",
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
        deliverable_format: str = None,
        artifact_ids: list[str] = None,
        artifact_hashes: dict[str, str] = None,
        completed_approval_id: str = None,
        completed_approval_decision: bool = None,
        ttl_seconds: int = 900) -> dict:
    if approval_kind not in ("local_function", "mcp"):
        raise ValueError("unsupported approval kind")
    if (completed_approval_id is None) != (
        completed_approval_decision is None
    ) or (
        completed_approval_decision is not None
        and not isinstance(completed_approval_decision, bool)
    ):
        raise ValueError("completed approval state is invalid")
    approval_id = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=max(60, min(ttl_seconds, 3600)))
    arguments_json = json.dumps(arguments or {}, default=str)
    text_format_json = (
        json.dumps(text_format, default=str) if text_format else None
    )
    artifact_ids = list(dict.fromkeys(artifact_ids or []))
    if len(artifact_ids) > 50 or any(
        not isinstance(artifact_id, str)
        or len(artifact_id) != 36
        or not artifact_id.startswith("ART-")
        for artifact_id in artifact_ids
    ):
        raise ValueError("approval artifact state is invalid")
    artifact_ids_json = json.dumps(artifact_ids)
    artifact_hashes = artifact_hashes or {}
    if (
        set(artifact_hashes) != set(artifact_ids)
        or any(
            not isinstance(sha, str)
            or len(sha) != 64
            or any(char not in "0123456789abcdef" for char in sha)
            for sha in artifact_hashes.values()
        )
    ):
        raise ValueError("approval artifact hashes are invalid")
    artifact_hashes_json = json.dumps(
        artifact_hashes, sort_keys=True, separators=(",", ":")
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
        if completed_approval_id and not _complete_approval(
            conn,
            session["id"],
            completed_approval_id,
            completed_approval_decision,
        ):
            return None
        conn.execute(
            "INSERT INTO chat_pending_approvals "
            "(id, session_id, approval_kind, provider_response_id, "
            "provider_item_id, tool_name, server_label, arguments_json, "
            "text_format_json, created_at, expires_at, deliverable_format, "
            "artifact_ids_json, artifact_hashes_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                (deliverable_format or "")[:20] or None,
                artifact_ids_json,
                artifact_hashes_json,
            ),
        )
        conn.execute(
            "UPDATE chat_sessions SET active_turn_token=NULL, "
            "active_turn_started_at=NULL, updated_at=? WHERE id=?",
            (now.isoformat(), session["id"]),
        )
    return get_pending_approval(session["id"], approval_id)


def begin_pending_approval_execution(
        sid: str, approval_id: str, approve: bool) -> dict:
    desired_status = "executing_approved" if approve else "executing_rejected"
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _expire_pending_approvals(conn, sid)
        row = conn.execute(
            "SELECT id, session_id, approval_kind, provider_response_id, "
            "provider_item_id, tool_name, server_label, arguments_json, "
            "text_format_json, status, created_at, expires_at, "
            "deliverable_format, artifact_ids_json, artifact_hashes_json, "
            "execution_result_json "
            "FROM chat_pending_approvals "
            "WHERE id=? AND session_id=? AND status IN "
            "('pending','executing_approved','executing_rejected')",
            (approval_id, sid),
        ).fetchone()
        if not row or row[9] not in ("pending", desired_status):
            return None
        if row[9] == "pending":
            conn.execute(
                "UPDATE chat_pending_approvals SET status=?, decided_at=? "
                "WHERE id=? AND status='pending'",
                (desired_status, _now(), approval_id),
            )
    approval = _approval_from_row(row, include_provider=True)
    approval["status"] = desired_status
    return approval


def store_pending_approval_execution(
        sid: str,
        approval_id: str,
        approve: bool,
        result,
        artifact_ids: list[str],
        artifact_hashes: dict[str, str]) -> bool:
    artifact_ids = list(dict.fromkeys(artifact_ids))
    if set(artifact_hashes) != set(artifact_ids):
        raise ValueError("approval execution artifact hashes are invalid")
    expected = "executing_approved" if approve else "executing_rejected"
    result_json = json.dumps(
        result, default=str, sort_keys=True, separators=(",", ":")
    )
    artifact_ids_json = json.dumps(artifact_ids)
    artifact_hashes_json = json.dumps(
        artifact_hashes, sort_keys=True, separators=(",", ":")
    )
    with _db() as conn:
        cursor = conn.execute(
            "UPDATE chat_pending_approvals SET execution_result_json=?, "
            "artifact_ids_json=?, artifact_hashes_json=? "
            "WHERE id=? AND session_id=? AND status=?",
            (
                result_json,
                artifact_ids_json,
                artifact_hashes_json,
                approval_id,
                sid,
                expected,
            ),
        )
    return cursor.rowcount == 1


def _complete_approval(
        conn, sid: str, approval_id: str, approve: bool) -> bool:
    expected = "executing_approved" if approve else "executing_rejected"
    terminal = "approved" if approve else "rejected"
    cursor = conn.execute(
        "UPDATE chat_pending_approvals SET status=?, decided_at=? "
        "WHERE id=? AND session_id=? AND status=?",
        (terminal, _now(), approval_id, sid, expected),
    )
    return cursor.rowcount == 1


def complete_pending_approval(
        sid: str, approval_id: str, approve: bool) -> bool:
    with _db() as conn:
        return _complete_approval(conn, sid, approval_id, approve)


def retry_pending_approval(sid: str, approval_id: str) -> bool:
    with _db() as conn:
        cursor = conn.execute(
            "UPDATE chat_pending_approvals SET status='pending', "
            "decided_at=NULL WHERE id=? AND session_id=? "
            "AND status IN ('executing_approved','executing_rejected') "
            "AND execution_result_json IS NULL",
            (approval_id, sid),
        )
    return cursor.rowcount == 1


def mark_pending_approval_execution_unknown(
        sid: str, approval_id: str, approve: bool) -> bool:
    expected = "executing_approved" if approve else "executing_rejected"
    with _db() as conn:
        cursor = conn.execute(
            "UPDATE chat_pending_approvals SET status='execution_unknown', "
            "decided_at=? WHERE id=? AND session_id=? AND status=? "
            "AND execution_result_json IS NULL",
            (_now(), approval_id, sid, expected),
        )
    return cursor.rowcount == 1


def _execution_arguments_sha256(tool_name: str, arguments: dict) -> str:
    payload = json.dumps(
        {"tool_name": tool_name, "arguments": arguments or {}},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _execution_from_row(row: tuple) -> dict:
    return {
        "state": row[0],
        "result": json.loads(row[1]) if row[1] is not None else None,
        "artifact_ids": json.loads(row[2] or "[]"),
        "artifact_hashes": json.loads(row[3] or "{}"),
        "started_at": row[4],
        "completed_at": row[5],
    }


def reserve_tool_execution(
        sid: str,
        execution_key: str,
        tool_name: str,
        arguments: dict,
        *,
        approval_id: str = "") -> dict:
    """Reserve one tool effect or recover its write-once completed result."""
    execution_key = str(execution_key or "").strip()
    tool_name = str(tool_name or "").strip()
    if (
        not execution_key
        or len(execution_key) > 300
        or not tool_name
        or len(tool_name) > 200
        or not isinstance(arguments, dict)
    ):
        raise ValueError("tool execution identity is invalid")
    arguments_sha256 = _execution_arguments_sha256(tool_name, arguments)
    token = secrets.token_urlsafe(32)
    now = _now()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, result_json, artifact_ids_json, "
            "artifact_hashes_json, started_at, completed_at, tool_name, "
            "arguments_sha256 FROM chat_tool_executions "
            "WHERE session_id=? AND execution_key=?",
            (sid, execution_key),
        ).fetchone()
        if row:
            if row[6] != tool_name or row[7] != arguments_sha256:
                return {"state": "conflict"}
            if row[0] == "reserved":
                conn.execute(
                    "UPDATE chat_tool_executions SET status='outcome_unknown', "
                    "completed_at=? WHERE session_id=? AND execution_key=? "
                    "AND status='reserved'",
                    (now, sid, execution_key),
                )
                return {
                    "state": "outcome_unknown",
                    "started_at": row[4],
                    "completed_at": now,
                }
            return _execution_from_row(row[:6])
        session_exists = conn.execute(
            "SELECT 1 FROM chat_sessions WHERE id=?", (sid,)
        ).fetchone()
        if not session_exists:
            return {"state": "conflict"}
        conn.execute(
            "INSERT INTO chat_tool_executions "
            "(session_id, execution_key, approval_id, tool_name, "
            "arguments_sha256, status, execution_token, started_at) "
            "VALUES (?,?,?,?,?,'reserved',?,?)",
            (
                sid,
                execution_key,
                approval_id or None,
                tool_name,
                arguments_sha256,
                token,
                now,
            ),
        )
    return {"state": "reserved", "execution_token": token, "started_at": now}


def complete_tool_execution(
        sid: str,
        execution_key: str,
        execution_token: str,
        result,
        artifact_ids: list[str],
        artifact_hashes: dict[str, str]) -> bool:
    artifact_ids = list(dict.fromkeys(artifact_ids or []))
    artifact_hashes = dict(artifact_hashes or {})
    if set(artifact_ids) != set(artifact_hashes):
        raise ValueError("tool execution artifact hashes are invalid")
    result_json = json.dumps(
        result, default=str, sort_keys=True, separators=(",", ":")
    )
    if len(result_json) > 200000:
        raise ValueError("tool execution result exceeds the persistence limit")
    with _db() as conn:
        cursor = conn.execute(
            "UPDATE chat_tool_executions SET status='completed', "
            "result_json=?, artifact_ids_json=?, artifact_hashes_json=?, "
            "completed_at=? WHERE session_id=? AND execution_key=? "
            "AND status='reserved' AND execution_token=? AND result_json IS NULL",
            (
                result_json,
                json.dumps(artifact_ids),
                json.dumps(
                    artifact_hashes, sort_keys=True, separators=(",", ":")
                ),
                _now(),
                sid,
                execution_key,
                execution_token,
            ),
        )
    return cursor.rowcount == 1


def mark_tool_execution_unknown(
        sid: str, execution_key: str, execution_token: str) -> bool:
    with _db() as conn:
        cursor = conn.execute(
            "UPDATE chat_tool_executions SET status='outcome_unknown', "
            "completed_at=? WHERE session_id=? AND execution_key=? "
            "AND status='reserved' AND execution_token=?",
            (_now(), sid, execution_key, execution_token),
        )
    return cursor.rowcount == 1


def record_provider_response_checkpoint(
        sid: str, operation_key: str, response_id: str) -> bool:
    """Bind one stable provider idempotency key to exactly one response ID."""
    operation_key = str(operation_key or "").strip()
    response_id = str(response_id or "").strip()
    if (
        not operation_key
        or len(operation_key) > 300
        or not response_id
        or len(response_id) > 300
    ):
        raise ValueError("provider checkpoint identity is invalid")
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT response_id FROM chat_provider_checkpoints "
            "WHERE session_id=? AND operation_key=?",
            (sid, operation_key),
        ).fetchone()
        if row:
            return hmac_compare(row[0], response_id)
        conn.execute(
            "INSERT INTO chat_provider_checkpoints "
            "(session_id, operation_key, response_id, created_at) "
            "VALUES (?,?,?,?)",
            (sid, operation_key, response_id, _now()),
        )
    return True


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
            "WHERE session_id=? AND status IN "
            "('pending','executing_approved','executing_rejected')",
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
        session: dict,
        token: str,
        content: str,
        response_id: str,
        *,
        completed_approval_id: str = None,
        completed_approval_decision: bool = None) -> bool:
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT active_turn_token FROM chat_sessions WHERE id=?",
            (session["id"],),
        ).fetchone()
        if not row or not hmac_compare(row[0], token):
            return False
        if completed_approval_id and not _complete_approval(
            conn,
            session["id"],
            completed_approval_id,
            completed_approval_decision,
        ):
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
    from runtime_config import load_mcp_config

    vector_store_ids = cfg.get("vector_store_ids")
    if not isinstance(vector_store_ids, list):
        vector_store_ids = (
            [cfg["vector_store_id"]] if cfg.get("vector_store_id") else []
        )
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
         bool(vector_store_ids),
         "vector store "
         + (str(vector_store_ids[0])[:28] if vector_store_ids else "not configured")),
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
    for s in load_mcp_config():
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
