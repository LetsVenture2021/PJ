"""
strategyops.py — differentiated operating-system capabilities for PJ.

Implements:
  - Goal Contracts (create/update/list)
  - Trust Graph evidence bundles
  - Memory Time-Machine timeline replay
  - Autonomous weekly operating review summary
"""
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_DB_PATH = _ROOT / "pj_data.sqlite3"

_GOAL_STATUS = {"active", "at_risk", "blocked", "done", "paused", "archived"}


@contextmanager
def _db():
    conn = sqlite3.connect(_DB_PATH)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS goal_contracts (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            objective TEXT DEFAULT '',
            metric TEXT DEFAULT '',
            target_value REAL DEFAULT 0,
            current_value REAL DEFAULT 0,
            due_date TEXT DEFAULT '',
            owner TEXT DEFAULT '',
            success_criteria TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            blockers TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS goal_contract_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            note TEXT DEFAULT '',
            payload TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _new_id():
    return str(uuid.uuid4())[:8]


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _clamp_int(value, low, high, default):
    try:
        val = int(value)
    except Exception:
        val = default
    return max(low, min(high, val))


def _query_if_exists(conn, query, params=()):
    try:
        return conn.execute(query, params).fetchall()
    except sqlite3.OperationalError:
        return []


def create_goal_contract(
    title: str,
    objective: str = "",
    metric: str = "",
    target_value: float = 0,
    current_value: float = 0,
    due_date: str = "",
    owner: str = "",
    success_criteria: str = "",
) -> dict:
    """Create a goal contract PJ can track and review over time."""
    title = (title or "").strip()
    if not title:
        return {"error": "title is required"}

    goal_id = _new_id()
    now = _now()
    with _db() as conn:
        conn.execute(
            "INSERT INTO goal_contracts "
            "(id, title, objective, metric, target_value, current_value, due_date, "
            "owner, success_criteria, status, blockers, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'active','',?,?)",
            (
                goal_id,
                title[:200],
                objective[:2000],
                metric[:200],
                _safe_float(target_value, 0),
                _safe_float(current_value, 0),
                due_date[:40],
                owner[:120],
                success_criteria[:2000],
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO goal_contract_events (goal_id, event_type, note, payload) "
            "VALUES (?, 'created', ?, ?)",
            (goal_id, "Goal contract created", ""),
        )
    return {"status": "created", "goal_id": goal_id, "title": title}


def update_goal_contract(
    goal_id: str,
    status: str = "",
    current_value: float = None,
    blockers: str = "",
    note: str = "",
) -> dict:
    """Update goal progress, risk status, and blockers."""
    goal_id = (goal_id or "").strip()
    if not goal_id:
        return {"error": "goal_id is required"}
    if status and status not in _GOAL_STATUS:
        return {"error": f"status must be one of {sorted(_GOAL_STATUS)}"}

    updates = []
    params = []
    if status:
        updates.append("status=?")
        params.append(status)
    if current_value is not None:
        updates.append("current_value=?")
        params.append(_safe_float(current_value, 0))
    if blockers:
        updates.append("blockers=?")
        params.append(blockers[:2000])
    if not updates and not note:
        return {"error": "no changes provided"}

    updates.append("updated_at=?")
    params.append(_now())
    params.append(goal_id)

    with _db() as conn:
        cur = conn.execute(
            f"UPDATE goal_contracts SET {', '.join(updates)} WHERE id=?",
            params,
        )
        if not cur.rowcount:
            return {"status": "not_found", "goal_id": goal_id}
        conn.execute(
            "INSERT INTO goal_contract_events (goal_id, event_type, note, payload) "
            "VALUES (?, 'updated', ?, ?)",
            (goal_id, note[:1000], ""),
        )
        row = conn.execute(
            "SELECT id, status, current_value, blockers, updated_at "
            "FROM goal_contracts WHERE id=?",
            (goal_id,),
        ).fetchone()
    return {
        "status": "updated",
        "goal": {
            "id": row[0],
            "status": row[1],
            "current_value": row[2],
            "blockers": row[3],
            "updated_at": row[4],
        },
    }


def list_goal_contracts(status: str = "all", limit: int = 50) -> dict:
    """List goal contracts and their latest state."""
    limit = _clamp_int(limit, 1, 200, 50)
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, title, objective, metric, target_value, current_value, "
            "due_date, owner, success_criteria, status, blockers, created_at, updated_at "
            "FROM goal_contracts "
            "WHERE status = ? OR ? = 'all' "
            "ORDER BY updated_at DESC LIMIT ?",
            (status, status, limit),
        ).fetchall()
    goals = [{
        "id": r[0],
        "title": r[1],
        "objective": r[2],
        "metric": r[3],
        "target_value": r[4],
        "current_value": r[5],
        "due_date": r[6],
        "owner": r[7],
        "success_criteria": r[8],
        "status": r[9],
        "blockers": r[10],
        "created_at": r[11],
        "updated_at": r[12],
    } for r in rows]
    return {"count": len(goals), "goals": goals}


def build_evidence_bundle(topic: str = "", since_days: int = 30, limit: int = 60) -> dict:
    """Build a trust-graph evidence bundle across PJ data sources."""
    since_days = _clamp_int(since_days, 1, 365, 30)
    limit = _clamp_int(limit, 1, 200, 60)
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    like = f"%{topic.strip()}%" if topic.strip() else "%"
    items = []

    with _db() as conn:
        rows = _query_if_exists(
            conn,
            "SELECT id, title, notes, created_at FROM tasks "
            "WHERE created_at >= ? AND (title LIKE ? OR notes LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (since, like, like, limit),
        )
        for r in rows:
            items.append({
                "source": "tasks",
                "record_id": r[0],
                "timestamp": r[3],
                "summary": r[1],
                "detail": r[2],
            })

        rows = _query_if_exists(
            conn,
            "SELECT id, topic, content, created_at FROM notes "
            "WHERE created_at >= ? AND (topic LIKE ? OR content LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (since, like, like, limit),
        )
        for r in rows:
            items.append({
                "source": "notes",
                "record_id": r[0],
                "timestamp": r[3],
                "summary": r[1],
                "detail": r[2],
            })

        rows = _query_if_exists(
            conn,
            "SELECT id, decision, rationale, created_at FROM co_decisions "
            "WHERE created_at >= ? AND (decision LIKE ? OR rationale LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (since, like, like, limit),
        )
        for r in rows:
            items.append({
                "source": "decisions",
                "record_id": r[0],
                "timestamp": r[3],
                "summary": r[1],
                "detail": r[2],
            })

        rows = _query_if_exists(
            conn,
            "SELECT doc_id || ':v' || version, title, template, created_at "
            "FROM docops_documents "
            "WHERE created_at >= ? AND (title LIKE ? OR template LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (since, like, like, limit),
        )
        for r in rows:
            items.append({
                "source": "documents",
                "record_id": r[0],
                "timestamp": r[3],
                "summary": r[1],
                "detail": f"template={r[2]}",
            })

    items.sort(key=lambda i: i.get("timestamp", ""), reverse=True)
    items = items[:limit]
    return {
        "topic": topic,
        "since_days": since_days,
        "count": len(items),
        "evidence": items,
    }


def timeline_replay(query: str = "", since_days: int = 30, limit: int = 80) -> dict:
    """Replay a time-ordered memory timeline across PJ sources."""
    since_days = _clamp_int(since_days, 1, 365, 30)
    limit = _clamp_int(limit, 1, 300, 80)
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    like = f"%{query.strip()}%" if query.strip() else "%"
    events = []

    with _db() as conn:
        for r in _query_if_exists(
            conn,
            "SELECT created_at, id, title, notes FROM tasks "
            "WHERE created_at >= ? AND (title LIKE ? OR notes LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (since, like, like, limit),
        ):
            events.append({
                "timestamp": r[0],
                "source": "task",
                "record_id": r[1],
                "summary": r[2],
                "detail": r[3],
            })
        for r in _query_if_exists(
            conn,
            "SELECT created_at, id, topic, content FROM notes "
            "WHERE created_at >= ? AND (topic LIKE ? OR content LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (since, like, like, limit),
        ):
            events.append({
                "timestamp": r[0],
                "source": "note",
                "record_id": r[1],
                "summary": r[2],
                "detail": r[3],
            })
        for r in _query_if_exists(
            conn,
            "SELECT created_at, id, decision, rationale FROM co_decisions "
            "WHERE created_at >= ? AND (decision LIKE ? OR rationale LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (since, like, like, limit),
        ):
            events.append({
                "timestamp": r[0],
                "source": "decision",
                "record_id": r[1],
                "summary": r[2],
                "detail": r[3],
            })
        for r in _query_if_exists(
            conn,
            "SELECT ts, id, role, content FROM chat_messages "
            "WHERE ts >= ? AND content LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (since, like, limit),
        ):
            events.append({
                "timestamp": r[0],
                "source": "chat",
                "record_id": str(r[1]),
                "summary": r[2],
                "detail": r[3],
            })

    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    events = events[:limit]
    return {
        "query": query,
        "since_days": since_days,
        "count": len(events),
        "events": events,
    }


def weekly_operating_review(days: int = 7) -> dict:
    """Generate an autonomous weekly operating review packet."""
    days = _clamp_int(days, 1, 31, 7)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _db() as conn:
        tasks_open = _query_if_exists(
            conn, "SELECT COUNT(*) FROM tasks WHERE status='open'")
        tasks_done_recent = _query_if_exists(
            conn, "SELECT COUNT(*) FROM tasks WHERE status='done' AND created_at >= ?",
            (since,),
        )
        commitments_open = _query_if_exists(
            conn, "SELECT COUNT(*) FROM co_commitments WHERE status='open'")
        risks_open = _query_if_exists(
            conn, "SELECT COUNT(*) FROM co_risks WHERE status='open'")
        high_risks_open = _query_if_exists(
            conn,
            "SELECT COUNT(*) FROM co_risks WHERE status='open' AND severity IN ('high','critical')",
        )
        opportunities = _query_if_exists(
            conn,
            "SELECT stage, COUNT(*), ROUND(SUM(value_usd),2) FROM co_opportunities "
            "WHERE status='open' GROUP BY stage ORDER BY COUNT(*) DESC",
        )
        goal_rows = conn.execute(
            "SELECT id, title, status, target_value, current_value, due_date, blockers "
            "FROM goal_contracts ORDER BY updated_at DESC"
        ).fetchall()

    tasks_open = int(tasks_open[0][0]) if tasks_open else 0
    tasks_done_recent = int(tasks_done_recent[0][0]) if tasks_done_recent else 0
    commitments_open = int(commitments_open[0][0]) if commitments_open else 0
    risks_open = int(risks_open[0][0]) if risks_open else 0
    high_risks_open = int(high_risks_open[0][0]) if high_risks_open else 0

    goals_at_risk = []
    for g in goal_rows:
        status = g[2]
        target = _safe_float(g[3], 0)
        current = _safe_float(g[4], 0)
        due = (g[5] or "").strip()
        blocked = bool((g[6] or "").strip())
        if status in ("at_risk", "blocked") or blocked:
            goals_at_risk.append(g[1])
        elif target > 0 and current < target and due:
            goals_at_risk.append(g[1])

    pipeline = [{
        "stage": r[0],
        "count": int(r[1]),
        "value_usd": float(r[2] or 0),
    } for r in opportunities]

    suggested_actions = []
    if tasks_open > max(3, tasks_done_recent):
        suggested_actions.append("Reduce open-task carryover and close stale tasks.")
    if high_risks_open > 0:
        suggested_actions.append("Run immediate mitigation reviews for high-severity risks.")
    if commitments_open > 10:
        suggested_actions.append("Reconfirm commitment owners and near-term due dates.")
    if goals_at_risk:
        suggested_actions.append("Escalate blockers for at-risk goal contracts.")

    return {
        "period_days": days,
        "generated_at": _now(),
        "summary": {
            "tasks_open": tasks_open,
            "tasks_completed_recent": tasks_done_recent,
            "commitments_open": commitments_open,
            "risks_open": risks_open,
            "high_risks_open": high_risks_open,
            "goals_total": len(goal_rows),
            "goals_at_risk_count": len(goals_at_risk),
        },
        "pipeline": pipeline,
        "goals_at_risk": goals_at_risk[:20],
        "suggested_actions": suggested_actions,
    }


STRATEGYOPS_SCHEMAS = [
    {"type": "function", "name": "create_goal_contract",
     "description": "Create a tracked goal contract with target metric and due date.",
     "parameters": {"type": "object", "properties": {
         "title": {"type": "string"},
         "objective": {"type": "string"},
         "metric": {"type": "string"},
         "target_value": {"type": "number"},
         "current_value": {"type": "number"},
         "due_date": {"type": "string"},
         "owner": {"type": "string"},
         "success_criteria": {"type": "string"},
     }, "required": ["title"]}},
    {"type": "function", "name": "update_goal_contract",
     "description": "Update goal status, progress, blockers, and review notes.",
     "parameters": {"type": "object", "properties": {
         "goal_id": {"type": "string"},
         "status": {"type": "string",
                    "enum": ["active", "at_risk", "blocked", "done", "paused", "archived"]},
         "current_value": {"type": "number"},
         "blockers": {"type": "string"},
         "note": {"type": "string"},
     }, "required": ["goal_id"]}},
    {"type": "function", "name": "list_goal_contracts",
     "description": "List goal contracts by status (or all).",
     "parameters": {"type": "object", "properties": {
         "status": {"type": "string",
                    "enum": ["all", "active", "at_risk", "blocked", "done", "paused", "archived"]},
         "limit": {"type": "integer"},
     }, "required": []}},
    {"type": "function", "name": "build_evidence_bundle",
     "description": "Build a trust-graph evidence bundle from tasks, notes, decisions, and documents.",
     "parameters": {"type": "object", "properties": {
         "topic": {"type": "string"},
         "since_days": {"type": "integer"},
         "limit": {"type": "integer"},
     }, "required": []}},
    {"type": "function", "name": "timeline_replay",
     "description": "Replay a memory timeline across tasks, notes, decisions, and chat records.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string"},
         "since_days": {"type": "integer"},
         "limit": {"type": "integer"},
     }, "required": []}},
    {"type": "function", "name": "weekly_operating_review",
     "description": "Generate a weekly operating review packet with risks, pipeline, and goal health.",
     "parameters": {"type": "object", "properties": {
         "days": {"type": "integer"},
     }, "required": []}},
]


STRATEGYOPS_DISPATCH = {
    "create_goal_contract": create_goal_contract,
    "update_goal_contract": update_goal_contract,
    "list_goal_contracts": list_goal_contracts,
    "build_evidence_bundle": build_evidence_bundle,
    "timeline_replay": timeline_replay,
    "weekly_operating_review": weekly_operating_review,
}
