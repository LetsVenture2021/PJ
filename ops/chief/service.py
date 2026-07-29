"""
chiefops.py — PJ's executive operations toolkit (ChiefOps).

21 functions derived from PJ's operating instructions:
  Calendar & meetings (§11)....... list/create calendar events, reminders
  Communication (§10)............. draft_email (drafts only, never sends),
                                   list_recent_emails
  Relationship memory (§10/§20)... log/search contact interactions
  Projects & portfolio (§12)...... create/update projects, portfolio_review
  Commitments (§13)............... log/list commitments (who owes what, when)
  Revenue intelligence (§15)...... log/update opportunities, pipeline_review
  Decision memory (§20)........... log/search decisions with rationale
  Risk & escalation (§21)......... log_risk, risks surface in daily_brief
  Digital property (§18).......... fetch_url, scrape_metadata, check_website
  Proactive cadence (§24)......... daily_brief aggregator

Design rules: consequential external actions are never taken — emails are
drafted, not sent; calendar events require explicit user intent. All
business state lives in pj_data.sqlite3. macOS integration uses
AppleScript with timeouts; failures degrade to structured errors.
"""

import json
import re
import sqlite3
import subprocess
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DB_PATH = _ROOT / "pj_data.sqlite3"


@contextmanager
def _db():
    conn = sqlite3.connect(_DB_PATH)
    try:
        for ddl in (
            """CREATE TABLE IF NOT EXISTS co_projects (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, org TEXT DEFAULT '',
                status TEXT DEFAULT 'active', next_milestone TEXT DEFAULT '',
                milestone_due TEXT DEFAULT '', notes TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS co_commitments (
                id TEXT PRIMARY KEY, who TEXT NOT NULL, what TEXT NOT NULL,
                due TEXT DEFAULT '', direction TEXT DEFAULT 'owed_by_me',
                status TEXT DEFAULT 'open', project_id TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS co_opportunities (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, org TEXT DEFAULT '',
                stage TEXT DEFAULT 'lead', value_usd REAL DEFAULT 0,
                probability REAL DEFAULT 0.3, next_step TEXT DEFAULT '',
                notes TEXT DEFAULT '', status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS co_decisions (
                id TEXT PRIMARY KEY, decision TEXT NOT NULL,
                rationale TEXT DEFAULT '', alternatives TEXT DEFAULT '',
                context TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS co_interactions (
                id TEXT PRIMARY KEY, person TEXT NOT NULL, org TEXT DEFAULT '',
                channel TEXT DEFAULT '', summary TEXT NOT NULL,
                follow_up TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS co_risks (
                id TEXT PRIMARY KEY, risk TEXT NOT NULL,
                severity TEXT DEFAULT 'medium', mitigation TEXT DEFAULT '',
                status TEXT DEFAULT 'open', project_id TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
        ):
            conn.execute(ddl)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _id():
    return str(uuid.uuid4())[:8]


def _osascript(script: str, timeout: int = 20) -> dict:
    try:
        out = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=timeout
        )
        if out.returncode != 0:
            return {"error": out.stderr.strip()[:400] or "AppleScript failed"}
        return {"ok": True, "stdout": out.stdout.strip()}
    except subprocess.TimeoutExpired:
        return {"error": f"AppleScript timed out ({timeout}s)"}
    except Exception as exc:
        return {"error": str(exc)}


# ------------------------------------------------- calendar & meetings (§11)
def list_calendar_events(days_ahead: int = 7) -> dict:
    """List upcoming macOS Calendar events in the next N days."""
    days_ahead = max(1, min(int(days_ahead), 60))
    script = f"""
    set output to ""
    set startD to current date
    set endD to startD + ({days_ahead} * days)
    tell application "Calendar"
      repeat with cal in calendars
        set evs to (every event of cal whose start date >= startD and start date <= endD)
        repeat with ev in evs
          set output to output & (start date of ev as string) & " | " & (summary of ev) & " | " & (name of cal) & linefeed
        end repeat
      end repeat
    end tell
    return output"""
    r = _osascript(script, timeout=45)
    if "error" in r:
        return r
    events = [
        dict(zip(("start", "title", "calendar"), (p.strip() for p in line.split("|", 2))))
        for line in r["stdout"].splitlines()
        if "|" in line
    ]
    events.sort(key=lambda e: e["start"])
    return {"days_ahead": days_ahead, "count": len(events), "events": events[:50]}


def create_calendar_event(
    title: str, start_iso: str, duration_minutes: int = 60, calendar_name: str = "", notes: str = ""
) -> dict:
    """Create a macOS Calendar event. start_iso like 2026-07-28T14:00."""
    try:
        start = datetime.fromisoformat(start_iso)
    except ValueError:
        return {"error": "start_iso must be ISO format, e.g. 2026-07-28T14:00"}
    dur = max(5, min(int(duration_minutes), 720))
    ymd = (start.year, start.month, start.day, start.hour, start.minute)
    q = lambda s: s.replace("\\", "").replace('"', "'")
    cal_clause = (
        f'set targetCal to calendar "{q(calendar_name)}"'
        if calendar_name
        else "set targetCal to first calendar whose writable is true"
    )
    script = f'''
    set startD to current date
    set year of startD to {ymd[0]}
    set month of startD to {ymd[1]}
    set day of startD to {ymd[2]}
    set hours of startD to {ymd[3]}
    set minutes of startD to {ymd[4]}
    set seconds of startD to 0
    set endD to startD + ({dur} * minutes)
    tell application "Calendar"
      {cal_clause}
      make new event at end of events of targetCal with properties {{summary:"{q(title)}", start date:startD, end date:endD, description:"{q(notes)}"}}
      return name of targetCal
    end tell'''
    r = _osascript(script, timeout=30)
    if "error" in r:
        return r
    return {
        "status": "created",
        "title": title,
        "start": start.isoformat(),
        "duration_minutes": dur,
        "calendar": r["stdout"],
    }


def list_reminders(list_name: str = "") -> dict:
    """List open items from macOS Reminders (optionally one list)."""
    q = lambda s: s.replace("\\", "").replace('"', "'")
    target = f'list "{q(list_name)}"' if list_name else "default list"
    script = f"""
    set output to ""
    tell application "Reminders"
      set rs to (every reminder of {target} whose completed is false)
      repeat with r in rs
        set dueTxt to ""
        if due date of r is not missing value then set dueTxt to due date of r as string
        set output to output & (name of r) & " | " & dueTxt & linefeed
      end repeat
    end tell
    return output"""
    r = _osascript(script, timeout=30)
    if "error" in r:
        return r
    items = [
        dict(zip(("title", "due"), (p.strip() for p in line.split("|", 1))))
        for line in r["stdout"].splitlines()
        if line.strip()
    ]
    return {"list": list_name or "default", "count": len(items), "reminders": items[:50]}


def create_reminder(title: str, due_iso: str = "", list_name: str = "") -> dict:
    """Create a macOS Reminder, optionally with a due datetime."""
    q = lambda s: s.replace("\\", "").replace('"', "'")
    target = f'list "{q(list_name)}"' if list_name else "default list"
    due_clause = ""
    if due_iso:
        try:
            d = datetime.fromisoformat(due_iso)
        except ValueError:
            return {"error": "due_iso must be ISO format"}
        due_clause = (
            f"set dueD to current date\n"
            f"set year of dueD to {d.year}\nset month of dueD to {d.month}\n"
            f"set day of dueD to {d.day}\nset hours of dueD to {d.hour}\n"
            f"set minutes of dueD to {d.minute}\nset seconds of dueD to 0\n"
        )
    props = f'{{name:"{q(title)}"' + (", due date:dueD}" if due_iso else "}")
    script = f'''{due_clause}tell application "Reminders"
      make new reminder at end of reminders of {target} with properties {props}
    end tell
    return "ok"'''
    r = _osascript(script, timeout=30)
    if "error" in r:
        return r
    return {
        "status": "created",
        "title": title,
        "due": due_iso or None,
        "list": list_name or "default",
    }


# ----------------------------------------------------- communication (§10)
def draft_email(to: str, subject: str, body: str) -> dict:
    """Compose a draft in Apple Mail for the user to review. NEVER sends."""
    q = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "Mail"
      set msg to make new outgoing message with properties {{subject:"{q(subject)}", content:"{q(body)}", visible:true}}
      tell msg to make new to recipient at end of to recipients with properties {{address:"{q(to)}"}}
      activate
    end tell
    return "ok"'''
    r = _osascript(script, timeout=30)
    if "error" in r:
        return r
    return {
        "status": "draft_open_in_mail",
        "to": to,
        "subject": subject,
        "note": "Draft only — the user reviews and sends it manually.",
    }


def list_recent_emails(count: int = 10) -> dict:
    """List the newest messages in the Apple Mail inbox (sender, subject, date)."""
    count = max(1, min(int(count), 25))
    script = f"""
    set output to ""
    tell application "Mail"
      set msgs to messages 1 thru (my min({count}, count of messages of inbox)) of inbox
      repeat with m in msgs
        set output to output & (date received of m as string) & " | " & (sender of m) & " | " & (subject of m) & linefeed
      end repeat
    end tell
    return output
    on min(a, b)
      if a < b then return a
      return b
    end min"""
    r = _osascript(script, timeout=45)
    if "error" in r:
        return r
    msgs = [
        dict(zip(("received", "sender", "subject"), (p.strip() for p in line.split("|", 2))))
        for line in r["stdout"].splitlines()
        if "|" in line
    ]
    return {"count": len(msgs), "messages": msgs}


# --------------------------------------- relationship memory (§10 / §20)
def log_contact_interaction(
    person: str, summary: str, org: str = "", channel: str = "", follow_up: str = ""
) -> dict:
    """Record an interaction with a person (call, email, meeting) for
    relationship continuity."""
    iid = _id()
    with _db() as conn:
        conn.execute(
            "INSERT INTO co_interactions (id, person, org, channel, summary,"
            " follow_up) VALUES (?,?,?,?,?,?)",
            (iid, person, org, channel, summary, follow_up),
        )
    return {"status": "logged", "interaction_id": iid, "person": person}


def search_contact_history(person: str) -> dict:
    """Retrieve interaction history for a person or organization."""
    like = f"%{person}%"
    with _db() as conn:
        rows = conn.execute(
            "SELECT person, org, channel, summary, follow_up, created_at "
            "FROM co_interactions WHERE person LIKE ? OR org LIKE ? "
            "ORDER BY created_at DESC LIMIT 25",
            (like, like),
        ).fetchall()
    return {
        "count": len(rows),
        "interactions": [
            dict(zip(("person", "org", "channel", "summary", "follow_up", "created_at"), r))
            for r in rows
        ],
    }


# --------------------------------------------- projects & portfolio (§12)
def create_project(
    name: str, org: str = "", next_milestone: str = "", milestone_due: str = "", notes: str = ""
) -> dict:
    """Register a project/initiative in the portfolio."""
    pid = _id()
    with _db() as conn:
        conn.execute(
            "INSERT INTO co_projects (id, name, org, next_milestone, "
            "milestone_due, notes) VALUES (?,?,?,?,?,?)",
            (pid, name, org, next_milestone, milestone_due, notes),
        )
    return {"status": "created", "project_id": pid, "name": name}


def update_project(
    project_id: str,
    status: str = "",
    next_milestone: str = "",
    milestone_due: str = "",
    notes: str = "",
) -> dict:
    """Update a project's status, milestone, or notes."""
    sets, vals = [], []
    for col, val in (
        ("status", status),
        ("next_milestone", next_milestone),
        ("milestone_due", milestone_due),
        ("notes", notes),
    ):
        if val:
            sets.append(f"{col}=?")
            vals.append(val)
    if not sets:
        return {"error": "nothing to update"}
    sets.append("updated_at=?")
    vals += [datetime.now(timezone.utc).isoformat(), project_id]
    with _db() as conn:
        cur = conn.execute(f"UPDATE co_projects SET {', '.join(sets)} WHERE id=?", vals)
    return {"project_id": project_id, "status": "updated" if cur.rowcount else "not_found"}


def portfolio_review() -> dict:
    """Review all projects: status, milestones, stale/overdue flags."""
    now = datetime.now(timezone.utc)
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, org, status, next_milestone, milestone_due, "
            "notes, updated_at FROM co_projects "
            "WHERE status != 'archived' ORDER BY org, name"
        ).fetchall()
    projects, flags = [], []
    for r in rows:
        p = dict(
            zip(
                (
                    "id",
                    "name",
                    "org",
                    "status",
                    "next_milestone",
                    "milestone_due",
                    "notes",
                    "updated_at",
                ),
                r,
            )
        )
        try:
            upd = datetime.fromisoformat(p["updated_at"])
            upd = upd if upd.tzinfo else upd.replace(tzinfo=timezone.utc)
            p["days_since_update"] = (now - upd).days
            if p["status"] == "active" and p["days_since_update"] > 14:
                flags.append(f"{p['name']}: no update in {p['days_since_update']}d")
        except ValueError:
            pass
        if p["milestone_due"]:
            try:
                if datetime.fromisoformat(p["milestone_due"]).replace(tzinfo=timezone.utc) < now:
                    flags.append(f"{p['name']}: milestone '{p['next_milestone']}' overdue")
            except ValueError:
                pass
        projects.append(p)
    return {"count": len(projects), "attention_flags": flags, "projects": projects}


# --------------------------------------------------- commitments (§13)
def log_commitment(
    who: str, what: str, due: str = "", direction: str = "owed_by_me", project_id: str = ""
) -> dict:
    """Track a commitment: something the user owes (owed_by_me) or is
    owed (owed_to_me), with an optional due date."""
    if direction not in ("owed_by_me", "owed_to_me"):
        return {"error": "direction must be owed_by_me or owed_to_me"}
    cid = _id()
    with _db() as conn:
        conn.execute(
            "INSERT INTO co_commitments (id, who, what, due, direction, "
            "project_id) VALUES (?,?,?,?,?,?)",
            (cid, who, what, due, direction, project_id),
        )
    return {"status": "logged", "commitment_id": cid}


def list_commitments(direction: str = "all") -> dict:
    """List open commitments with overdue flags, optionally by direction."""
    now = datetime.now(timezone.utc)
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, who, what, due, direction, project_id, created_at "
            "FROM co_commitments WHERE status='open' AND "
            "(direction = ? OR ? = 'all') ORDER BY due",
            (direction, direction),
        ).fetchall()
    items = []
    for r in rows:
        c = dict(zip(("id", "who", "what", "due", "direction", "project_id", "created_at"), r))
        if c["due"]:
            try:
                c["overdue"] = datetime.fromisoformat(c["due"]).replace(tzinfo=timezone.utc) < now
            except ValueError:
                c["overdue"] = False
        items.append(c)
    return {
        "count": len(items),
        "overdue": sum(1 for c in items if c.get("overdue")),
        "commitments": items,
    }


def complete_commitment(commitment_id: str) -> dict:
    """Close a commitment as fulfilled."""
    with _db() as conn:
        cur = conn.execute("UPDATE co_commitments SET status='done' WHERE id=?", (commitment_id,))
    return {"commitment_id": commitment_id, "status": "done" if cur.rowcount else "not_found"}


# ------------------------------------------- revenue intelligence (§15)
def log_opportunity(
    name: str,
    org: str = "",
    stage: str = "lead",
    value_usd: float = 0,
    probability: float = 0.3,
    next_step: str = "",
    notes: str = "",
) -> dict:
    """Add a revenue opportunity to the pipeline."""
    oid = _id()
    with _db() as conn:
        conn.execute(
            "INSERT INTO co_opportunities (id, name, org, stage, value_usd, "
            "probability, next_step, notes) VALUES (?,?,?,?,?,?,?,?)",
            (
                oid,
                name,
                org,
                stage,
                float(value_usd),
                min(max(float(probability), 0), 1),
                next_step,
                notes,
            ),
        )
    return {"status": "logged", "opportunity_id": oid, "name": name}


def update_opportunity(
    opportunity_id: str,
    stage: str = "",
    value_usd: float = -1,
    probability: float = -1,
    next_step: str = "",
    status: str = "",
    notes: str = "",
) -> dict:
    """Update an opportunity's stage, value, probability, next step, or
    status (open/won/lost)."""
    sets, vals = [], []
    if stage:
        sets.append("stage=?")
        vals.append(stage)
    if value_usd >= 0:
        sets.append("value_usd=?")
        vals.append(float(value_usd))
    if probability >= 0:
        sets.append("probability=?")
        vals.append(min(max(float(probability), 0), 1))
    if next_step:
        sets.append("next_step=?")
        vals.append(next_step)
    if status:
        if status not in ("open", "won", "lost"):
            return {"error": "status must be open, won, or lost"}
        sets.append("status=?")
        vals.append(status)
    if notes:
        sets.append("notes=?")
        vals.append(notes)
    if not sets:
        return {"error": "nothing to update"}
    sets.append("updated_at=?")
    vals += [datetime.now(timezone.utc).isoformat(), opportunity_id]
    with _db() as conn:
        cur = conn.execute(f"UPDATE co_opportunities SET {', '.join(sets)} WHERE id=?", vals)
    return {"opportunity_id": opportunity_id, "status": "updated" if cur.rowcount else "not_found"}


def pipeline_review() -> dict:
    """Summarize the revenue pipeline: totals, weighted value, by stage,
    and stalled deals."""
    now = datetime.now(timezone.utc)
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, org, stage, value_usd, probability, next_step, "
            "updated_at FROM co_opportunities WHERE status='open' "
            "ORDER BY value_usd DESC"
        ).fetchall()
    opps, stages, stalled = [], {}, []
    total = weighted = 0.0
    for r in rows:
        o = dict(
            zip(
                (
                    "id",
                    "name",
                    "org",
                    "stage",
                    "value_usd",
                    "probability",
                    "next_step",
                    "updated_at",
                ),
                r,
            )
        )
        total += o["value_usd"]
        weighted += o["value_usd"] * o["probability"]
        s = stages.setdefault(o["stage"], {"count": 0, "value_usd": 0.0})
        s["count"] += 1
        s["value_usd"] += o["value_usd"]
        try:
            upd = datetime.fromisoformat(o["updated_at"])
            upd = upd if upd.tzinfo else upd.replace(tzinfo=timezone.utc)
            if (now - upd).days > 14:
                stalled.append(f"{o['name']} ({o['stage']}, {(now - upd).days}d idle)")
        except ValueError:
            pass
        opps.append(o)
    return {
        "open_count": len(opps),
        "total_value_usd": round(total, 2),
        "weighted_value_usd": round(weighted, 2),
        "by_stage": stages,
        "stalled": stalled,
        "opportunities": opps[:25],
    }


# ------------------------------------------------ decision memory (§20)
def log_decision(
    decision: str, rationale: str = "", alternatives: str = "", context: str = ""
) -> dict:
    """Record a decision with its rationale and rejected alternatives so
    future work honors it."""
    did = _id()
    with _db() as conn:
        conn.execute(
            "INSERT INTO co_decisions (id, decision, rationale, "
            "alternatives, context) VALUES (?,?,?,?,?)",
            (did, decision, rationale, alternatives, context),
        )
    return {"status": "logged", "decision_id": did}


def search_decisions(query: str) -> dict:
    """Search the decision journal by keyword."""
    like = f"%{query}%"
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, decision, rationale, alternatives, context, "
            "created_at FROM co_decisions WHERE decision LIKE ? OR "
            "rationale LIKE ? OR context LIKE ? "
            "ORDER BY created_at DESC LIMIT 20",
            (like, like, like),
        ).fetchall()
    return {
        "count": len(rows),
        "decisions": [
            dict(zip(("id", "decision", "rationale", "alternatives", "context", "created_at"), r))
            for r in rows
        ],
    }


# ------------------------------------------------ risk register (§21)
def log_risk(
    risk: str, severity: str = "medium", mitigation: str = "", project_id: str = ""
) -> dict:
    """Register a business/operational risk with severity and mitigation."""
    if severity not in ("low", "medium", "high", "critical"):
        return {"error": "severity must be low, medium, high, or critical"}
    rid = _id()
    with _db() as conn:
        conn.execute(
            "INSERT INTO co_risks (id, risk, severity, mitigation, project_id) VALUES (?,?,?,?,?)",
            (rid, risk, severity, mitigation, project_id),
        )
    return {"status": "logged", "risk_id": rid, "severity": severity}


# ------------------------------------------- digital property (§18)
def _sanitized_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port:
        host = f"{host}:{port}"
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))


def _sanitized_web_error(exc) -> str:
    message = " ".join(str(exc).split())

    def replace(match):
        return _sanitized_url(match.group(0).rstrip(".,);")) or "[redacted URL]"

    return re.sub(r"https?://[^\s]+", replace, message)[:300]


def _is_access_interstitial(final_url: str, body: str = "") -> bool:
    try:
        parsed = urllib.parse.urlsplit(final_url)
    except ValueError:
        return False
    path = parsed.path.casefold()
    host = (parsed.hostname or "").casefold()
    body_prefix = body[:20000].casefold()
    return (
        (host.endswith(".cloudflareaccess.com") and "/cdn-cgi/access/" in path)
        or "/cdn-cgi/challenge-platform/" in path
        or (
            "cloudflare access" in body_prefix
            and ("send login code" in body_prefix or "sign in" in body_prefix)
        )
    )


def fetch_url(url: str, max_chars: int = 4000) -> dict:
    """Fetch a web page and return its visible text (tags stripped)."""
    safe_url = _sanitized_url(url)
    if not safe_url:
        return {"error": "url must start with http(s)://"}
    import re as _re

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PJ/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read(1_500_000).decode("utf-8", "replace")
            status = resp.status
            final_url = resp.geturl()
    except Exception as exc:
        return {"error": _sanitized_web_error(exc), "url": safe_url}
    text = _re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=_re.S | _re.I)
    text = _re.sub(r"<[^>]+>", " ", text)
    text = _re.sub(r"\s+", " ", text).strip()
    access_required = _is_access_interstitial(final_url, raw)
    return {
        "url": safe_url,
        "final_url": _sanitized_url(final_url),
        "http_status": status,
        "transport_reachable": True,
        "access_login_required": access_required,
        "application_content_verified": not access_required,
        "text": text[: max(500, min(int(max_chars), 12000))],
    }


class _MetadataParser(HTMLParser):
    """Collect bounded, declarative metadata without executing page content."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.in_title = False
        self.meta = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = {str(key).casefold(): value or "" for key, value in attrs}
        lowered_tag = tag.casefold()
        if lowered_tag == "title":
            self.in_title = True
        elif lowered_tag == "meta" and len(self.meta) < 200:
            key = attributes.get("property") or attributes.get("name")
            content = attributes.get("content")
            if key and content:
                self.meta.append((key.strip().casefold(), content.strip()[:4000]))
        elif lowered_tag == "link" and len(self.links) < 100:
            rel = {part.casefold() for part in attributes.get("rel", "").split()}
            href = attributes.get("href", "").strip()
            if rel and href:
                self.links.append((rel, href[:4000]))

    def handle_endtag(self, tag):
        if tag.casefold() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title and sum(map(len, self.title_parts)) < 4000:
            self.title_parts.append(data)


def scrape_metadata(url: str) -> dict:
    """Fetch a page and extract standard discovery and social metadata."""
    safe_url = _sanitized_url(url)
    if not safe_url:
        return {"error": "url must start with http(s)://"}
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "PJ/1.0", "Accept": "text/html,application/xhtml+xml"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            content_type = resp.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return {
                    "error": "response is not HTML",
                    "url": safe_url,
                    "content_type": content_type,
                }
            charset = resp.headers.get_content_charset() or "utf-8"
            raw = resp.read(1_500_001)
            truncated = len(raw) > 1_500_000
            html = raw[:1_500_000].decode(charset, "replace")
            status = int(resp.status)
            final_url = resp.geturl()
    except (LookupError, UnicodeError) as exc:
        return {"error": _sanitized_web_error(exc), "url": safe_url}
    except Exception as exc:
        return {"error": _sanitized_web_error(exc), "url": safe_url}

    parser = _MetadataParser()
    parser.feed(html)
    values = {}
    for key, value in parser.meta:
        values.setdefault(key, value)

    def absolute_link(relations):
        for rel, href in parser.links:
            if rel.intersection(relations):
                return _sanitized_url(urllib.parse.urljoin(final_url, href))
        return None

    open_graph = {key[3:]: value for key, value in values.items() if key.startswith("og:")}
    twitter = {key[8:]: value for key, value in values.items() if key.startswith("twitter:")}
    for metadata, keys in (
        (open_graph, {"audio", "image", "image:url", "url", "video"}),
        (twitter, {"image", "image:src", "player"}),
    ):
        for key in metadata.keys() & keys:
            metadata[key] = _sanitized_url(urllib.parse.urljoin(final_url, metadata[key]))
    title = " ".join("".join(parser.title_parts).split())[:4000]
    return {
        "url": safe_url,
        "final_url": _sanitized_url(final_url),
        "http_status": status,
        "content_type": content_type,
        "truncated": truncated,
        "access_login_required": _is_access_interstitial(final_url, html),
        "title": title or open_graph.get("title") or twitter.get("title"),
        "description": values.get("description") or open_graph.get("description"),
        "canonical_url": absolute_link({"canonical"}),
        "icon_url": absolute_link({"icon", "shortcut"}),
        "open_graph": open_graph,
        "twitter": twitter,
    }


def check_website(url: str) -> dict:
    """Separate transport reachability, Access gating, and application health."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    safe_url = _sanitized_url(url)
    if not safe_url:
        return {
            "url": "",
            "up": False,
            "transport_reachable": False,
            "application_healthy": False,
            "status": "invalid_url",
            "error": "url must be a valid HTTP(S) URL",
        }
    import time as _t

    start = _t.monotonic()
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "PJ/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            latency = int((_t.monotonic() - start) * 1000)
            raw = resp.read(100_000).decode("utf-8", "replace")
            final_url = resp.geturl()
            access_required = _is_access_interstitial(final_url, raw)
            application_healthy = 200 <= int(resp.status) < 400 and not access_required
            return {
                "url": safe_url,
                "up": application_healthy,
                "transport_reachable": True,
                "application_healthy": application_healthy,
                "access_login_required": access_required,
                "status": (
                    "access_login_required"
                    if access_required
                    else ("healthy" if application_healthy else "http_error")
                ),
                "http_status": resp.status,
                "latency_ms": latency,
                "final_url": _sanitized_url(final_url),
                "redirected": safe_url != _sanitized_url(final_url),
            }
    except Exception as exc:
        status = getattr(exc, "code", None)
        transport_reachable = isinstance(status, int)
        final_url = _sanitized_url(getattr(exc, "url", "") or url)
        return {
            "url": safe_url,
            "up": False,
            "transport_reachable": transport_reachable,
            "application_healthy": False,
            "access_login_required": _is_access_interstitial(final_url),
            "status": "http_error" if transport_reachable else "transport_error",
            "http_status": status,
            "final_url": final_url,
            "error": _sanitized_web_error(exc),
            "latency_ms": int((_t.monotonic() - start) * 1000),
        }


# ------------------------------------------- proactive cadence (§24)
def daily_brief() -> dict:
    """Aggregate the executive daily brief: open tasks, commitments
    (overdue first), pipeline summary, portfolio flags, open risks, and
    today's calendar."""
    import skills as _skills

    tasks = _skills.list_tasks("open")
    commitments = list_commitments("all")
    pipeline = pipeline_review()
    portfolio = portfolio_review()
    with _db() as conn:
        risks = conn.execute(
            "SELECT risk, severity, mitigation FROM co_risks "
            "WHERE status='open' ORDER BY CASE severity "
            "WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 ELSE 3 END LIMIT 10"
        ).fetchall()
    calendar = list_calendar_events(1)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "open_tasks": tasks.get("count", 0),
        "top_tasks": tasks.get("tasks", [])[:5],
        "commitments_open": commitments["count"],
        "commitments_overdue": commitments["overdue"],
        "overdue_commitments": [c for c in commitments["commitments"] if c.get("overdue")][:5],
        "pipeline": {
            k: pipeline[k]
            for k in ("open_count", "total_value_usd", "weighted_value_usd", "stalled")
        },
        "portfolio_flags": portfolio["attention_flags"],
        "open_risks": [{"risk": r[0], "severity": r[1], "mitigation": r[2]} for r in risks],
        "today_calendar": (
            calendar.get("events", [])
            if "error" not in calendar
            else f"calendar unavailable: {calendar['error']}"
        ),
    }


# --------------------------------------------------------- tool schemas
def _fn(name, desc, props=None, required=None):
    return {
        "type": "function",
        "name": name,
        "description": desc,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []},
    }


_S = {"type": "string"}
_N = {"type": "number"}
_I = {"type": "integer"}

CHIEFOPS_SCHEMAS = [
    _fn(
        "list_calendar_events",
        "List upcoming macOS Calendar events within the next N days.",
        {"days_ahead": {**_I, "description": "1-60, default 7"}},
    ),
    _fn(
        "create_calendar_event",
        "Create a macOS Calendar event. Confirm details with the user first.",
        {
            "title": _S,
            "start_iso": {**_S, "description": "e.g. 2026-07-28T14:00 (local)"},
            "duration_minutes": _I,
            "calendar_name": {**_S, "description": "Optional calendar name"},
            "notes": _S,
        },
        ["title", "start_iso"],
    ),
    _fn(
        "list_reminders",
        "List open macOS Reminders (default list, or a named list).",
        {"list_name": _S},
    ),
    _fn(
        "create_reminder",
        "Create a macOS Reminder with optional due datetime.",
        {"title": _S, "due_iso": {**_S, "description": "e.g. 2026-07-28T09:00"}, "list_name": _S},
        ["title"],
    ),
    _fn(
        "draft_email",
        "Compose an email DRAFT in Apple Mail for the user to review and "
        "send manually. Never sends automatically.",
        {"to": _S, "subject": _S, "body": _S},
        ["to", "subject", "body"],
    ),
    _fn(
        "list_recent_emails",
        "List the newest messages in the Apple Mail inbox (sender, subject, date).",
        {"count": {**_I, "description": "1-25, default 10"}},
    ),
    _fn(
        "log_contact_interaction",
        "Record an interaction with a person (call, meeting, email) for relationship continuity.",
        {"person": _S, "summary": _S, "org": _S, "channel": _S, "follow_up": _S},
        ["person", "summary"],
    ),
    _fn(
        "search_contact_history",
        "Retrieve logged interaction history for a person or organization.",
        {"person": _S},
        ["person"],
    ),
    _fn(
        "create_project",
        "Register a project or initiative in the portfolio tracker.",
        {
            "name": _S,
            "org": {**_S, "description": "e.g. Aimhi Deal Desk"},
            "next_milestone": _S,
            "milestone_due": {**_S, "description": "ISO date"},
            "notes": _S,
        },
        ["name"],
    ),
    _fn(
        "update_project",
        "Update a project's status (active/paused/done/archived), milestone, or notes.",
        {"project_id": _S, "status": _S, "next_milestone": _S, "milestone_due": _S, "notes": _S},
        ["project_id"],
    ),
    _fn("portfolio_review", "Review all projects with stale-update and overdue-milestone flags."),
    _fn(
        "log_commitment",
        "Track a commitment: something the user owes (owed_by_me) or is owed (owed_to_me).",
        {
            "who": {**_S, "description": "Counterparty"},
            "what": _S,
            "due": {**_S, "description": "ISO date"},
            "direction": {**_S, "enum": ["owed_by_me", "owed_to_me"]},
            "project_id": _S,
        },
        ["who", "what"],
    ),
    _fn(
        "list_commitments",
        "List open commitments with overdue flags.",
        {"direction": {**_S, "enum": ["owed_by_me", "owed_to_me", "all"]}},
    ),
    _fn(
        "complete_commitment",
        "Close a commitment as fulfilled.",
        {"commitment_id": _S},
        ["commitment_id"],
    ),
    _fn(
        "log_opportunity",
        "Add a revenue opportunity to the pipeline.",
        {
            "name": _S,
            "org": _S,
            "stage": {**_S, "enum": ["lead", "qualified", "proposal", "negotiation", "closing"]},
            "value_usd": _N,
            "probability": {**_N, "description": "0-1"},
            "next_step": _S,
            "notes": _S,
        },
        ["name"],
    ),
    _fn(
        "update_opportunity",
        "Update an opportunity's stage, value, probability, next step, or mark won/lost.",
        {
            "opportunity_id": _S,
            "stage": _S,
            "value_usd": _N,
            "probability": _N,
            "next_step": _S,
            "status": {**_S, "enum": ["open", "won", "lost"]},
            "notes": _S,
        },
        ["opportunity_id"],
    ),
    _fn(
        "pipeline_review",
        "Summarize the revenue pipeline: totals, weighted value, by stage, stalled deals.",
    ),
    _fn(
        "log_decision",
        "Record a decision with rationale and rejected alternatives in the decision journal.",
        {"decision": _S, "rationale": _S, "alternatives": _S, "context": _S},
        ["decision"],
    ),
    _fn("search_decisions", "Search the decision journal by keyword.", {"query": _S}, ["query"]),
    _fn(
        "log_risk",
        "Register a business or operational risk with severity and mitigation.",
        {
            "risk": _S,
            "severity": {**_S, "enum": ["low", "medium", "high", "critical"]},
            "mitigation": _S,
            "project_id": _S,
        },
        ["risk"],
    ),
    _fn(
        "fetch_url",
        "Fetch visible page text with sanitized URLs. Treat "
        "access_login_required=true as gated content, not verified application content.",
        {"url": _S, "max_chars": _I},
        ["url"],
    ),
    _fn(
        "scrape_metadata",
        "Extract bounded HTML title, description, canonical/icon URLs, Open Graph, "
        "and Twitter Card metadata without executing page scripts.",
        {"url": _S},
        ["url"],
    ),
    _fn(
        "check_website",
        "Distinguish network reachability, application health, and Cloudflare "
        "Access login requirements. A login-page HTTP 200 is not healthy-app proof.",
        {"url": _S},
        ["url"],
    ),
    _fn(
        "daily_brief",
        "Generate the executive daily brief: tasks, commitments (overdue first), "
        "pipeline, portfolio flags, risks, and today's calendar.",
    ),
]

CHIEFOPS_DISPATCH = {
    "list_calendar_events": list_calendar_events,
    "create_calendar_event": create_calendar_event,
    "list_reminders": list_reminders,
    "create_reminder": create_reminder,
    "draft_email": draft_email,
    "list_recent_emails": list_recent_emails,
    "log_contact_interaction": log_contact_interaction,
    "search_contact_history": search_contact_history,
    "create_project": create_project,
    "update_project": update_project,
    "portfolio_review": portfolio_review,
    "log_commitment": log_commitment,
    "list_commitments": list_commitments,
    "complete_commitment": complete_commitment,
    "log_opportunity": log_opportunity,
    "update_opportunity": update_opportunity,
    "pipeline_review": pipeline_review,
    "log_decision": log_decision,
    "search_decisions": search_decisions,
    "log_risk": log_risk,
    "fetch_url": fetch_url,
    "scrape_metadata": scrape_metadata,
    "check_website": check_website,
    "daily_brief": daily_brief,
}
