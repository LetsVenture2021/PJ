"""
skillops.py — PJ's self-improvement engine (SkillOps).

PJ is a standalone Chief of Staff. This module lets PJ:
  1. OBSERVE   — record recurring tasks/patterns/friction it notices.
  2. REASON    — surface observations + telemetry so PJ can decide which
                 new skill would deliver the most leverage.
  3. CREATE    — install new skills PJ writes itself (validated, sandbox
                 smoke-tested, versioned, registered as 'candidate').
  4. MASTER    — every dispatch is instrumented; telemetry feeds back in.
  5. GOVERN    — deterministic lifecycle review recommends promote /
                 optimize / pause / deprecate / retire; humans (or PJ,
                 explicitly) activate and deprecate. Nothing is silently
                 enabled: created skills start as candidates.

Generated skills live in ~/PJ/generated_skills/<name>.py. Each module
must define:
    SCHEMA  — OpenAI function-tool schema (dict with name/description/parameters)
    run(**kwargs) -> dict
Active skills are loaded dynamically by skills.py at startup.
"""
import ast
import importlib.util
import json
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_DB_PATH = _ROOT / "pj_data.sqlite3"
GENERATED_DIR = _ROOT / "generated_skills"
GENERATED_DIR.mkdir(exist_ok=True)

# Names that can never be overridden by generated skills.
RESERVED_NAMES = {
    "get_current_time", "add_task", "list_tasks", "complete_task",
    "save_note", "search_notes", "get_active_browser_tab", "run_shortcut",
    "observe_pattern", "list_observations", "create_skill", "activate_skill",
    "review_skills", "deprecate_skill",
    "list_doc_templates", "create_doc_template", "draft_document",
    "revise_document", "finalize_document", "export_document",
    "list_documents", "get_document",
    "list_calendar_events", "create_calendar_event", "list_reminders",
    "create_reminder", "draft_email", "list_recent_emails",
    "log_contact_interaction", "search_contact_history", "create_project",
    "update_project", "portfolio_review", "log_commitment",
    "list_commitments", "complete_commitment", "log_opportunity",
    "update_opportunity", "pipeline_review", "log_decision",
    "search_decisions", "log_risk", "fetch_url", "check_website",
    "daily_brief",
}

# Lifecycle policy (governed thresholds, tunable in one place).
POLICY = {
    "window_days": 30,          # telemetry window for review
    "unused_age_days": 21,      # candidate/active unused this long -> flag
    "stale_age_days": 90,       # active + never updated -> tech-refresh review
    "min_calls_for_stats": 5,   # don't judge failure rate on tiny samples
    "pause_failure_rate": 0.5,  # >50% failures -> pause_and_revalidate
    "optimize_failure_rate": 0.2,
    "slow_p90_ms": 10_000,
}


def _db():
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS skillops_observations (
        id TEXT PRIMARY KEY,
        pattern TEXT NOT NULL,
        context TEXT DEFAULT '',
        frequency_hint TEXT DEFAULT '',
        status TEXT DEFAULT 'open',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS skillops_registry (
        name TEXT PRIMARY KEY,
        version INTEGER DEFAULT 1,
        status TEXT DEFAULT 'candidate',
        description TEXT DEFAULT '',
        origin TEXT DEFAULT 'pj_generated',
        path TEXT DEFAULT '',
        review_note TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS skillops_telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill TEXT NOT NULL,
        ok INTEGER NOT NULL,
        latency_ms INTEGER NOT NULL,
        error TEXT,
        ts TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    return conn


# ---------------------------------------------------------------- telemetry
def record_invocation(skill: str, ok: bool, latency_ms: int, error: str = None):
    """Called by skills.dispatch() for every tool invocation."""
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO skillops_telemetry (skill, ok, latency_ms, error) "
                "VALUES (?,?,?,?)",
                (skill, 1 if ok else 0, latency_ms,
                 (error or "")[:500] or None))
    except Exception:
        pass  # telemetry must never break a skill call


# ------------------------------------------------------------- observations
def observe_pattern(pattern: str, context: str = "",
                    frequency_hint: str = "") -> dict:
    """Record a recurring task/pattern/friction point worth automating."""
    obs_id = str(uuid.uuid4())[:8]
    with _db() as conn:
        conn.execute(
            "INSERT INTO skillops_observations "
            "(id, pattern, context, frequency_hint) VALUES (?,?,?,?)",
            (obs_id, pattern, context, frequency_hint))
    return {"status": "recorded", "observation_id": obs_id}


def list_observations(status: str = "open") -> dict:
    """List recorded patterns, optionally filtered by status (open/addressed/all)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, pattern, context, frequency_hint, status, created_at "
            "FROM skillops_observations WHERE status = ? OR ? = 'all' "
            "ORDER BY created_at DESC LIMIT 50", (status, status)).fetchall()
    return {"count": len(rows), "observations": [
        {"id": r[0], "pattern": r[1], "context": r[2],
         "frequency_hint": r[3], "status": r[4], "created_at": r[5]}
        for r in rows]}


# ---------------------------------------------------------- skill creation
def _validate_code(name: str, code: str) -> list:
    """Static checks. Returns a list of error strings (empty = ok)."""
    errors = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]
    top = {n.name for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if "run" not in top:
        errors.append("code must define a run(**kwargs) function")
    if not name.isidentifier() or name != name.lower():
        errors.append("name must be a lowercase python identifier")
    if name in RESERVED_NAMES:
        errors.append(f"'{name}' is a reserved built-in skill name")
    return errors


def _smoke_test(path: Path, test_args: dict) -> dict:
    """Import + execute the skill in a subprocess with a timeout."""
    prog = (
        "import json,sys,importlib.util\n"
        f"spec=importlib.util.spec_from_file_location('smoke', {str(path)!r})\n"
        "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        f"out=m.run(**json.loads({json.dumps(json.dumps(test_args))}))\n"
        "print(json.dumps({'ok':True,'result':str(out)[:500]}))\n")
    try:
        out = subprocess.run([sys.executable, "-c", prog],
                             capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return {"ok": False, "error": out.stderr.strip()[:800]}
        return json.loads(out.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "smoke test timed out (30s)"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def create_skill(name: str, description: str, code: str,
                 parameters_json: str = "{}", test_args_json: str = "",
                 addresses_observation: str = "") -> dict:
    """Install a new (or upgraded) PJ-authored skill.

    code must define run(**kwargs) -> dict. parameters_json is the
    JSON-schema 'parameters' object for the tool. The skill is validated,
    smoke-tested if test_args_json is provided, written to
    generated_skills/<name>.py and registered as status='candidate'
    (or version-bumped if it already exists). It only becomes callable
    after activate_skill(name).
    """
    errors = _validate_code(name, code)
    try:
        parameters = json.loads(parameters_json or "{}")
    except Exception as exc:
        errors.append(f"parameters_json is not valid JSON: {exc}")
    if errors:
        return {"status": "rejected", "errors": errors}

    schema = {"type": "function", "name": name,
              "description": description[:1024],
              "parameters": parameters or
              {"type": "object", "properties": {}, "required": []}}
    module_src = (
        f'"""PJ-generated skill: {name} (SkillOps)."""\n'
        f"SCHEMA = {json.dumps(schema, indent=2)}\n\n{code}\n")
    path = GENERATED_DIR / f"{name}.py"
    path.write_text(module_src)

    smoke = {"skipped": True}
    if test_args_json:
        try:
            smoke = _smoke_test(path, json.loads(test_args_json))
        except Exception as exc:
            smoke = {"ok": False, "error": f"bad test_args_json: {exc}"}
        if not smoke.get("ok"):
            path.unlink(missing_ok=True)
            return {"status": "rejected", "errors": ["smoke test failed"],
                    "smoke_test": smoke}

    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        row = conn.execute("SELECT version FROM skillops_registry WHERE name=?",
                           (name,)).fetchone()
        if row:
            conn.execute(
                "UPDATE skillops_registry SET version=version+1, "
                "description=?, path=?, updated_at=?, status='candidate', "
                "review_note='upgraded, pending re-activation' WHERE name=?",
                (description, str(path), now, name))
            version = row[0] + 1
        else:
            conn.execute(
                "INSERT INTO skillops_registry "
                "(name, description, path, created_at, updated_at) "
                "VALUES (?,?,?,?,?)", (name, description, str(path), now, now))
            version = 1
        if addresses_observation:
            conn.execute("UPDATE skillops_observations SET status='addressed' "
                         "WHERE id=?", (addresses_observation,))
    return {"status": "installed_as_candidate", "name": name,
            "version": version, "path": str(path), "smoke_test": smoke,
            "next": "call activate_skill to enable it (takes effect next PJ session)"}


def activate_skill(name: str) -> dict:
    """Promote a candidate skill to active so PJ loads it as a tool."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE skillops_registry SET status='active', "
            "updated_at=? WHERE name=? AND status IN ('candidate','deprecated')",
            (datetime.now(timezone.utc).isoformat(), name))
    if not cur.rowcount:
        return {"status": "not_found_or_already_active", "name": name}
    return {"status": "active", "name": name,
            "note": "loads on next PJ start"}


def deprecate_skill(name: str, reason: str = "") -> dict:
    """Deprecate an active skill (stops loading; file and history kept)."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE skillops_registry SET status='deprecated', review_note=?, "
            "updated_at=? WHERE name=?",
            (reason[:500], datetime.now(timezone.utc).isoformat(), name))
    if not cur.rowcount:
        return {"status": "not_found", "name": name}
    return {"status": "deprecated", "name": name, "reason": reason}


# ------------------------------------------------------- lifecycle review
def _percentile(values, pct):
    if not values:
        return 0
    values = sorted(values)
    k = min(len(values) - 1, max(0, int(round(pct / 100 * (len(values) - 1)))))
    return values[k]


def review_skills() -> dict:
    """Aggregate live telemetry + registry and recommend lifecycle actions.

    Recommendations: maintain / observe / optimize / pause_and_revalidate /
    deprecation_candidate / promote_candidate / tech_refresh_review.
    Deterministic and advisory — activation/deprecation stay explicit.
    """
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(days=POLICY["window_days"])).isoformat()
    with _db() as conn:
        registry = conn.execute(
            "SELECT name, version, status, description, created_at, "
            "updated_at, review_note FROM skillops_registry").fetchall()
        tele = conn.execute(
            "SELECT skill, ok, latency_ms FROM skillops_telemetry "
            "WHERE ts >= ?", (window_start,)).fetchall()
        open_obs = conn.execute(
            "SELECT COUNT(*) FROM skillops_observations WHERE status='open'"
        ).fetchone()[0]

    stats = {}
    for skill, ok, latency in tele:
        s = stats.setdefault(skill, {"calls": 0, "failures": 0, "lat": []})
        s["calls"] += 1
        s["failures"] += 0 if ok else 1
        s["lat"].append(latency)

    def parse_dt(val):
        try:
            dt = datetime.fromisoformat(val)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return now

    recommendations = []
    for name, version, status, desc, created, updated, note in registry:
        if status == "retired":
            continue
        s = stats.get(name, {"calls": 0, "failures": 0, "lat": []})
        calls, fails = s["calls"], s["failures"]
        fail_rate = fails / calls if calls else 0.0
        p90 = _percentile(s["lat"], 90)
        age_days = (now - parse_dt(created)).days
        staleness_days = (now - parse_dt(updated)).days
        reasons, rec = [], "maintain"

        if status == "candidate":
            rec = "promote_candidate" if calls == 0 else (
                "promote_candidate" if fail_rate == 0 else "observe")
            reasons.append("candidate awaiting activation")
        elif calls == 0 and age_days >= POLICY["unused_age_days"]:
            rec = "deprecation_candidate"
            reasons.append(f"no calls in {POLICY['window_days']}d window, "
                           f"age {age_days}d")
        elif calls >= POLICY["min_calls_for_stats"] and \
                fail_rate >= POLICY["pause_failure_rate"]:
            rec = "pause_and_revalidate"
            reasons.append(f"failure rate {fail_rate:.0%} over {calls} calls")
        elif calls >= POLICY["min_calls_for_stats"] and \
                fail_rate >= POLICY["optimize_failure_rate"]:
            rec = "optimize"
            reasons.append(f"failure rate {fail_rate:.0%}")
        elif p90 > POLICY["slow_p90_ms"]:
            rec = "optimize"
            reasons.append(f"p90 latency {p90}ms")
        if status == "active" and staleness_days >= POLICY["stale_age_days"]:
            reasons.append(f"unchanged for {staleness_days}d — check for "
                           "newer tech/APIs and changing usage trends")
            if rec == "maintain":
                rec = "tech_refresh_review"

        recommendations.append({
            "skill": name, "version": version, "status": status,
            "recommendation": rec, "reasons": reasons,
            "calls_30d": calls, "failure_rate": round(fail_rate, 3),
            "p90_latency_ms": p90, "review_note": note or None})

    # Built-in skills seen in telemetry but not in the registry.
    builtin = []
    for skill, s in stats.items():
        if any(r[0] == skill for r in registry):
            continue
        fail_rate = s["failures"] / s["calls"] if s["calls"] else 0
        builtin.append({"skill": skill, "calls_30d": s["calls"],
                        "failure_rate": round(fail_rate, 3),
                        "p90_latency_ms": _percentile(s["lat"], 90)})

    return {"generated_at": now.isoformat(),
            "policy": POLICY,
            "open_observations": open_obs,
            "registry_recommendations": recommendations,
            "builtin_skill_stats": sorted(builtin,
                                          key=lambda b: -b["calls_30d"]),
            "guidance": ("Cross-reference open observations with these stats "
                         "to decide the next skill to create, upgrade, or "
                         "deprecate. Use web research for current best-in-"
                         "class tech before writing an upgrade.")}


# ----------------------------------------------------- dynamic skill loading
def load_generated_skills():
    """Return (schemas, dispatch) for all active generated skills."""
    schemas, dispatch = [], {}
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT name, path FROM skillops_registry "
                "WHERE status='active'").fetchall()
    except Exception:
        return schemas, dispatch
    for name, path in rows:
        p = Path(path)
        if not p.exists() or name in RESERVED_NAMES:
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"pj_generated_{name}", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if callable(getattr(mod, "run", None)) and \
                    isinstance(getattr(mod, "SCHEMA", None), dict):
                schemas.append(mod.SCHEMA)
                dispatch[name] = mod.run
        except Exception as exc:
            record_invocation(name, False, 0, f"load failure: {exc}")
    return schemas, dispatch


# ----------------------------------------------------------- tool schemas
SKILLOPS_SCHEMAS = [
    {"type": "function", "name": "observe_pattern",
     "description": ("Record a recurring task, friction point, or usage "
                     "pattern that might justify creating a new PJ skill. "
                     "Use proactively whenever the user repeats a manual "
                     "workflow."),
     "parameters": {"type": "object", "properties": {
         "pattern": {"type": "string",
                     "description": "What keeps happening"},
         "context": {"type": "string",
                     "description": "Why it matters / examples"},
         "frequency_hint": {"type": "string",
                            "description": "e.g. 'daily', '3x this week'"}},
         "required": ["pattern"]}},
    {"type": "function", "name": "list_observations",
     "description": "List recorded automation-worthy patterns (open, addressed, or all).",
     "parameters": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["open", "addressed", "all"]}},
         "required": []}},
    {"type": "function", "name": "create_skill",
     "description": ("Install a new or upgraded PJ skill you have written. "
                     "Provide Python code defining run(**kwargs)->dict, plus "
                     "a JSON-schema parameters object. It is validated, "
                     "optionally smoke-tested, and registered as a candidate "
                     "pending activation. Prefer stdlib-only, fast, "
                     "deterministic implementations; research current best "
                     "practices before writing."),
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string",
                  "description": "lowercase_identifier tool name"},
         "description": {"type": "string",
                         "description": "What the skill does (tool description)"},
         "code": {"type": "string",
                  "description": "Python source defining run(**kwargs) -> dict"},
         "parameters_json": {"type": "string",
                             "description": "JSON string of the tool's parameters schema"},
         "test_args_json": {"type": "string",
                            "description": "Optional JSON kwargs for a sandbox smoke test"},
         "addresses_observation": {"type": "string",
                                   "description": "Optional observation id this skill resolves"}},
         "required": ["name", "description", "code"]}},
    {"type": "function", "name": "activate_skill",
     "description": ("Promote a candidate (or previously deprecated) skill "
                     "to active. Active skills load as callable tools on the "
                     "next PJ session."),
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string"}}, "required": ["name"]}},
    {"type": "function", "name": "review_skills",
     "description": ("Run the SkillOps lifecycle review: aggregates live "
                     "telemetry (calls, failure rates, latency) across all "
                     "skills and recommends promote / optimize / pause / "
                     "deprecate / tech-refresh actions. Use periodically and "
                     "before creating new skills."),
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"type": "function", "name": "deprecate_skill",
     "description": "Deprecate a generated skill so it no longer loads (reversible via activate_skill).",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string"},
         "reason": {"type": "string"}}, "required": ["name"]}},
]

SKILLOPS_DISPATCH = {
    "observe_pattern": observe_pattern,
    "list_observations": list_observations,
    "create_skill": create_skill,
    "activate_skill": activate_skill,
    "review_skills": review_skills,
    "deprecate_skill": deprecate_skill,
}
