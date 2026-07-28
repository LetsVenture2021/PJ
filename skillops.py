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
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent
_DB_PATH = _ROOT / "pj_data.sqlite3"
GENERATED_DIR = _ROOT / "generated_skills"
GENERATED_DIR.mkdir(exist_ok=True)
_CONFIG_PATH = _ROOT / "config.json"

# Names that can never be overridden by generated skills.
RESERVED_NAMES = {
    "get_current_time", "add_task", "list_tasks", "complete_task",
    "save_note", "search_notes", "get_active_browser_tab", "run_shortcut",
    "observe_pattern", "list_observations", "create_skill", "activate_skill",
    "review_skills", "deprecate_skill", "learn_from_vector_store",
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
    "daily_brief", "create_goal_contract", "update_goal_contract",
    "list_goal_contracts", "build_evidence_bundle", "timeline_replay",
    "weekly_operating_review",
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

MAX_SKILL_SOURCE_CHARS = 20_000
FORBIDDEN_IMPORT_PREFIXES = (
    "os",
    "subprocess",
    "socket",
    "ctypes",
    "multiprocessing",
    "resource",
    "signal",
    "pty",
    "telnetlib",
)
FORBIDDEN_CALL_NAMES = {"eval", "exec", "compile", "open", "__import__", "input"}
FORBIDDEN_ATTR_CALLS = {
    ("os", "system"),
    ("os", "popen"),
    ("subprocess", "Popen"),
    ("subprocess", "run"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
}


@contextmanager
def _db():
    conn = sqlite3.connect(_DB_PATH)
    try:
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
        conn.execute("""CREATE TABLE IF NOT EXISTS skillops_learning_runs (
            run_id TEXT PRIMARY KEY,
            vector_store_id TEXT NOT NULL,
            dry_run INTEGER DEFAULT 0,
            overwrite_existing INTEGER DEFAULT 0,
            include_provisional INTEGER DEFAULT 0,
            max_files INTEGER DEFAULT 0,
            max_chars_per_file INTEGER DEFAULT 0,
            files_seen INTEGER DEFAULT 0,
            files_processed INTEGER DEFAULT 0,
            templates_created INTEGER DEFAULT 0,
            templates_updated INTEGER DEFAULT 0,
            aliases_registered INTEGER DEFAULT 0,
            items_skipped_provisional INTEGER DEFAULT 0,
            items_skipped_invalid INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            error TEXT,
            details_json TEXT DEFAULT '',
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT
        )""")
        yield conn
        conn.commit()
    finally:
        conn.close()


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


def _load_runtime_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except Exception:
        return {}


def _require_vector_store_id() -> str:
    cfg = _load_runtime_config()
    vector_store_id = str(
        cfg.get("vector_store_id") or os.getenv("PJ_VECTOR_STORE_ID") or ""
    ).strip()
    if not vector_store_id:
        raise ValueError(
            "vector_store_id is missing in config.json and PJ_VECTOR_STORE_ID is not set"
        )
    return vector_store_id


def _require_openai_api_key() -> str:
    api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")
    return api_key


def _openai_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _list_vector_store_files(vector_store_id: str, api_key: str,
                             max_files: int = 0) -> list:
    files = []
    after = None
    while True:
        params = {"limit": 100}
        if after:
            params["after"] = after
        resp = requests.get(
            f"https://api.openai.com/v1/vector_stores/{vector_store_id}/files",
            headers=_openai_headers(api_key),
            params=params,
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"vector store file list failed ({resp.status_code}): "
                f"{resp.text[:300]}"
            )
        payload = resp.json()
        data = payload.get("data", [])
        files.extend(data)
        if max_files > 0 and len(files) >= max_files:
            return files[:max_files]
        if not payload.get("has_more"):
            break
        after = payload.get("last_id") or (
            data[-1].get("id") if data else None
        )
        if not after:
            break
    return files


def _read_openai_file_content(file_id: str, api_key: str,
                              max_chars_per_file: int) -> tuple[str, bool]:
    resp = requests.get(
        f"https://api.openai.com/v1/files/{file_id}/content",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=40,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"file content read failed for {file_id} ({resp.status_code}): "
            f"{resp.text[:300]}"
        )
    text = resp.content.decode("utf-8", errors="replace")
    if max_chars_per_file > 0 and len(text) > max_chars_per_file:
        return text[:max_chars_per_file], True
    return text, False


def learn_from_vector_store(
        dry_run: bool = False,
        max_files: int = 0,
        overwrite_existing: bool = False,
        include_provisional: bool = False,
        max_chars_per_file: int = 250_000) -> dict:
    """Import template specs from every file in the configured vector store."""
    run_id = "lrn-" + str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    max_files = max(0, int(max_files or 0))
    max_chars_per_file = max(10_000, min(int(max_chars_per_file or 250_000),
                                          2_000_000))
    vector_store_id = ""
    api_key = ""
    with _db() as conn:
        conn.execute(
            "INSERT INTO skillops_learning_runs "
            "(run_id, vector_store_id, dry_run, overwrite_existing, "
            "include_provisional, max_files, max_chars_per_file, status, "
            "details_json, started_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                "unknown",
                1 if dry_run else 0,
                1 if overwrite_existing else 0,
                1 if include_provisional else 0,
                max_files,
                max_chars_per_file,
                "running",
                "{}",
                now,
            ),
        )

    totals = {
        "files_seen": 0,
        "files_processed": 0,
        "templates_created": 0,
        "templates_updated": 0,
        "aliases_registered": 0,
        "items_skipped_provisional": 0,
        "items_skipped_invalid": 0,
    }
    file_reports = []
    errors = []
    try:
        vector_store_id = _require_vector_store_id()
        api_key = _require_openai_api_key()
        with _db() as conn:
            conn.execute(
                "UPDATE skillops_learning_runs SET vector_store_id=? WHERE run_id=?",
                (vector_store_id, run_id),
            )

        files = _list_vector_store_files(vector_store_id, api_key, max_files)
        totals["files_seen"] = len(files)

        import docops

        for entry in files:
            content_file_id = entry.get("file_id") or entry.get("id")
            if not content_file_id:
                continue
            text, truncated = _read_openai_file_content(
                content_file_id, api_key, max_chars_per_file
            )
            totals["files_processed"] += 1
            imported = docops.import_doc_templates_from_knowledge_pack_text(
                text,
                overwrite_existing=overwrite_existing,
                include_provisional=include_provisional,
                dry_run=dry_run,
            )
            totals["templates_created"] += int(imported.get("templates_created", 0))
            totals["templates_updated"] += int(imported.get("templates_updated", 0))
            totals["aliases_registered"] += int(imported.get("aliases_registered", 0))
            totals["items_skipped_provisional"] += int(
                imported.get("items_skipped_provisional", 0)
            )
            totals["items_skipped_invalid"] += int(
                imported.get("items_skipped_invalid", 0)
            )
            for err in imported.get("errors", [])[:10]:
                errors.append(f"{content_file_id}: {err}")
            file_reports.append({
                "file_id": content_file_id,
                "vector_store_file_id": entry.get("id"),
                "filename": entry.get("filename"),
                "truncated": truncated,
                "items_total": imported.get("items_total", 0),
                "templates_created": imported.get("templates_created", 0),
                "templates_updated": imported.get("templates_updated", 0),
                "status": imported.get("status"),
            })

        context = (
            f"vector_store_id={vector_store_id}; dry_run={bool(dry_run)}; "
            f"files_processed={totals['files_processed']}; "
            f"templates_created={totals['templates_created']}; "
            f"templates_updated={totals['templates_updated']}; "
            f"aliases_registered={totals['aliases_registered']}"
        )
        obs = observe_pattern(
            pattern="Vector store learning sync executed",
            context=context,
            frequency_hint="on-demand",
        )

        details = {
            "totals": totals,
            "files": file_reports,
            "errors": errors[:100],
            "observation_id": obs.get("observation_id"),
        }
        with _db() as conn:
            conn.execute(
                "UPDATE skillops_learning_runs SET "
                "files_seen=?, files_processed=?, templates_created=?, "
                "templates_updated=?, aliases_registered=?, "
                "items_skipped_provisional=?, items_skipped_invalid=?, "
                "status='completed', error=NULL, details_json=?, finished_at=? "
                "WHERE run_id=?",
                (
                    totals["files_seen"],
                    totals["files_processed"],
                    totals["templates_created"],
                    totals["templates_updated"],
                    totals["aliases_registered"],
                    totals["items_skipped_provisional"],
                    totals["items_skipped_invalid"],
                    json.dumps(details),
                    datetime.now(timezone.utc).isoformat(),
                    run_id,
                ),
            )
        return {
            "status": "dry_run_complete" if dry_run else "completed",
            "run_id": run_id,
            "vector_store_id": vector_store_id,
            "dry_run": bool(dry_run),
            "files_seen": totals["files_seen"],
            "files_processed": totals["files_processed"],
            "templates_created": totals["templates_created"],
            "templates_updated": totals["templates_updated"],
            "aliases_registered": totals["aliases_registered"],
            "items_skipped_provisional": totals["items_skipped_provisional"],
            "items_skipped_invalid": totals["items_skipped_invalid"],
            "observation_id": obs.get("observation_id"),
            "file_reports": file_reports,
            "errors": errors[:50],
        }
    except Exception as exc:
        err = str(exc)
        observe_pattern(
            pattern="Vector store learning sync failed",
            context=f"run_id={run_id}; error={err[:300]}",
            frequency_hint="on-demand",
        )
        with _db() as conn:
            conn.execute(
                "UPDATE skillops_learning_runs SET status='failed', error=?, "
                "details_json=?, finished_at=? WHERE run_id=?",
                (
                    err[:500],
                    json.dumps({
                        "totals": totals,
                        "files": file_reports,
                        "errors": errors[:100],
                        "vector_store_id": vector_store_id,
                    }),
                    datetime.now(timezone.utc).isoformat(),
                    run_id,
                ),
            )
        return {"status": "failed", "run_id": run_id, "error": err}


# ---------------------------------------------------------- skill creation
def _is_forbidden_import(module_name: str) -> bool:
    if not module_name:
        return False
    return any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )


def _validate_code(name: str, code: str) -> list:
    """Static checks. Returns a list of error strings (empty = ok)."""
    errors = []
    if len(code or "") > MAX_SKILL_SOURCE_CHARS:
        errors.append(f"code exceeds max size ({MAX_SKILL_SOURCE_CHARS} chars)")
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
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_import(alias.name):
                    errors.append(f"forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            if _is_forbidden_import(module_name):
                errors.append(f"forbidden import: {module_name}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
                errors.append(f"forbidden call: {node.func.id}()")
            elif isinstance(node.func, ast.Attribute) and \
                    isinstance(node.func.value, ast.Name):
                pair = (node.func.value.id, node.func.attr)
                if pair in FORBIDDEN_ATTR_CALLS:
                    errors.append(f"forbidden call: {pair[0]}.{pair[1]}()")
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
        row = conn.execute(
            "SELECT path, status FROM skillops_registry WHERE name=?",
            (name,),
        ).fetchone()
    if not row or row[1] not in ("candidate", "deprecated"):
        return {"status": "not_found_or_already_active", "name": name}

    skill_path = Path(row[0] or "")
    if not skill_path.exists():
        return {"status": "rejected", "name": name,
                "errors": [f"skill file missing at {skill_path}"]}

    errors = _validate_code(name, skill_path.read_text())
    if errors:
        with _db() as conn:
            conn.execute(
                "UPDATE skillops_registry SET review_note=?, updated_at=? "
                "WHERE name=?",
                ("activation blocked by safety policy",
                 datetime.now(timezone.utc).isoformat(), name),
            )
        return {"status": "rejected", "name": name, "errors": errors}

    with _db() as conn:
        conn.execute(
            "UPDATE skillops_registry SET status='active', review_note='', "
            "updated_at=? WHERE name=? AND status IN ('candidate','deprecated')",
            (datetime.now(timezone.utc).isoformat(), name),
        )
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
    {"type": "function", "name": "learn_from_vector_store",
     "description": ("Process every file in PJ's configured vector store and "
                     "import ITEM specs into DocOps templates, including alias "
                     "registration and persistent SkillOps learning-run audit."),
     "parameters": {"type": "object", "properties": {
         "dry_run": {"type": "boolean",
                     "description": "Parse and report without mutating templates/aliases"},
         "max_files": {"type": "integer",
                       "description": "Optional cap on number of vector-store files to process (0=all)"},
         "overwrite_existing": {"type": "boolean",
                                "description": "Allow updating existing templates and alias remaps"},
         "include_provisional": {"type": "boolean",
                                 "description": "Import items marked provisional/draft/experimental"},
         "max_chars_per_file": {"type": "integer",
                                "description": "Max characters to read from each file before parsing"},
     }, "required": []}},
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
    "learn_from_vector_store": learn_from_vector_store,
    "deprecate_skill": deprecate_skill,
}
