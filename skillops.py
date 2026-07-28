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
import codecs
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

_ROOT = Path(__file__).resolve().parent
_DB_PATH = _ROOT / "pj_data.sqlite3"
GENERATED_DIR = _ROOT / "generated_skills"
GENERATED_DIR.mkdir(exist_ok=True)
_CONFIG_PATH = _ROOT / "config.json"
_SYNC_LOCK_PATH = Path(
    os.getenv("PJ_VECTOR_SYNC_LOCK_PATH")
    or Path.home() / "Library" / "Application Support" / "PJ"
    / "vector-store-sync.lock"
)
_VECTOR_SOURCE_CACHE_DIR = Path(
    os.getenv("PJ_VECTOR_SOURCE_CACHE_DIR")
    or Path.home() / "Library" / "Application Support" / "PJ"
    / "vector-source-cache"
)
_N8N_EVALUATION_RECEIPT_DIR = Path(
    os.getenv("PJ_N8N_EVALUATION_RECEIPT_DIR")
    or Path.home() / "Library" / "Application Support" / "PJ"
    / "n8n-evaluation-receipts"
)

DEFAULT_MAX_CHARS_PER_FILE = 5_000_000
MAX_MAX_CHARS_PER_FILE = 25_000_000
MAX_SYNC_REPORT_DETAILS = 200
SYNC_IMPORTER_REVISION = "domain-capability-registry-n8n-v2"
DEFAULT_REQUEST_RETRIES = 4
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5
N8N_CORPUS_TYPE = "n8n_capabilities"
N8N_MIN_CAPABILITIES = 40
N8N_MAX_CAPABILITIES = 80
N8N_MIN_INVENTORY_COVERAGE = 0.95
N8N_MIN_TOP5_RETRIEVAL = 0.90
N8N_MAX_EVALUATION_RECEIPT_BYTES = 128_000
DOWNLOADABLE_FILE_PURPOSES = {
    "assistants_output", "batch_output", "fine-tune-results"
}

# Names that can never be overridden by generated skills.
RESERVED_NAMES = {
    "get_current_time", "add_task", "list_tasks", "complete_task",
    "save_note", "search_notes", "get_active_browser_tab", "run_shortcut",
    "observe_pattern", "list_observations", "create_skill", "activate_skill",
    "review_skills", "deprecate_skill", "learn_from_vector_store",
    "sync_vector_store", "get_vector_sync_status", "list_coding_capabilities",
    "list_n8n_capabilities", "get_n8n_corpus_status",
    "get_pj_capability_snapshot",
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
        conn.execute("""CREATE TABLE IF NOT EXISTS skillops_vector_sync_files (
            vector_store_id TEXT NOT NULL,
            source_file_id TEXT NOT NULL,
            vector_store_file_id TEXT DEFAULT '',
            filename TEXT DEFAULT '',
            success_version_hash TEXT,
            sync_policy_hash TEXT,
            content_sha256 TEXT,
            content_chars INTEGER DEFAULT 0,
            synchronized_at TEXT,
            last_attempt_run_id TEXT DEFAULT '',
            last_attempt_status TEXT DEFAULT '',
            last_attempt_error TEXT,
            last_attempt_at TEXT,
            PRIMARY KEY (vector_store_id, source_file_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS skillops_coding_capabilities (
            item_id TEXT PRIMARY KEY,
            canonical_title TEXT NOT NULL,
            tool_family TEXT NOT NULL,
            surface TEXT NOT NULL,
            version_scope TEXT DEFAULT '',
            corpus_status TEXT DEFAULT '',
            requires_current_docs_check INTEGER DEFAULT 0,
            source_page_url TEXT DEFAULT '',
            source_record_id TEXT DEFAULT '',
            source_content_sha256 TEXT DEFAULT '',
            record_sha256 TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            what_it_teaches TEXT DEFAULT '',
            appropriate_tasks_json TEXT DEFAULT '[]',
            workflow_json TEXT DEFAULT '[]',
            safety_controls_json TEXT DEFAULT '[]',
            authoritative_sources_json TEXT DEFAULT '[]',
            metadata_json TEXT DEFAULT '{}',
            source_file_id TEXT DEFAULT '',
            source_run_id TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            retired_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS skillops_n8n_source_census (
            source_file_id TEXT PRIMARY KEY,
            vector_store_id TEXT DEFAULT '',
            vector_store_file_id TEXT DEFAULT '',
            filename TEXT DEFAULT '',
            canonical_url TEXT DEFAULT '',
            classification TEXT NOT NULL DEFAULT 'n8n_source',
            disposition_status TEXT NOT NULL DEFAULT 'pending',
            disposition_detail TEXT DEFAULT '',
            terminal INTEGER NOT NULL DEFAULT 0,
            content_sha256 TEXT DEFAULT '',
            content_chars INTEGER DEFAULT 0,
            source_version TEXT DEFAULT '',
            last_seen_run_id TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            terminal_at TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS skillops_n8n_evaluations (
            evaluation_id TEXT PRIMARY KEY,
            corpus_version TEXT DEFAULT '',
            source_file_id TEXT DEFAULT '',
            corpus_sha256 TEXT DEFAULT '',
            registry_sha256 TEXT DEFAULT '',
            receipt_sha256 TEXT DEFAULT '',
            evidence_source TEXT DEFAULT '',
            capability_count INTEGER NOT NULL DEFAULT 0,
            canonical_pages_total INTEGER NOT NULL DEFAULT 0,
            canonical_pages_covered INTEGER NOT NULL DEFAULT 0,
            inaccessible_sources_total INTEGER NOT NULL DEFAULT 0,
            inaccessible_sources_dispositioned INTEGER NOT NULL DEFAULT 0,
            retrieval_cases_total INTEGER NOT NULL DEFAULT 0,
            retrieval_top5_passed INTEGER NOT NULL DEFAULT 0,
            security_warning_cases_total INTEGER NOT NULL DEFAULT 0,
            security_warning_cases_passed INTEGER NOT NULL DEFAULT 0,
            invented_node_parameters INTEGER NOT NULL DEFAULT 0,
            credential_exposures INTEGER NOT NULL DEFAULT 0,
            gate_passed INTEGER NOT NULL DEFAULT 0,
            details_json TEXT DEFAULT '{}',
            evaluated_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS skillops_corpus_import_runs (
            run_id TEXT PRIMARY KEY,
            parent_run_id TEXT DEFAULT '',
            corpus_type TEXT NOT NULL,
            corpus_version TEXT DEFAULT '',
            source_file_id TEXT DEFAULT '',
            source_sha256 TEXT NOT NULL,
            dry_run INTEGER DEFAULT 0,
            overwrite_existing INTEGER DEFAULT 0,
            include_provisional INTEGER DEFAULT 1,
            items_total INTEGER DEFAULT 0,
            records_created INTEGER DEFAULT 0,
            records_updated INTEGER DEFAULT 0,
            records_unchanged INTEGER DEFAULT 0,
            records_skipped_existing INTEGER DEFAULT 0,
            records_skipped_provisional INTEGER DEFAULT 0,
            items_skipped_invalid INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            error TEXT,
            details_json TEXT DEFAULT '',
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT
        )""")
        existing_run_columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(skillops_learning_runs)"
            ).fetchall()
        }
        for column, definition in {
            "run_type": "TEXT DEFAULT 'learn'",
            "force": "INTEGER DEFAULT 0",
            "files_skipped_unchanged": "INTEGER DEFAULT 0",
            "files_skipped_unavailable": "INTEGER DEFAULT 0",
            "files_skipped_unsupported": "INTEGER DEFAULT 0",
            "files_failed": "INTEGER DEFAULT 0",
            "guidance_records_imported": "INTEGER DEFAULT 0",
            "capabilities_created": "INTEGER DEFAULT 0",
            "capabilities_updated": "INTEGER DEFAULT 0",
            "capabilities_unchanged": "INTEGER DEFAULT 0",
        }.items():
            if column not in existing_run_columns:
                conn.execute(
                    f"ALTER TABLE skillops_learning_runs "
                    f"ADD COLUMN {column} {definition}"
                )
        existing_import_columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(skillops_corpus_import_runs)"
            ).fetchall()
        }
        for column, definition in {
            "include_provisional": "INTEGER DEFAULT 1",
            "records_skipped_provisional": "INTEGER DEFAULT 0",
        }.items():
            if column not in existing_import_columns:
                conn.execute(
                    f"ALTER TABLE skillops_corpus_import_runs "
                    f"ADD COLUMN {column} {definition}"
                )
        existing_sync_columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(skillops_vector_sync_files)"
            ).fetchall()
        }
        for column, definition in {
            "sync_policy_hash": "TEXT",
            "terminal_status": "TEXT",
            "terminal_detail": "TEXT",
            "terminal_at": "TEXT",
        }.items():
            if column not in existing_sync_columns:
                conn.execute(
                    f"ALTER TABLE skillops_vector_sync_files "
                    f"ADD COLUMN {column} {definition}"
                )
        capability_columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(skillops_coding_capabilities)"
            ).fetchall()
        }
        for column, definition in {
            "corpus_type": "TEXT DEFAULT 'ai_coding_capabilities'",
            "taxonomy_json": "TEXT DEFAULT '[]'",
            "task_types_json": "TEXT DEFAULT '[]'",
            "output_contract": "TEXT DEFAULT ''",
            "cloud_self_hosted_json": "TEXT DEFAULT '[]'",
            "version_constraints_json": "TEXT DEFAULT '[]'",
            "validation_json": "TEXT DEFAULT '[]'",
            "failure_modes_json": "TEXT DEFAULT '[]'",
            "freshness_json": "TEXT DEFAULT '[]'",
            "approval_policy": "TEXT DEFAULT ''",
            "active": "INTEGER DEFAULT 1",
            "retired_at": "TEXT",
        }.items():
            if column not in capability_columns:
                conn.execute(
                    f"ALTER TABLE skillops_coding_capabilities "
                    f"ADD COLUMN {column} {definition}"
                )
        conn.execute(
            "UPDATE skillops_coding_capabilities "
            "SET corpus_type='ai_coding_capabilities' "
            "WHERE corpus_type IS NULL OR corpus_type=''"
        )
        conn.execute(
            "UPDATE skillops_coding_capabilities SET active=1 "
            "WHERE active IS NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_skillops_capabilities_corpus "
            "ON skillops_coding_capabilities(corpus_type, canonical_title)"
        )
        evaluation_columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(skillops_n8n_evaluations)"
            ).fetchall()
        }
        for column, definition in {
            "corpus_sha256": "TEXT DEFAULT ''",
            "registry_sha256": "TEXT DEFAULT ''",
            "receipt_sha256": "TEXT DEFAULT ''",
            "evidence_source": "TEXT DEFAULT ''",
        }.items():
            if column not in evaluation_columns:
                conn.execute(
                    f"ALTER TABLE skillops_n8n_evaluations "
                    f"ADD COLUMN {column} {definition}"
                )
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
    configured = cfg.get("vector_store_ids")
    if isinstance(configured, list) and configured:
        configured = configured[0]
    vector_store_id = str(
        configured or cfg.get("vector_store_id")
        or os.getenv("PJ_VECTOR_STORE_ID") or ""
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


def _request_with_retry(url: str, *, headers: dict, params: dict = None,
                        timeout: int = 30, stream: bool = False,
                        retries: int = DEFAULT_REQUEST_RETRIES):
    last_error = ""
    attempts = max(1, int(retries or 1))
    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=timeout,
                stream=stream,
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            retryable = True
        else:
            if response.status_code < 400:
                return response
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable:
                raise RuntimeError(last_error)
            response.close()
        if not retryable or attempt + 1 >= attempts:
            break
        time.sleep(DEFAULT_RETRY_BACKOFF_SECONDS * (2 ** attempt))
    raise RuntimeError(
        f"request failed after {attempts} attempts: {last_error}"
    )


def _list_vector_store_files(vector_store_id: str, api_key: str,
                             max_files: int = 0) -> list:
    files = []
    after = None
    while True:
        params = {"limit": 100}
        if after:
            params["after"] = after
        resp = _request_with_retry(
            f"https://api.openai.com/v1/vector_stores/{vector_store_id}/files",
            headers=_openai_headers(api_key),
            params=params,
            timeout=30,
        )
        payload = resp.json()
        resp.close()
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


def _get_openai_file_metadata(file_id: str, api_key: str) -> dict:
    response = _request_with_retry(
        f"https://api.openai.com/v1/files/{file_id}",
        headers=_openai_headers(api_key),
        timeout=30,
    )
    try:
        payload = response.json()
    finally:
        response.close()
    return payload if isinstance(payload, dict) else {}


def _list_openai_file_metadata(api_key: str, file_ids: set[str]) -> dict:
    """Bulk-resolve File metadata so large stores avoid one request per file."""
    remaining = {str(file_id) for file_id in file_ids if file_id}
    metadata = {}
    after = None
    seen_cursors = set()
    while remaining:
        params = {"limit": 10_000, "order": "desc"}
        if after:
            params["after"] = after
        response = _request_with_retry(
            "https://api.openai.com/v1/files",
            headers=_openai_headers(api_key),
            params=params,
            timeout=60,
        )
        try:
            payload = response.json()
        finally:
            response.close()
        data = payload.get("data", [])
        for item in data:
            file_id = str(item.get("id") or "")
            if file_id in remaining:
                metadata[file_id] = item
                remaining.remove(file_id)
        if not payload.get("has_more") or not data:
            break
        cursor = payload.get("last_id") or data[-1].get("id")
        if not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
        after = cursor

    for file_id in remaining:
        metadata[file_id] = _get_openai_file_metadata(file_id, api_key)
    return metadata


def _file_content_is_downloadable(metadata: dict) -> bool:
    purpose = str(metadata.get("purpose") or "").strip().lower()
    return not purpose or purpose in DOWNLOADABLE_FILE_PURPOSES


def _safe_file_id(file_id: str) -> str:
    value = str(file_id or "")
    if (
        not value.startswith("file-")
        or len(value) > 128
        or any(not (char.isalnum() or char in "-_") for char in value)
    ):
        raise ValueError("invalid OpenAI file ID")
    return value


def cache_vector_source(file_id: str, content: bytes, *,
                        filename: str = "", source_sha256: str = "") -> dict:
    """Securely cache source text that OpenAI's input File API cannot return."""
    file_id = _safe_file_id(file_id)
    if not isinstance(content, bytes):
        raise ValueError("content must be bytes")
    if len(content) > MAX_MAX_CHARS_PER_FILE:
        raise ValueError(
            f"source exceeds cache limit ({MAX_MAX_CHARS_PER_FILE} bytes)"
        )
    content.decode("utf-8")
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if source_sha256 and source_sha256 != actual_sha256:
        raise ValueError("source_sha256 does not match content")

    if (
        _VECTOR_SOURCE_CACHE_DIR.exists()
        and _VECTOR_SOURCE_CACHE_DIR.is_symlink()
    ):
        raise RuntimeError("vector source cache directory must not be a symlink")
    _VECTOR_SOURCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(_VECTOR_SOURCE_CACHE_DIR, 0o700)
    content_path = _VECTOR_SOURCE_CACHE_DIR / f"{file_id}.content"
    manifest_path = _VECTOR_SOURCE_CACHE_DIR / f"{file_id}.json"
    manifest = {
        "file_id": file_id,
        "filename": str(filename or ""),
        "bytes": len(content),
        "source_sha256": actual_sha256,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary_paths = []
    try:
        with tempfile.NamedTemporaryFile(
            dir=_VECTOR_SOURCE_CACHE_DIR,
            prefix=f".{file_id}.",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_content = Path(handle.name)
            temporary_paths.append(temporary_content)
        temporary_manifest = (
            _VECTOR_SOURCE_CACHE_DIR / f".{file_id}.{uuid.uuid4().hex}.json"
        )
        temporary_manifest.write_text(
            json.dumps(manifest, sort_keys=True),
            encoding="utf-8",
        )
        temporary_paths.append(temporary_manifest)
        os.chmod(temporary_content, 0o600)
        os.chmod(temporary_manifest, 0o600)
        os.replace(temporary_content, content_path)
        os.replace(temporary_manifest, manifest_path)
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)
    return {
        "status": "cached",
        "file_id": file_id,
        "filename": manifest["filename"],
        "bytes": manifest["bytes"],
        "source_sha256": actual_sha256,
    }


def _read_cached_vector_source(file_id: str, metadata: dict, entry: dict,
                               max_chars_per_file: int) -> str | None:
    file_id = _safe_file_id(file_id)
    if (
        _VECTOR_SOURCE_CACHE_DIR.exists()
        and _VECTOR_SOURCE_CACHE_DIR.is_symlink()
    ):
        raise RuntimeError("vector source cache directory must not be a symlink")
    content_path = _VECTOR_SOURCE_CACHE_DIR / f"{file_id}.content"
    manifest_path = _VECTOR_SOURCE_CACHE_DIR / f"{file_id}.json"
    if not content_path.exists() and not manifest_path.exists():
        return None
    if (
        not content_path.is_file()
        or content_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.is_symlink()
    ):
        raise RuntimeError(f"cached source for {file_id} is incomplete or unsafe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    content = content_path.read_bytes()
    actual_sha256 = hashlib.sha256(content).hexdigest()
    expected_sha256 = str(
        (entry.get("attributes") or {}).get("source_sha256")
        or manifest.get("source_sha256")
        or ""
    )
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise RuntimeError(f"cached source hash mismatch for {file_id}")
    expected_bytes = int(metadata.get("bytes") or manifest.get("bytes") or 0)
    if expected_bytes and len(content) != expected_bytes:
        raise RuntimeError(f"cached source byte count mismatch for {file_id}")
    text = content.decode("utf-8")
    if max_chars_per_file > 0 and len(text) > max_chars_per_file:
        raise ValueError(
            f"file {file_id} exceeds max_chars_per_file "
            f"({max_chars_per_file}); no partial content was processed"
        )
    return text


def _cached_vector_source_fingerprint(file_id: str) -> str:
    file_id = _safe_file_id(file_id)
    manifest_path = _VECTOR_SOURCE_CACHE_DIR / f"{file_id}.json"
    if not manifest_path.exists():
        return ""
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError(f"cached source manifest for {file_id} is unsafe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fingerprint = str(manifest.get("source_sha256") or "").lower()
    if (
        len(fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in fingerprint)
    ):
        raise RuntimeError(f"cached source manifest for {file_id} has no valid hash")
    return fingerprint


def _read_openai_file_content(file_id: str, api_key: str,
                              max_chars_per_file: int,
                              expected_bytes: int = 0) -> tuple[str, bool]:
    headers = _openai_headers(api_key)
    headers["Accept-Encoding"] = "identity"
    response = _request_with_retry(
        f"https://api.openai.com/v1/files/{file_id}/content",
        headers=headers,
        timeout=40,
        stream=True,
    )
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    chunks = []
    char_count = 0
    byte_count = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            byte_count += len(chunk)
            decoded = decoder.decode(chunk)
            char_count += len(decoded)
            if max_chars_per_file > 0 and char_count > max_chars_per_file:
                raise ValueError(
                    f"file {file_id} exceeds max_chars_per_file "
                    f"({max_chars_per_file}); no partial content was processed"
                )
            chunks.append(decoded)
        tail = decoder.decode(b"", final=True)
        char_count += len(tail)
        if max_chars_per_file > 0 and char_count > max_chars_per_file:
            raise ValueError(
                f"file {file_id} exceeds max_chars_per_file "
                f"({max_chars_per_file}); no partial content was processed"
            )
        chunks.append(tail)
        content_length = int(response.headers.get("Content-Length") or 0)
        expected_bytes = int(expected_bytes or 0)
        for expected, source in (
            (content_length, "HTTP Content-Length"),
            (expected_bytes, "file metadata"),
        ):
            if expected > 0 and byte_count != expected:
                raise RuntimeError(
                    f"incomplete file read for {file_id}: received "
                    f"{byte_count} bytes, expected {expected} from {source}"
                )
    finally:
        response.close()
    return "".join(chunks), False


def _markdown_heading_section(text: str, heading: str) -> str:
    match = re.search(
        rf"^####\s+{re.escape(heading)}\s*$"
        rf"(.*?)(?=^####\s+|\Z)",
        text or "",
        flags=re.M | re.S | re.I,
    )
    return match.group(1).strip() if match else ""


def _markdown_section_list(text: str, heading: str,
                           ordered: bool = False) -> list:
    section = _markdown_heading_section(text, heading)
    if not section:
        return []
    pattern = r"^\s*\d+[.)]\s+(.+?)\s*$" if ordered \
        else r"^\s*-\s+(.+?)\s*$"
    values = []
    seen = set()
    for match in re.finditer(pattern, section, flags=re.M):
        value = match.group(1).strip()
        key = value.lower()
        if value and key not in seen:
            values.append(value)
            seen.add(key)
    return values


def _parse_leading_item_metadata(text: str) -> dict:
    import docops

    candidate = (text or "").lstrip()
    yaml_fence = re.match(
        r"```ya?ml\s*(.*?)\s*```",
        candidate,
        flags=re.S | re.I,
    )
    if yaml_fence:
        return docops._parse_key_value_text(yaml_fence.group(1))
    json_fence = re.match(
        r"```json\s*(\{.*?\})\s*```",
        candidate,
        flags=re.S | re.I,
    )
    if json_fence:
        try:
            parsed = json.loads(json_fence.group(1))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _validate_item_corpus_framing(text: str) -> dict:
    import docops

    candidate = text or ""
    starts = re.findall(
        r"^---ITEM_START(?:[^\r\n]*)$",
        candidate,
        flags=re.M | re.I,
    )
    ends = re.findall(
        r"^---ITEM_END(?:[^\r\n]*)$",
        candidate,
        flags=re.M | re.I,
    )
    blocks = docops._extract_item_blocks(candidate)
    errors = []
    if starts or ends:
        if len(starts) != len(ends) or len(blocks) != len(starts):
            errors.append(
                "corpus has incomplete or unmatched ITEM markers "
                f"(starts={len(starts)}, ends={len(ends)}, "
                f"complete={len(blocks)})"
            )
        for index, block in enumerate(blocks, start=1):
            if block["marker_id"] != block["end_marker_id"]:
                errors.append(
                    f"item {index}: marker IDs do not match "
                    f"(start={block['marker_id']!r}, "
                    f"end={block['end_marker_id']!r})"
                )
    return {"items_total": len(blocks), "blocks": blocks, "errors": errors}


def _invalid_vector_corpus_result(corpus_type: str, framing: dict) -> dict:
    return {
        "status": "invalid",
        "corpus_type": corpus_type,
        "items_total": framing["items_total"],
        "templates_created": 0,
        "templates_updated": 0,
        "guidance_records_imported": 0,
        "capabilities_created": 0,
        "capabilities_updated": 0,
        "capabilities_unchanged": 0,
        "aliases_registered": 0,
        "record_count": 0,
        "items_skipped_provisional": 0,
        "items_skipped_invalid": max(1, len(framing["errors"])),
        "errors": list(framing["errors"]),
    }


def _parse_coding_capability_corpus(text: str) -> dict:
    import docops

    text = text or ""
    blocks = docops._extract_item_blocks(text)
    first_marker = re.search(r"^---ITEM_START", text, flags=re.M | re.I)
    preamble = text[:first_marker.start()] if first_marker else text
    start_count = len(re.findall(
        r"^---ITEM_START(?:[^\r\n]*)$",
        text,
        flags=re.M | re.I,
    ))
    end_count = len(re.findall(
        r"^---ITEM_END(?:[^\r\n]*)$",
        text,
        flags=re.M | re.I,
    ))
    errors = []
    invalid = 0
    if start_count != end_count or len(blocks) != start_count:
        invalid += max(1, max(start_count, end_count) - len(blocks))
        errors.append(
            "coding corpus has incomplete or unmatched ITEM markers "
            f"(starts={start_count}, ends={end_count}, complete={len(blocks)})"
        )
    declared_match = re.search(
        r"^\s*record_count:\s*(.*?)\s*$",
        preamble,
        flags=re.M | re.I,
    )
    declared_count = None
    if not declared_match:
        invalid += 1
        errors.append("coding corpus is missing required record_count")
    elif not re.fullmatch(r"[1-9]\d*", declared_match.group(1).strip()):
        invalid += 1
        errors.append("coding corpus record_count must be a positive integer")
    else:
        declared_count = int(declared_match.group(1))
    if declared_count is not None and declared_count != len(blocks):
        invalid += abs(declared_count - len(blocks))
        errors.append(
            "coding corpus item count does not match record_count "
            f"(declared={declared_count}, complete={len(blocks)})"
        )
    version_match = re.search(
        r"^\s*corpus_version:\s*(.*?)\s*$",
        preamble,
        flags=re.M | re.I,
    )
    corpus_version = version_match.group(1).strip() if version_match else ""
    if not corpus_version:
        invalid += 1
        errors.append("coding corpus is missing required corpus_version")
    records = []
    seen_ids = set()
    for index, block in enumerate(blocks, start=1):
        marker_id = block["marker_id"]
        end_marker_id = block["end_marker_id"]
        if marker_id != end_marker_id:
            invalid += 1
            errors.append(
                f"item {index}: marker IDs do not match "
                f"(start={marker_id!r}, end={end_marker_id!r})"
            )
            continue
        metadata = _parse_leading_item_metadata(block["content"])
        item_id = str(metadata.get("item_id") or marker_id or "").strip()
        title = str(metadata.get("canonical_title") or "").strip()
        tool_family = str(metadata.get("tool_family") or "").strip()
        surface = str(metadata.get("surface") or "").strip()
        if not item_id or not title or not tool_family or not surface:
            invalid += 1
            errors.append(
                f"item {index}: capability record requires item_id, "
                "canonical_title, tool_family, and surface"
            )
            continue
        freshness_raw = metadata.get("requires_current_docs_check")
        if isinstance(freshness_raw, bool):
            requires_current_docs_check = freshness_raw
        elif (
            isinstance(freshness_raw, str)
            and freshness_raw.strip().lower() in {"true", "false"}
        ):
            requires_current_docs_check = (
                freshness_raw.strip().lower() == "true"
            )
        else:
            invalid += 1
            errors.append(
                f"item {index}: requires_current_docs_check must be an "
                "explicit boolean"
            )
            continue
        if item_id != marker_id:
            invalid += 1
            errors.append(
                f"item {index}: item_id {item_id!r} does not match "
                f"marker {marker_id!r}"
            )
            continue
        if item_id in seen_ids:
            invalid += 1
            errors.append(f"item {index}: duplicate item_id {item_id!r}")
            continue
        seen_ids.add(item_id)
        record_sha256 = hashlib.sha256(
            block["content"].encode("utf-8")
        ).hexdigest()
        records.append({
            "item_id": item_id,
            "canonical_title": title,
            "tool_family": tool_family,
            "surface": surface,
            "version_scope": str(metadata.get("version_scope") or "").strip(),
            "corpus_status": str(metadata.get("corpus_status") or "").strip(),
            "requires_current_docs_check": requires_current_docs_check,
            "provisional": (
                docops._is_truthy(
                    metadata.get("provisional", metadata.get("is_provisional"))
                )
                or "provisional" in str(
                    metadata.get("corpus_status") or ""
                ).strip().lower()
                or str(metadata.get("corpus_status") or "").strip().lower()
                in {"draft", "experimental", "wip"}
            ),
            "source_page_url": str(
                metadata.get("source_page_url") or ""
            ).strip(),
            "source_record_id": str(
                metadata.get("source_record_id") or item_id
            ).strip(),
            "source_content_sha256": str(
                metadata.get("content_sha256") or ""
            ).strip(),
            "record_sha256": record_sha256,
            "what_it_teaches": docops._extract_markdown_field(
                block["content"],
                "What this item teaches",
            ),
            "appropriate_tasks": _markdown_section_list(
                block["content"],
                "Appropriate tasks",
            ),
            "workflow": _markdown_section_list(
                block["content"],
                "Recommended operating workflow",
                ordered=True,
            ),
            "safety_controls": _markdown_section_list(
                block["content"],
                "Safety and governance controls",
            ),
            "authoritative_sources": _markdown_section_list(
                block["content"],
                "Current authoritative sources",
            ),
            "metadata": metadata,
        })
    return {
        "corpus_type": "ai_coding_capabilities",
        "corpus_version": corpus_version,
        "declared_record_count": declared_count,
        "items_total": len(blocks),
        "records": records,
        "items_skipped_invalid": invalid,
        "errors": errors,
    }


def _looks_like_coding_capability_corpus(text: str) -> bool:
    candidate = text or ""
    first_marker = re.search(r"^---ITEM_START", candidate, flags=re.M | re.I)
    preamble = candidate[:first_marker.start()] if first_marker else candidate
    if (
        re.search(
            rf"^\s*corpus_type\s*:\s*{re.escape(N8N_CORPUS_TYPE)}\s*$",
            preamble,
            flags=re.M | re.I,
        )
        or re.search(r"^\s*#\s+.*\bn8n\b.*\bcorpus\b", preamble, flags=re.M | re.I)
    ):
        return False
    first_item = re.search(
        r"^---ITEM_START(?:[^\r\n]*)\r?\n"
        r"(?P<body>.*?)(?=^---ITEM_(?:END|START)|\Z)",
        candidate,
        flags=re.M | re.S | re.I,
    )
    first_body = first_item.group("body") if first_item else ""
    metadata = _parse_leading_item_metadata(first_body)
    header_signal = bool(re.search(
        r"^\s*#\s+AI CODING TOOLS\s+[—-]+\s+"
        r"VECTOR-STORE TRAINING CORPUS\s*$",
        preamble,
        flags=re.M | re.I,
    ))
    training_signal = bool(re.search(
        r"^##\s+TRAINING ITEMS\s*$",
        preamble,
        flags=re.M | re.I,
    ))
    corpus_metadata_signal = bool(re.search(
        r"^\s*(?:corpus_version|record_count)\s*:",
        preamble,
        flags=re.M | re.I,
    ))
    capability_metadata_count = sum(
        key in metadata
        for key in (
            "tool_family",
            "surface",
            "source_record_id",
            "requires_current_docs_check",
            "corpus_status",
            "version_scope",
        )
    )
    return bool(
        capability_metadata_count >= 3
        and (
            header_signal
            or training_signal
            or corpus_metadata_signal
        )
    )


def _looks_like_codeops_guidance(text: str) -> bool:
    framing = _validate_item_corpus_framing(text)
    if framing["errors"] or not framing["blocks"]:
        return False
    for block in framing["blocks"]:
        metadata = _parse_leading_item_metadata(block["content"])
        metadata_signals = (
            "source_page_url",
            "source_record_id",
            "canonical_title",
            "tool_family",
            "surface",
            "version_scope",
            "corpus_status",
            "requires_current_docs_check",
        )
        signal_count = sum(
            metadata.get(key) not in (None, "") for key in metadata_signals
        )
        section_count = sum(
            bool(_markdown_heading_section(block["content"], heading))
            for heading in (
                "Prompt contract",
                "Output contract",
                "Evaluation checklist",
            )
        )
        if (
            section_count >= 2
            and signal_count >= 4
            and (
                metadata.get("source_page_url")
                or metadata.get("source_record_id")
            )
        ):
            return True
    return False


def _is_coding_capability_corpus(text: str) -> bool:
    if not _looks_like_coding_capability_corpus(text):
        return False
    parsed = _parse_coding_capability_corpus(text)
    return (
        parsed["items_total"] > 0
        and not parsed["errors"]
        and len(parsed["records"]) == parsed["items_total"]
    )


def import_coding_capability_corpus_text(
        corpus_text: str,
        overwrite_existing: bool = True,
        include_provisional: bool = True,
        dry_run: bool = False,
        source_file_id: str = "",
        parent_run_id: str = "",
        audit: bool = True) -> dict:
    """Import structured coding-tool guidance into the capability registry."""
    parsed = _parse_coding_capability_corpus(corpus_text)
    import_run_id = "cap-" + str(uuid.uuid4())[:8]
    source_sha256 = hashlib.sha256(
        (corpus_text or "").encode("utf-8")
    ).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    if audit:
        with _db() as conn:
            conn.execute(
                "INSERT INTO skillops_corpus_import_runs "
                "(run_id, parent_run_id, corpus_type, corpus_version, "
                "source_file_id, source_sha256, dry_run, overwrite_existing, "
                "include_provisional, status, details_json, started_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    import_run_id,
                    parent_run_id,
                    parsed["corpus_type"],
                    parsed["corpus_version"],
                    source_file_id,
                    source_sha256,
                    1 if dry_run else 0,
                    1 if overwrite_existing else 0,
                    1 if include_provisional else 0,
                    "running",
                    "{}",
                    now,
                ),
            )

    created = updated = unchanged = skipped_existing = skipped_provisional = 0
    imports = []
    if not parsed["errors"]:
        with _db() as conn:
            for record in parsed["records"]:
                if record["provisional"] and not include_provisional:
                    skipped_provisional += 1
                    imports.append({
                        "item_id": record["item_id"],
                        "canonical_title": record["canonical_title"],
                        "tool_family": record["tool_family"],
                        "surface": record["surface"],
                        "version": None,
                        "action": "skipped_provisional",
                    })
                    continue
                existing = conn.execute(
                    "SELECT record_sha256, version "
                    "FROM skillops_coding_capabilities "
                    "WHERE item_id=? AND corpus_type='ai_coding_capabilities'",
                    (record["item_id"],),
                ).fetchone()
                if existing and existing[0] == record["record_sha256"]:
                    action = "unchanged"
                    version = existing[1]
                    unchanged += 1
                elif existing and not overwrite_existing:
                    action = "skipped_existing"
                    version = existing[1]
                    skipped_existing += 1
                elif dry_run:
                    action = "would_update" if existing else "would_create"
                    version = existing[1] + 1 if existing else 1
                    if existing:
                        updated += 1
                    else:
                        created += 1
                else:
                    values = (
                        record["canonical_title"],
                        record["tool_family"],
                        record["surface"],
                        record["version_scope"],
                        record["corpus_status"],
                        1 if record["requires_current_docs_check"] else 0,
                        record["source_page_url"],
                        record["source_record_id"],
                        record["source_content_sha256"],
                        record["record_sha256"],
                        record["what_it_teaches"],
                        json.dumps(record["appropriate_tasks"]),
                        json.dumps(record["workflow"]),
                        json.dumps(record["safety_controls"]),
                        json.dumps(record["authoritative_sources"]),
                        json.dumps(record["metadata"]),
                        source_file_id,
                        parent_run_id or import_run_id,
                        now,
                    )
                    if existing:
                        conn.execute(
                            "UPDATE skillops_coding_capabilities SET "
                            "corpus_type='ai_coding_capabilities', "
                            "canonical_title=?, tool_family=?, surface=?, "
                            "version_scope=?, corpus_status=?, "
                            "requires_current_docs_check=?, source_page_url=?, "
                            "source_record_id=?, source_content_sha256=?, "
                            "record_sha256=?, version=version+1, "
                            "what_it_teaches=?, appropriate_tasks_json=?, "
                            "workflow_json=?, safety_controls_json=?, "
                            "authoritative_sources_json=?, metadata_json=?, "
                            "source_file_id=?, source_run_id=?, updated_at=? "
                            "WHERE item_id=?",
                            values + (record["item_id"],),
                        )
                        action = "updated"
                        version = existing[1] + 1
                        updated += 1
                    else:
                        conn.execute(
                            "INSERT INTO skillops_coding_capabilities "
                            "(corpus_type, canonical_title, tool_family, surface, "
                            "version_scope, corpus_status, "
                            "requires_current_docs_check, source_page_url, "
                            "source_record_id, source_content_sha256, "
                            "record_sha256, what_it_teaches, "
                            "appropriate_tasks_json, workflow_json, "
                            "safety_controls_json, authoritative_sources_json, "
                            "metadata_json, source_file_id, source_run_id, "
                            "created_at, updated_at, item_id) "
                            "VALUES ('ai_coding_capabilities',?,?,?,?,?,?,?,?,?,"
                            "?,?,?,?,?,?,?,?,?,?,?,?)",
                            values + (now, record["item_id"]),
                        )
                        action = "created"
                        version = 1
                        created += 1
                imports.append({
                    "item_id": record["item_id"],
                    "canonical_title": record["canonical_title"],
                    "tool_family": record["tool_family"],
                    "surface": record["surface"],
                    "version": version,
                    "action": action,
                })

    status = "invalid" if parsed["errors"] else (
        "dry_run_complete" if dry_run else "imported"
    )
    details = {
        "imports": imports,
        "errors": parsed["errors"],
        "declared_record_count": parsed["declared_record_count"],
    }
    if audit:
        with _db() as conn:
            conn.execute(
                "UPDATE skillops_corpus_import_runs SET "
                "items_total=?, records_created=?, records_updated=?, "
                "records_unchanged=?, records_skipped_existing=?, "
                "records_skipped_provisional=?, items_skipped_invalid=?, "
                "status=?, error=?, details_json=?, "
                "finished_at=? WHERE run_id=?",
                (
                    parsed["items_total"],
                    created,
                    updated,
                    unchanged,
                    skipped_existing,
                    skipped_provisional,
                    parsed["items_skipped_invalid"],
                    status,
                    "; ".join(parsed["errors"][:3]) or None,
                    json.dumps(details),
                    datetime.now(timezone.utc).isoformat(),
                    import_run_id,
                ),
            )
    return {
        "status": status,
        "run_id": import_run_id,
        "corpus_type": parsed["corpus_type"],
        "corpus_version": parsed["corpus_version"],
        "items_total": parsed["items_total"],
        "capabilities_created": created,
        "capabilities_updated": updated,
        "capabilities_unchanged": unchanged,
        "capabilities_skipped_existing": skipped_existing,
        "items_skipped_provisional": skipped_provisional,
        "items_skipped_invalid": parsed["items_skipped_invalid"],
        "imports": imports,
        "errors": parsed["errors"],
    }


def _preamble_scalar(preamble: str, name: str) -> str:
    match = re.search(
        rf"^\s*{re.escape(name)}\s*:\s*(.*?)\s*$",
        preamble or "",
        flags=re.M | re.I,
    )
    return match.group(1).strip().strip("\"'") if match else ""


def _metadata_string_list(value) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        candidate = value.strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        raw = parsed if isinstance(parsed, list) else candidate.split(",")
    else:
        raw = []
    values = []
    seen = set()
    for item in raw:
        text = str(item or "").strip().strip("\"'")
        if text and text.casefold() not in seen:
            values.append(text)
            seen.add(text.casefold())
    return values


def _safe_https_source_url(value: str) -> str:
    candidate = str(value or "").strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return ""
    host = parsed.hostname.casefold()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", "", ""))


def _markdown_section_text(text: str, heading: str) -> str:
    return _markdown_heading_section(text, heading).strip()


_N8N_EVALUATION_FIELDS = (
    "canonical_pages_total",
    "canonical_pages_covered",
    "inaccessible_sources_total",
    "inaccessible_sources_dispositioned",
    "retrieval_cases_total",
    "retrieval_top5_passed",
    "security_warning_cases_total",
    "security_warning_cases_passed",
    "invented_node_parameters",
    "credential_exposures",
)
_N8N_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|ghp|glpat|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"""(?ix)\b(?:api[_ -]?key|password|secret|access[_ -]?token)\s*
        [:=]\s*["']?
        (?!example\b|placeholder\b|redacted\b|replace[_ -]?me\b|your[_ -]?)
        [A-Za-z0-9/+_.=-]{16,}"""
    ),
)


def _n8n_records_sha256(records: list[dict]) -> str:
    registry = [
        {
            "item_id": record["item_id"],
            "record_sha256": record["record_sha256"],
        }
        for record in sorted(records, key=lambda item: item["item_id"])
    ]
    return hashlib.sha256(
        json.dumps(
            registry,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _n8n_receipt_sha256(receipt: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            receipt,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _load_n8n_release_approval() -> dict:
    config = _load_runtime_config()
    configured = config.get("n8n_release_approval")
    configured = configured if isinstance(configured, dict) else {}
    return {
        "corpus_sha256": str(
            os.getenv("PJ_N8N_APPROVED_CORPUS_SHA256")
            or configured.get("corpus_sha256")
            or ""
        ).strip().lower(),
        "corpus_version": str(
            os.getenv("PJ_N8N_APPROVED_CORPUS_VERSION")
            or configured.get("corpus_version")
            or ""
        ).strip(),
        "evaluation_receipt_sha256": str(
            os.getenv("PJ_N8N_APPROVED_EVALUATION_SHA256")
            or configured.get("evaluation_receipt_sha256")
            or ""
        ).strip().lower(),
    }


def load_n8n_evaluation_receipt(path: Path) -> dict:
    source = Path(path).expanduser()
    if source.is_symlink():
        raise ValueError("n8n evaluation receipt must not be a symlink")
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("n8n evaluation receipt must be a regular file")
    if source.stat().st_size > N8N_MAX_EVALUATION_RECEIPT_BYTES:
        raise ValueError("n8n evaluation receipt exceeds the safety limit")
    try:
        receipt = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("n8n evaluation receipt is not valid JSON") from exc
    if not isinstance(receipt, dict):
        raise ValueError("n8n evaluation receipt must be a JSON object")
    return receipt


def cache_n8n_evaluation_receipt(
        corpus_sha256: str, receipt: dict) -> dict:
    corpus_sha256 = str(corpus_sha256 or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", corpus_sha256):
        raise ValueError("invalid n8n corpus SHA-256")
    if not isinstance(receipt, dict):
        raise ValueError("n8n evaluation receipt must be an object")
    payload = json.dumps(
        receipt,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    if len(payload) > N8N_MAX_EVALUATION_RECEIPT_BYTES:
        raise ValueError("n8n evaluation receipt exceeds the safety limit")
    if (
        _N8N_EVALUATION_RECEIPT_DIR.exists()
        and _N8N_EVALUATION_RECEIPT_DIR.is_symlink()
    ):
        raise RuntimeError(
            "n8n evaluation receipt directory must not be a symlink"
        )
    _N8N_EVALUATION_RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(_N8N_EVALUATION_RECEIPT_DIR, 0o700)
    destination = _N8N_EVALUATION_RECEIPT_DIR / f"{corpus_sha256}.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=_N8N_EVALUATION_RECEIPT_DIR,
            prefix=f".{corpus_sha256}.",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return {
        "status": "cached",
        "corpus_sha256": corpus_sha256,
        "receipt_sha256": _n8n_receipt_sha256(receipt),
    }


def _load_n8n_evaluation_receipt_for_corpus(
        corpus_sha256: str) -> dict | None:
    config = _load_runtime_config()
    configured_path = str(
        os.getenv("PJ_N8N_EVALUATION_RECEIPT_PATH")
        or config.get("n8n_evaluation_receipt_path")
        or ""
    ).strip()
    if configured_path:
        receipt = load_n8n_evaluation_receipt(Path(configured_path))
        if str(receipt.get("corpus_sha256") or "").strip().lower() == corpus_sha256:
            return receipt
    cached_path = _N8N_EVALUATION_RECEIPT_DIR / f"{corpus_sha256}.json"
    if cached_path.exists():
        return load_n8n_evaluation_receipt(cached_path)
    return None


def _n8n_evaluation_gates(
        capability_count: int,
        metrics: dict,
        *,
        source_integrity: bool) -> dict:
    canonical_total = int(metrics.get("canonical_pages_total", 0))
    canonical_covered = int(metrics.get("canonical_pages_covered", 0))
    inaccessible_total = int(metrics.get("inaccessible_sources_total", 0))
    inaccessible_dispositioned = int(
        metrics.get("inaccessible_sources_dispositioned", 0)
    )
    retrieval_total = int(metrics.get("retrieval_cases_total", 0))
    retrieval_passed = int(metrics.get("retrieval_top5_passed", 0))
    security_total = int(metrics.get("security_warning_cases_total", 0))
    security_passed = int(metrics.get("security_warning_cases_passed", 0))
    inventory_coverage = (
        canonical_covered / canonical_total if canonical_total else 0.0
    )
    inaccessible_coverage = (
        inaccessible_dispositioned / inaccessible_total
        if inaccessible_total else 1.0
    )
    retrieval_top5 = (
        retrieval_passed / retrieval_total if retrieval_total else 0.0
    )
    security_warning_retrieval = (
        security_passed / security_total if security_total else 0.0
    )
    gates = {
        "metric_consistency": all((
            0 <= canonical_covered <= canonical_total,
            0 <= inaccessible_dispositioned <= inaccessible_total,
            0 <= retrieval_passed <= retrieval_total,
            0 <= security_passed <= security_total,
        )),
        "capability_count": (
            N8N_MIN_CAPABILITIES
            <= int(capability_count)
            <= N8N_MAX_CAPABILITIES
        ),
        "inventory_coverage": (
            canonical_total > 0
            and inventory_coverage >= N8N_MIN_INVENTORY_COVERAGE
        ),
        "inaccessible_source_disposition": inaccessible_coverage >= 1.0,
        "top5_retrieval": (
            retrieval_total > 0
            and retrieval_top5 >= N8N_MIN_TOP5_RETRIEVAL
        ),
        "security_warning_retrieval": (
            security_total > 0 and security_warning_retrieval >= 1.0
        ),
        "zero_invented_node_parameters": (
            int(metrics.get("invented_node_parameters", -1)) == 0
        ),
        "zero_credential_exposure": (
            int(metrics.get("credential_exposures", -1)) == 0
        ),
        "source_integrity": bool(source_integrity),
    }
    return {
        "passed": all(gates.values()),
        "gates": gates,
        "metrics": {
            **{key: int(metrics.get(key, 0)) for key in _N8N_EVALUATION_FIELDS},
            "inventory_coverage": round(inventory_coverage, 6),
            "inaccessible_disposition_coverage": round(
                inaccessible_coverage, 6
            ),
            "top5_retrieval_rate": round(retrieval_top5, 6),
            "security_warning_retrieval_rate": round(
                security_warning_retrieval, 6
            ),
        },
    }


def _evaluate_n8n_receipt(
        parsed: dict,
        corpus_sha256: str,
        receipt: dict | None) -> tuple[dict, list[str]]:
    errors = []
    expected_registry_sha256 = _n8n_records_sha256(parsed["records"])
    if not isinstance(receipt, dict):
        errors.append(
            "an independent n8n evaluation receipt is required"
        )
        receipt = {}
    metrics = receipt.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    required_text = {
        "evaluation_id": receipt.get("evaluation_id"),
        "evaluated_at": receipt.get("evaluated_at"),
        "evaluator": receipt.get("evaluator"),
        "evidence_sha256": receipt.get("evidence_sha256"),
    }
    for name, value in required_text.items():
        if not str(value or "").strip():
            errors.append(f"n8n evaluation receipt is missing {name}")
    if str(receipt.get("schema_version") or "") != "1":
        errors.append("n8n evaluation receipt schema_version must be '1'")
    if (
        str(receipt.get("corpus_sha256") or "").strip().lower()
        != corpus_sha256
    ):
        errors.append("n8n evaluation receipt corpus_sha256 does not match")
    if (
        str(receipt.get("corpus_version") or "").strip()
        != parsed["corpus_version"]
    ):
        errors.append("n8n evaluation receipt corpus_version does not match")
    if (
        str(receipt.get("registry_sha256") or "").strip().lower()
        != expected_registry_sha256
    ):
        errors.append("n8n evaluation receipt registry_sha256 does not match")
    if not re.fullmatch(
        r"[a-f0-9]{64}",
        str(receipt.get("evidence_sha256") or "").strip().lower(),
    ):
        errors.append(
            "n8n evaluation receipt evidence_sha256 must be a lowercase SHA-256"
        )
    normalized_metrics = {}
    for field in _N8N_EVALUATION_FIELDS:
        value = metrics.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append(
                f"n8n evaluation receipt {field} must be a non-negative integer"
            )
            normalized_metrics[field] = 0
        else:
            normalized_metrics[field] = value
    declared_metrics = parsed["declared_evaluation"]["metrics"]
    if any(
        int(declared_metrics.get(field, -1))
        != normalized_metrics[field]
        for field in _N8N_EVALUATION_FIELDS
    ):
        errors.append(
            "n8n corpus-declared evaluation metrics do not match the "
            "independent receipt"
        )
    evaluation = _n8n_evaluation_gates(
        len(parsed["records"]),
        normalized_metrics,
        source_integrity=(
            bool(parsed["records"])
            and len(parsed["records"]) == parsed["items_total"]
            and all(
                record["source_content_sha256"]
                for record in parsed["records"]
            )
        ),
    )
    receipt_sha256 = _n8n_receipt_sha256(receipt) if receipt else ""
    approval = _load_n8n_release_approval()
    approval_gates = {
        "approved_corpus_digest": bool(
            re.fullmatch(
                r"[a-f0-9]{64}", approval["corpus_sha256"]
            )
            and approval["corpus_sha256"] == corpus_sha256
        ),
        "approved_corpus_version": bool(
            approval["corpus_version"]
            and approval["corpus_version"] == parsed["corpus_version"]
        ),
        "approved_evaluation_receipt": bool(
            re.fullmatch(
                r"[a-f0-9]{64}",
                approval["evaluation_receipt_sha256"],
            )
            and approval["evaluation_receipt_sha256"] == receipt_sha256
        ),
    }
    evaluation.update({
        "passed": evaluation["passed"] and not errors,
        "approval_gates": approval_gates,
        "approved": all(approval_gates.values()),
        "corpus_sha256": corpus_sha256,
        "registry_sha256": expected_registry_sha256,
        "receipt_sha256": receipt_sha256,
        "evaluation_id": str(receipt.get("evaluation_id") or ""),
        "evaluated_at": str(receipt.get("evaluated_at") or ""),
        "evaluator": str(receipt.get("evaluator") or ""),
        "evidence_sha256": str(receipt.get("evidence_sha256") or ""),
        "evidence_source": "independent_receipt" if receipt else "",
    })
    return evaluation, errors


def _looks_like_n8n_capability_corpus(text: str) -> bool:
    candidate = text or ""
    first_marker = re.search(r"^---ITEM_START", candidate, flags=re.M | re.I)
    preamble = candidate[:first_marker.start()] if first_marker else candidate
    first_item = re.search(
        r"^---ITEM_START(?:[^\r\n]*)\r?\n"
        r"(?P<body>.*?)(?=^---ITEM_(?:END|START)|\Z)",
        candidate,
        flags=re.M | re.S | re.I,
    )
    metadata = _parse_leading_item_metadata(
        first_item.group("body") if first_item else ""
    )
    header_signal = bool(re.search(
        r"^\s*#\s+.*\bn8n\b.*\b(?:capability|training)\b.*\bcorpus\b",
        preamble,
        flags=re.M | re.I,
    ))
    corpus_signal = (
        _preamble_scalar(preamble, "corpus_type").casefold()
        == N8N_CORPUS_TYPE
    )
    domain_signal = str(
        metadata.get("domain")
        or metadata.get("platform")
        or metadata.get("tool_family")
        or ""
    ).strip().casefold() == "n8n"
    contract_signals = sum(
        bool(metadata.get(key))
        for key in (
            "source_page_url",
            "source_record_id",
            "content_sha256",
            "corpus_status",
        )
    )
    return bool(domain_signal and contract_signals >= 2 and (
        header_signal or corpus_signal
    ))


def _parse_n8n_capability_corpus(text: str) -> dict:
    import docops

    text = text or ""
    framing = _validate_item_corpus_framing(text)
    blocks = framing["blocks"]
    first_marker = re.search(r"^---ITEM_START", text, flags=re.M | re.I)
    preamble = text[:first_marker.start()] if first_marker else text
    errors = list(framing["errors"])
    corpus_type = _preamble_scalar(preamble, "corpus_type")
    corpus_version = _preamble_scalar(preamble, "corpus_version")
    declared_raw = _preamble_scalar(preamble, "record_count")
    if corpus_type.casefold() != N8N_CORPUS_TYPE:
        errors.append(f"n8n corpus_type must be {N8N_CORPUS_TYPE!r}")
    if not corpus_version:
        errors.append("n8n corpus is missing required corpus_version")
    if not re.fullmatch(r"[1-9]\d*", declared_raw):
        declared_count = None
        errors.append("n8n record_count must be a positive integer")
    else:
        declared_count = int(declared_raw)
        if declared_count != len(blocks):
            errors.append(
                "n8n corpus item count does not match record_count "
                f"(declared={declared_count}, complete={len(blocks)})"
            )

    metrics = {}
    for field in _N8N_EVALUATION_FIELDS:
        raw = _preamble_scalar(preamble, field)
        if not re.fullmatch(r"\d+", raw):
            errors.append(f"n8n corpus is missing non-negative integer {field}")
            metrics[field] = 0
        else:
            metrics[field] = int(raw)

    records = []
    seen_ids = set()
    for index, block in enumerate(blocks, start=1):
        if block["marker_id"] != block["end_marker_id"]:
            errors.append(f"item {index}: marker IDs do not match")
            continue
        metadata = _parse_leading_item_metadata(block["content"])
        item_id = str(metadata.get("item_id") or block["marker_id"] or "").strip()
        title = str(metadata.get("canonical_title") or "").strip()
        domain = str(
            metadata.get("domain")
            or metadata.get("platform")
            or metadata.get("tool_family")
            or ""
        ).strip()
        source_page_url = str(metadata.get("source_page_url") or "").strip()
        source_record_id = str(
            metadata.get("source_record_id") or item_id
        ).strip()
        source_content_sha256 = str(
            metadata.get("content_sha256") or ""
        ).strip().lower()
        if (
            not item_id.startswith("N8N-")
            or item_id != block["marker_id"]
            or item_id in seen_ids
        ):
            errors.append(
                f"item {index}: item_id must be a unique N8N- identifier "
                "matching its marker"
            )
            continue
        seen_ids.add(item_id)
        freshness_raw = metadata.get("requires_current_docs_check")
        if isinstance(freshness_raw, bool):
            requires_current_docs_check = freshness_raw
        elif (
            isinstance(freshness_raw, str)
            and freshness_raw.strip().casefold() in {"true", "false"}
        ):
            requires_current_docs_check = (
                freshness_raw.strip().casefold() == "true"
            )
        else:
            errors.append(
                f"item {index}: requires_current_docs_check must be boolean"
            )
            continue

        taxonomy = _metadata_string_list(metadata.get("taxonomy")) or (
            _markdown_section_list(block["content"], "Taxonomy")
        )
        task_types = _markdown_section_list(
            block["content"], "Task types"
        )
        workflow = _markdown_section_list(
            block["content"], "Recommended operating workflow", ordered=True
        )
        output_contract = _markdown_section_text(
            block["content"], "Output contract"
        )
        safety_controls = _markdown_section_list(
            block["content"], "Safety and governance controls"
        )
        cloud_self_hosted = _markdown_section_list(
            block["content"], "Cloud and self-hosted differences"
        )
        version_constraints = _markdown_section_list(
            block["content"], "Version constraints"
        )
        validation = _markdown_section_list(
            block["content"], "Validation checklist"
        )
        failure_modes = _markdown_section_list(
            block["content"], "Failure modes"
        )
        authoritative_sources = _markdown_section_list(
            block["content"], "Current authoritative sources"
        )
        freshness = _markdown_section_list(
            block["content"], "Freshness requirements"
        )
        approval_policy = _markdown_section_text(
            block["content"], "Approval policy"
        )
        what_it_teaches = (
            docops._extract_markdown_field(
                block["content"], "What this capability teaches"
            )
            or docops._extract_markdown_field(
                block["content"], "What this item teaches"
            )
            or _markdown_section_text(
                block["content"], "What this capability teaches"
            )
        )
        missing = [
            name for name, value in (
                ("canonical_title", title),
                ("domain=n8n", domain.casefold() == "n8n"),
                ("source_page_url", source_page_url),
                ("source_record_id", source_record_id),
                ("content_sha256", source_content_sha256),
                ("corpus_status", metadata.get("corpus_status")),
                ("taxonomy", taxonomy),
                ("task types", task_types),
                ("workflow", workflow),
                ("output contract", output_contract),
                ("safety controls", safety_controls),
                ("cloud/self-hosted differences", cloud_self_hosted),
                ("version constraints", version_constraints),
                ("validation", validation),
                ("failure modes", failure_modes),
                ("authoritative sources", authoritative_sources),
                ("freshness", freshness),
                ("approval policy", approval_policy),
                ("what this capability teaches", what_it_teaches),
            )
            if not value
        ]
        if missing:
            errors.append(
                f"item {index}: missing required n8n fields: "
                + ", ".join(missing)
            )
            continue
        if (
            _safe_https_source_url(source_page_url) != source_page_url
            or any(
                _safe_https_source_url(source) != source
                for source in authoritative_sources
            )
        ):
            errors.append(
                f"item {index}: authoritative sources must be canonical HTTPS "
                "URLs without credentials, queries, or fragments"
            )
            continue
        if not re.fullmatch(r"[a-f0-9]{64}", source_content_sha256):
            errors.append(
                f"item {index}: content_sha256 must be a lowercase SHA-256"
            )
            continue
        if source_page_url not in authoritative_sources:
            errors.append(
                f"item {index}: source_page_url must be included in "
                "Current authoritative sources"
            )
            continue
        if any(
            pattern.search(block["content"])
            for pattern in _N8N_SECRET_PATTERNS
        ):
            errors.append(f"item {index}: potential credential material detected")
            continue
        records.append({
            "item_id": item_id,
            "canonical_title": title,
            "tool_family": "n8n",
            "surface": str(
                metadata.get("surface") or "Cloud and self-hosted"
            ).strip(),
            "version_scope": str(
                metadata.get("version_scope") or ""
            ).strip(),
            "corpus_status": str(
                metadata.get("corpus_status") or ""
            ).strip(),
            "requires_current_docs_check": requires_current_docs_check,
            "provisional": (
                docops._is_truthy(
                    metadata.get("provisional", metadata.get("is_provisional"))
                )
                or str(metadata.get("corpus_status") or "").strip().casefold()
                in {"draft", "experimental", "wip", "provisional"}
            ),
            "source_page_url": source_page_url,
            "source_record_id": source_record_id,
            "source_content_sha256": source_content_sha256,
            "record_sha256": hashlib.sha256(
                block["content"].encode("utf-8")
            ).hexdigest(),
            "what_it_teaches": what_it_teaches,
            "task_types": task_types,
            "workflow": workflow,
            "safety_controls": safety_controls,
            "authoritative_sources": authoritative_sources,
            "taxonomy": taxonomy,
            "output_contract": output_contract,
            "cloud_self_hosted": cloud_self_hosted,
            "version_constraints": version_constraints,
            "validation": validation,
            "failure_modes": failure_modes,
            "freshness": freshness,
            "approval_policy": approval_policy,
            "metadata": metadata,
        })

    declared_evaluation = _n8n_evaluation_gates(
        len(records),
        metrics,
        source_integrity=(
            bool(records)
            and len(records) == len(blocks)
            and all(record["source_content_sha256"] for record in records)
        ),
    )
    return {
        "corpus_type": N8N_CORPUS_TYPE,
        "corpus_version": corpus_version,
        "declared_record_count": declared_count,
        "items_total": len(blocks),
        "records": records,
        "items_skipped_invalid": max(0, len(blocks) - len(records)),
        "errors": errors,
        "declared_evaluation": declared_evaluation,
    }


def _prepare_n8n_corpus(
        corpus_text: str,
        evaluation_receipt: dict | None = None) -> dict:
    parsed = _parse_n8n_capability_corpus(corpus_text)
    corpus_sha256 = hashlib.sha256(
        (corpus_text or "").encode("utf-8")
    ).hexdigest()
    receipt = evaluation_receipt
    if receipt is None:
        try:
            receipt = _load_n8n_evaluation_receipt_for_corpus(corpus_sha256)
        except (OSError, ValueError, RuntimeError) as exc:
            parsed["errors"].append(str(exc))
    evaluation, evaluation_errors = _evaluate_n8n_receipt(
        parsed,
        corpus_sha256,
        receipt,
    )
    parsed["evaluation"] = evaluation
    parsed["errors"].extend(evaluation_errors)
    parsed["source_sha256"] = corpus_sha256
    return parsed


def preflight_n8n_corpus_text(
        corpus_text: str,
        evaluation_receipt: dict | None = None) -> dict:
    parsed = _prepare_n8n_corpus(corpus_text, evaluation_receipt)
    ingestion_ready = (
        not parsed["errors"]
        and parsed["evaluation"]["passed"]
        and parsed["evaluation"]["approved"]
        and len(parsed["records"]) == parsed["items_total"]
    )
    return {
        "status": "ready" if ingestion_ready else "blocked",
        "corpus_type": N8N_CORPUS_TYPE,
        "corpus_version": parsed["corpus_version"],
        "items_total": parsed["items_total"],
        "records_valid": len(parsed["records"]),
        "items_skipped_invalid": parsed["items_skipped_invalid"],
        "ingestion_ready": ingestion_ready,
        "evaluation": parsed["evaluation"],
        "errors": parsed["errors"],
    }


def _record_n8n_evaluation(parsed: dict, source_file_id: str) -> str:
    evaluation = parsed["evaluation"]
    evaluation_id = evaluation["evaluation_id"]
    metrics = evaluation["metrics"]
    with _db() as conn:
        conn.execute(
            "INSERT INTO skillops_n8n_evaluations "
            "(evaluation_id, corpus_version, source_file_id, corpus_sha256, "
            "registry_sha256, receipt_sha256, evidence_source, capability_count, "
            "canonical_pages_total, canonical_pages_covered, "
            "inaccessible_sources_total, inaccessible_sources_dispositioned, "
            "retrieval_cases_total, retrieval_top5_passed, "
            "security_warning_cases_total, security_warning_cases_passed, "
            "invented_node_parameters, credential_exposures, gate_passed, "
            "details_json, evaluated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(evaluation_id) DO UPDATE SET "
            "corpus_version=excluded.corpus_version, "
            "source_file_id=excluded.source_file_id, "
            "corpus_sha256=excluded.corpus_sha256, "
            "registry_sha256=excluded.registry_sha256, "
            "receipt_sha256=excluded.receipt_sha256, "
            "evidence_source=excluded.evidence_source, "
            "capability_count=excluded.capability_count, "
            "canonical_pages_total=excluded.canonical_pages_total, "
            "canonical_pages_covered=excluded.canonical_pages_covered, "
            "inaccessible_sources_total=excluded.inaccessible_sources_total, "
            "inaccessible_sources_dispositioned="
            "excluded.inaccessible_sources_dispositioned, "
            "retrieval_cases_total=excluded.retrieval_cases_total, "
            "retrieval_top5_passed=excluded.retrieval_top5_passed, "
            "security_warning_cases_total="
            "excluded.security_warning_cases_total, "
            "security_warning_cases_passed="
            "excluded.security_warning_cases_passed, "
            "invented_node_parameters=excluded.invented_node_parameters, "
            "credential_exposures=excluded.credential_exposures, "
            "gate_passed=excluded.gate_passed, "
            "details_json=excluded.details_json, "
            "evaluated_at=excluded.evaluated_at",
            (
                evaluation_id,
                parsed["corpus_version"],
                source_file_id,
                parsed["source_sha256"],
                evaluation["registry_sha256"],
                evaluation["receipt_sha256"],
                evaluation["evidence_source"],
                len(parsed["records"]),
                metrics["canonical_pages_total"],
                metrics["canonical_pages_covered"],
                metrics["inaccessible_sources_total"],
                metrics["inaccessible_sources_dispositioned"],
                metrics["retrieval_cases_total"],
                metrics["retrieval_top5_passed"],
                metrics["security_warning_cases_total"],
                metrics["security_warning_cases_passed"],
                metrics["invented_node_parameters"],
                metrics["credential_exposures"],
                1 if evaluation["passed"] else 0,
                json.dumps(evaluation, sort_keys=True),
                evaluation["evaluated_at"],
            ),
        )
    return evaluation_id


def import_n8n_capability_corpus_text(
        corpus_text: str,
        overwrite_existing: bool = True,
        include_provisional: bool = False,
        dry_run: bool = False,
        source_file_id: str = "",
        parent_run_id: str = "",
        audit: bool = True,
        evaluation_receipt: dict | None = None) -> dict:
    """Import governed n8n records into the shared domain capability registry."""
    parsed = _prepare_n8n_corpus(corpus_text, evaluation_receipt)
    if not parsed["evaluation"]["passed"]:
        failed_gates = [
            name
            for name, passed in parsed["evaluation"]["gates"].items()
            if not passed
        ]
        parsed["errors"].append(
            "n8n evaluation gates failed: " + ", ".join(failed_gates)
        )
    if not parsed["evaluation"]["approved"]:
        failed_approval = [
            name
            for name, passed
            in parsed["evaluation"]["approval_gates"].items()
            if not passed
        ]
        parsed["errors"].append(
            "n8n release approval gates failed: "
            + ", ".join(failed_approval)
        )
    import_run_id = "n8n-" + str(uuid.uuid4())[:8]
    source_sha256 = parsed["source_sha256"]
    now = datetime.now(timezone.utc).isoformat()
    if audit:
        with _db() as conn:
            conn.execute(
                "INSERT INTO skillops_corpus_import_runs "
                "(run_id, parent_run_id, corpus_type, corpus_version, "
                "source_file_id, source_sha256, dry_run, overwrite_existing, "
                "include_provisional, status, details_json, started_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    import_run_id,
                    parent_run_id,
                    N8N_CORPUS_TYPE,
                    parsed["corpus_version"],
                    source_file_id,
                    source_sha256,
                    1 if dry_run else 0,
                    1 if overwrite_existing else 0,
                    1 if include_provisional else 0,
                    "running",
                    "{}",
                    now,
                ),
            )

    created = updated = unchanged = skipped_existing = skipped_provisional = 0
    retired = 0
    imports = []
    if not parsed["errors"]:
        with _db() as conn:
            collisions = [
                record["item_id"]
                for record in parsed["records"]
                if (
                    (row := conn.execute(
                        "SELECT corpus_type FROM skillops_coding_capabilities "
                        "WHERE item_id=?",
                        (record["item_id"],),
                    ).fetchone())
                    and row[0] != N8N_CORPUS_TYPE
                )
            ]
            if collisions:
                parsed["errors"].append(
                    "n8n capability IDs collide with another corpus: "
                    + ", ".join(collisions[:5])
                )
            changed_existing = []
            if not overwrite_existing and not collisions:
                for record in parsed["records"]:
                    existing_row = conn.execute(
                        "SELECT record_sha256 "
                        "FROM skillops_coding_capabilities "
                        "WHERE item_id=? AND corpus_type=?",
                        (record["item_id"], N8N_CORPUS_TYPE),
                    ).fetchone()
                    if (
                        existing_row
                        and existing_row[0] != record["record_sha256"]
                    ):
                        changed_existing.append(record["item_id"])
                if changed_existing:
                    parsed["errors"].append(
                        "authoritative n8n import requires overwrite_existing "
                        "for changed records: "
                        + ", ".join(changed_existing[:5])
                    )
            for record in (
                parsed["records"]
                if not collisions and not changed_existing
                else []
            ):
                if record["provisional"] and not include_provisional:
                    skipped_provisional += 1
                    imports.append({
                        "item_id": record["item_id"],
                        "action": "skipped_provisional",
                    })
                    continue
                existing = conn.execute(
                    "SELECT record_sha256, version, active "
                    "FROM skillops_coding_capabilities "
                    "WHERE item_id=? AND corpus_type=?",
                    (record["item_id"], N8N_CORPUS_TYPE),
                ).fetchone()
                if existing and existing[0] == record["record_sha256"]:
                    action = "unchanged"
                    version = existing[1]
                    unchanged += 1
                    if not dry_run and not existing[2]:
                        conn.execute(
                            "UPDATE skillops_coding_capabilities SET "
                            "active=1, retired_at=NULL, updated_at=? "
                            "WHERE item_id=? AND corpus_type=?",
                            (now, record["item_id"], N8N_CORPUS_TYPE),
                        )
                elif existing and not overwrite_existing:
                    action = "skipped_existing"
                    version = existing[1]
                    skipped_existing += 1
                elif dry_run:
                    action = "would_update" if existing else "would_create"
                    version = existing[1] + 1 if existing else 1
                    if existing:
                        updated += 1
                    else:
                        created += 1
                else:
                    values = (
                        record["canonical_title"],
                        record["tool_family"],
                        record["surface"],
                        record["version_scope"],
                        record["corpus_status"],
                        1 if record["requires_current_docs_check"] else 0,
                        record["source_page_url"],
                        record["source_record_id"],
                        record["source_content_sha256"],
                        record["record_sha256"],
                        record["what_it_teaches"],
                        json.dumps(record["task_types"]),
                        json.dumps(record["workflow"]),
                        json.dumps(record["safety_controls"]),
                        json.dumps(record["authoritative_sources"]),
                        json.dumps(record["metadata"], sort_keys=True),
                        source_file_id,
                        parent_run_id or import_run_id,
                        json.dumps(record["taxonomy"]),
                        json.dumps(record["task_types"]),
                        record["output_contract"],
                        json.dumps(record["cloud_self_hosted"]),
                        json.dumps(record["version_constraints"]),
                        json.dumps(record["validation"]),
                        json.dumps(record["failure_modes"]),
                        json.dumps(record["freshness"]),
                        record["approval_policy"],
                        now,
                    )
                    if existing:
                        conn.execute(
                            "UPDATE skillops_coding_capabilities SET "
                            "canonical_title=?, tool_family=?, surface=?, "
                            "version_scope=?, corpus_status=?, "
                            "requires_current_docs_check=?, source_page_url=?, "
                            "source_record_id=?, source_content_sha256=?, "
                            "record_sha256=?, version=version+1, "
                            "what_it_teaches=?, appropriate_tasks_json=?, "
                            "workflow_json=?, safety_controls_json=?, "
                            "authoritative_sources_json=?, metadata_json=?, "
                            "source_file_id=?, source_run_id=?, taxonomy_json=?, "
                            "task_types_json=?, output_contract=?, "
                            "cloud_self_hosted_json=?, version_constraints_json=?, "
                            "validation_json=?, failure_modes_json=?, "
                            "freshness_json=?, approval_policy=?, active=1, "
                            "retired_at=NULL, updated_at=? "
                            "WHERE item_id=? AND corpus_type=?",
                            values + (record["item_id"], N8N_CORPUS_TYPE),
                        )
                        action = "updated"
                        version = existing[1] + 1
                        updated += 1
                    else:
                        conn.execute(
                            "INSERT INTO skillops_coding_capabilities "
                            "(corpus_type, canonical_title, tool_family, surface, "
                            "version_scope, corpus_status, "
                            "requires_current_docs_check, source_page_url, "
                            "source_record_id, source_content_sha256, "
                            "record_sha256, what_it_teaches, "
                            "appropriate_tasks_json, workflow_json, "
                            "safety_controls_json, authoritative_sources_json, "
                            "metadata_json, source_file_id, source_run_id, "
                            "taxonomy_json, task_types_json, output_contract, "
                            "cloud_self_hosted_json, version_constraints_json, "
                            "validation_json, failure_modes_json, freshness_json, "
                            "approval_policy, created_at, updated_at, item_id) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                            "?,?,?,?,?,?,?,?,?)",
                            (
                                N8N_CORPUS_TYPE,
                                *values[:-1],
                                now,
                                values[-1],
                                record["item_id"],
                            ),
                        )
                        action = "created"
                        version = 1
                        created += 1
                imports.append({
                    "item_id": record["item_id"],
                    "canonical_title": record["canonical_title"],
                    "version": version,
                    "action": action,
                })
            if not dry_run and not collisions and overwrite_existing:
                active_ids = [
                    record["item_id"]
                    for record in parsed["records"]
                    if include_provisional or not record["provisional"]
                ]
                exclusion = ""
                parameters = [now, now, N8N_CORPUS_TYPE]
                if active_ids:
                    placeholders = ",".join("?" for _ in active_ids)
                    exclusion = f" AND item_id NOT IN ({placeholders})"
                    parameters.extend(active_ids)
                cursor = conn.execute(
                    "UPDATE skillops_coding_capabilities SET active=0, "
                    "retired_at=?, updated_at=? WHERE corpus_type=? AND active=1 "
                    + exclusion,
                    tuple(parameters),
                )
                retired = cursor.rowcount

    status = "invalid" if parsed["errors"] else (
        "dry_run_complete" if dry_run else "imported"
    )
    evaluation_id = None
    if status == "imported":
        evaluation_id = _record_n8n_evaluation(parsed, source_file_id)
    details = {
        "imports": imports,
        "errors": parsed["errors"],
        "evaluation": parsed["evaluation"],
        "evaluation_id": evaluation_id,
        "capabilities_retired": retired,
    }
    if audit:
        with _db() as conn:
            conn.execute(
                "UPDATE skillops_corpus_import_runs SET "
                "items_total=?, records_created=?, records_updated=?, "
                "records_unchanged=?, records_skipped_existing=?, "
                "records_skipped_provisional=?, items_skipped_invalid=?, "
                "status=?, error=?, details_json=?, finished_at=? WHERE run_id=?",
                (
                    parsed["items_total"],
                    created,
                    updated,
                    unchanged,
                    skipped_existing,
                    skipped_provisional,
                    parsed["items_skipped_invalid"],
                    status,
                    "; ".join(parsed["errors"][:3]) or None,
                    json.dumps(details, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                    import_run_id,
                ),
            )
    return {
        "status": status,
        "run_id": import_run_id,
        "corpus_type": N8N_CORPUS_TYPE,
        "corpus_version": parsed["corpus_version"],
        "items_total": parsed["items_total"],
        "capabilities_created": created,
        "capabilities_updated": updated,
        "capabilities_unchanged": unchanged,
        "capabilities_skipped_existing": skipped_existing,
        "capabilities_retired": retired,
        "items_skipped_provisional": skipped_provisional,
        "items_skipped_invalid": parsed["items_skipped_invalid"],
        "evaluation": parsed["evaluation"],
        "evaluation_id": evaluation_id,
        "imports": imports,
        "errors": parsed["errors"],
    }


def _import_vector_content(text: str, *, overwrite_existing: bool,
                           include_provisional: bool, dry_run: bool,
                           source_file_id: str = "",
                           source_label: str = "",
                           parent_run_id: str = "",
                           audit: bool = True) -> dict:
    framing = _validate_item_corpus_framing(text)
    if framing["errors"]:
        return _invalid_vector_corpus_result(
            "invalid_item_corpus",
            framing,
        )
    if _looks_like_n8n_capability_corpus(text):
        return import_n8n_capability_corpus_text(
            text,
            overwrite_existing=overwrite_existing,
            include_provisional=include_provisional,
            dry_run=dry_run,
            source_file_id=source_file_id,
            parent_run_id=parent_run_id,
            audit=audit,
        )
    if _looks_like_coding_capability_corpus(text):
        return import_coding_capability_corpus_text(
            text,
            overwrite_existing=overwrite_existing,
            include_provisional=include_provisional,
            dry_run=dry_run,
            source_file_id=source_file_id,
            parent_run_id=parent_run_id,
            audit=audit,
        )
    if _looks_like_codeops_guidance(text):
        import codeops

        try:
            result = codeops.import_codeops_guidance(
                text,
                source_label=source_label or source_file_id or "vector_store",
                historical_context_acknowledged=True,
                dry_run=dry_run,
                include_provisional=include_provisional,
            )
        except ValueError as exc:
            framing["errors"] = [str(exc)]
            return _invalid_vector_corpus_result(
                "codeops_guidance",
                framing,
            )
        return {
            **result,
            "corpus_type": "codeops_guidance",
            "items_total": int(
                result.get("items_total", result.get("record_count", 0))
            ),
            "items_skipped_provisional": int(
                result.get("items_skipped_provisional", 0)
            ),
            "items_skipped_invalid": 0,
            "errors": [],
        }
    import docops
    result = docops.import_doc_templates_from_knowledge_pack_text(
        text,
        overwrite_existing=overwrite_existing,
        include_provisional=include_provisional,
        dry_run=dry_run,
    )
    result.setdefault("corpus_type", "docops_templates")
    return result


def list_coding_capabilities(query: str = "", tool_family: str = "",
                             limit: int = 50) -> dict:
    """List structured coding capabilities learned from training corpora."""
    query = str(query or "").strip()
    tool_family = str(tool_family or "").strip()
    limit = max(1, min(int(limit or 50), 100))
    like = f"%{query}%"
    family_like = f"%{tool_family}%"
    with _db() as conn:
        rows = conn.execute(
            "SELECT item_id, canonical_title, tool_family, surface, "
            "version_scope, corpus_status, requires_current_docs_check, "
            "source_page_url, version, what_it_teaches, "
            "appropriate_tasks_json, workflow_json, safety_controls_json, "
            "authoritative_sources_json, updated_at "
            "FROM skillops_coding_capabilities "
            "WHERE corpus_type='ai_coding_capabilities' AND active=1 "
            "AND (?='' OR canonical_title LIKE ? OR item_id LIKE ? "
            "OR what_it_teaches LIKE ? OR surface LIKE ?) "
            "AND (?='' OR tool_family LIKE ?) "
            "ORDER BY tool_family, canonical_title LIMIT ?",
            (
                query, like, like, like, like,
                tool_family, family_like, limit,
            ),
        ).fetchall()
        audit_rows = conn.execute(
            "SELECT run_id, corpus_version, source_file_id, dry_run, "
            "include_provisional, items_total, records_created, "
            "records_updated, records_unchanged, "
            "records_skipped_provisional, items_skipped_invalid, status, "
            "started_at, finished_at "
            "FROM skillops_corpus_import_runs "
            "WHERE corpus_type='ai_coding_capabilities' "
            "ORDER BY started_at DESC LIMIT 10"
        ).fetchall()
    capabilities = [
        {
            "item_id": row[0],
            "canonical_title": row[1],
            "tool_family": row[2],
            "surface": row[3],
            "version_scope": row[4],
            "corpus_status": row[5],
            "requires_current_docs_check": bool(row[6]),
            "source_page_url": row[7],
            "version": row[8],
            "what_it_teaches": row[9],
            "appropriate_tasks": json.loads(row[10]),
            "workflow": json.loads(row[11]),
            "safety_controls": json.loads(row[12]),
            "authoritative_sources": json.loads(row[13]),
            "updated_at": row[14],
        }
        for row in rows
    ]
    return {
        "count": len(capabilities),
        "capabilities": capabilities,
        "import_audit": [
            {
                "run_id": row[0],
                "corpus_version": row[1],
                "source_file_id": row[2],
                "dry_run": bool(row[3]),
                "include_provisional": bool(row[4]),
                "items_total": row[5],
                "records_created": row[6],
                "records_updated": row[7],
                "records_unchanged": row[8],
                "records_skipped_provisional": row[9],
                "items_skipped_invalid": row[10],
                "status": row[11],
                "started_at": row[12],
                "finished_at": row[13],
            }
            for row in audit_rows
        ],
    }


def list_n8n_capabilities(
        query: str = "",
        taxonomy: str = "",
        task_type: str = "",
        limit: int = 50) -> dict:
    """List governed n8n capabilities without activating unsafe execution."""
    query = str(query or "").strip()
    taxonomy = str(taxonomy or "").strip()
    task_type = str(task_type or "").strip()
    limit = max(1, min(int(limit or 50), 100))
    like = f"%{query}%"
    taxonomy_like = f"%{taxonomy}%"
    task_like = f"%{task_type}%"
    with _db() as conn:
        rows = conn.execute(
            "SELECT item_id, canonical_title, surface, version_scope, "
            "corpus_status, requires_current_docs_check, source_page_url, "
            "source_record_id, source_content_sha256, version, "
            "what_it_teaches, taxonomy_json, task_types_json, workflow_json, "
            "output_contract, safety_controls_json, cloud_self_hosted_json, "
            "version_constraints_json, validation_json, failure_modes_json, "
            "authoritative_sources_json, freshness_json, approval_policy, "
            "updated_at FROM skillops_coding_capabilities "
            "WHERE corpus_type=? AND active=1 "
            "AND (?='' OR canonical_title LIKE ? OR item_id LIKE ? "
            "OR what_it_teaches LIKE ? OR output_contract LIKE ?) "
            "AND (?='' OR taxonomy_json LIKE ?) "
            "AND (?='' OR task_types_json LIKE ?) "
            "ORDER BY canonical_title LIMIT ?",
            (
                N8N_CORPUS_TYPE,
                query, like, like, like, like,
                taxonomy, taxonomy_like,
                task_type, task_like,
                limit,
            ),
        ).fetchall()
    capabilities = [
        {
            "item_id": row[0],
            "canonical_title": row[1],
            "surface": row[2],
            "version_scope": row[3],
            "corpus_status": row[4],
            "requires_current_docs_check": bool(row[5]),
            "source_page_url": row[6],
            "source_record_id": row[7],
            "source_content_sha256": row[8],
            "version": row[9],
            "what_it_teaches": row[10],
            "taxonomy": json.loads(row[11] or "[]"),
            "task_types": json.loads(row[12] or "[]"),
            "workflow": json.loads(row[13] or "[]"),
            "output_contract": row[14],
            "safety_controls": json.loads(row[15] or "[]"),
            "cloud_self_hosted_differences": json.loads(row[16] or "[]"),
            "version_constraints": json.loads(row[17] or "[]"),
            "validation": json.loads(row[18] or "[]"),
            "failure_modes": json.loads(row[19] or "[]"),
            "authoritative_sources": json.loads(row[20] or "[]"),
            "freshness": json.loads(row[21] or "[]"),
            "approval_policy": row[22],
            "updated_at": row[23],
        }
        for row in rows
    ]
    return {
        "count": len(capabilities),
        "capabilities": capabilities,
        "release": get_n8n_corpus_status(include_census=False),
    }


def get_n8n_corpus_status(include_census: bool = True) -> dict:
    """Return n8n census, evaluation, synchronization, and release-gate state."""
    with _db() as conn:
        capability_row = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN length(source_content_sha256)=64 THEN 1 ELSE 0 END), "
            "MAX(version) FROM skillops_coding_capabilities "
            "WHERE corpus_type=? AND active=1",
            (N8N_CORPUS_TYPE,),
        ).fetchone()
        registry_rows = conn.execute(
            "SELECT item_id, record_sha256 "
            "FROM skillops_coding_capabilities "
            "WHERE corpus_type=? AND active=1 ORDER BY item_id",
            (N8N_CORPUS_TYPE,),
        ).fetchall()
        registry_sha256 = hashlib.sha256(
            json.dumps(
                [
                    {"item_id": row[0], "record_sha256": row[1]}
                    for row in registry_rows
                ],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest() if registry_rows else ""
        evaluation_row = conn.execute(
            "SELECT evaluation_id, corpus_version, source_file_id, "
            "corpus_sha256, registry_sha256, receipt_sha256, evidence_source, "
            "capability_count, canonical_pages_total, canonical_pages_covered, "
            "inaccessible_sources_total, inaccessible_sources_dispositioned, "
            "retrieval_cases_total, retrieval_top5_passed, "
            "security_warning_cases_total, security_warning_cases_passed, "
            "invented_node_parameters, credential_exposures, gate_passed, "
            "details_json, evaluated_at FROM skillops_n8n_evaluations "
            "ORDER BY evaluated_at DESC LIMIT 1"
        ).fetchone()
        import_row = conn.execute(
            "SELECT run_id, corpus_version, source_file_id, source_sha256, status, "
            "items_total, records_created, records_updated, records_unchanged, "
            "items_skipped_invalid, started_at, finished_at "
            "FROM skillops_corpus_import_runs "
            "WHERE corpus_type=? AND dry_run=0 "
            "ORDER BY started_at DESC LIMIT 1",
            (N8N_CORPUS_TYPE,),
        ).fetchone()
        census_rows = conn.execute(
            "SELECT disposition_status, terminal, COUNT(*) "
            "FROM skillops_n8n_source_census "
            "GROUP BY disposition_status, terminal"
        ).fetchall()
        sync_row = conn.execute(
            "SELECT run_id, vector_store_id, status, max_files, "
            "files_seen, files_processed, "
            "files_skipped_unchanged, files_skipped_unavailable, "
            "files_skipped_unsupported, files_failed, started_at, finished_at "
            "FROM skillops_learning_runs "
            "WHERE run_type='sync' AND dry_run=0 AND max_files=0 "
            "AND status!='locked' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        census_detail_rows = conn.execute(
            "SELECT source_file_id, filename, canonical_url, "
            "disposition_status, disposition_detail, terminal, "
            "content_sha256, content_chars, source_version, "
            "last_seen_run_id, last_seen_at, terminal_at "
            "FROM skillops_n8n_source_census "
            "ORDER BY last_seen_at DESC LIMIT 100"
        ).fetchall() if include_census else []
        evaluated_source_row = None
        if evaluation_row and evaluation_row[2]:
            evaluated_source_row = conn.execute(
                "SELECT disposition_status, terminal, last_seen_run_id, "
                "content_sha256 "
                "FROM skillops_n8n_source_census WHERE source_file_id=?",
                (evaluation_row[2],),
            ).fetchone()

    capability_count = int(capability_row[0] or 0)
    source_hash_count = int(capability_row[1] or 0)
    evaluation = None
    evaluation_gates = {
        "metric_consistency": False,
        "capability_count": False,
        "inventory_coverage": False,
        "inaccessible_source_disposition": False,
        "top5_retrieval": False,
        "security_warning_retrieval": False,
        "zero_invented_node_parameters": False,
        "zero_credential_exposure": False,
        "source_integrity": False,
    }
    if evaluation_row:
        evaluation = {
            "evaluation_id": evaluation_row[0],
            "corpus_version": evaluation_row[1],
            "source_file_id": evaluation_row[2],
            "corpus_sha256": evaluation_row[3],
            "registry_sha256": evaluation_row[4],
            "receipt_sha256": evaluation_row[5],
            "evidence_source": evaluation_row[6],
            "capability_count": evaluation_row[7],
            "canonical_pages_total": evaluation_row[8],
            "canonical_pages_covered": evaluation_row[9],
            "inaccessible_sources_total": evaluation_row[10],
            "inaccessible_sources_dispositioned": evaluation_row[11],
            "retrieval_cases_total": evaluation_row[12],
            "retrieval_top5_passed": evaluation_row[13],
            "security_warning_cases_total": evaluation_row[14],
            "security_warning_cases_passed": evaluation_row[15],
            "invented_node_parameters": evaluation_row[16],
            "credential_exposures": evaluation_row[17],
            "gate_passed": bool(evaluation_row[18]),
            "details": json.loads(evaluation_row[19] or "{}"),
            "evaluated_at": evaluation_row[20],
        }
        evaluation_gates.update(
            evaluation["details"].get("gates") or {}
        )
    evaluation_gates["capability_count"] = (
        N8N_MIN_CAPABILITIES
        <= capability_count
        <= N8N_MAX_CAPABILITIES
    )
    evaluation_gates["source_integrity"] = (
        capability_count > 0 and source_hash_count == capability_count
    )

    census_total = sum(int(row[2]) for row in census_rows)
    census_terminal = sum(int(row[2]) for row in census_rows if row[1])
    census_by_status = {}
    for row in census_rows:
        census_by_status[row[0]] = (
            census_by_status.get(row[0], 0) + int(row[2])
        )
    sync = (
        {
            "run_id": sync_row[0],
            "vector_store_id": sync_row[1],
            "status": sync_row[2],
            "max_files": sync_row[3],
            "files_seen": sync_row[4],
            "files_processed": sync_row[5],
            "files_skipped_unchanged": sync_row[6],
            "files_skipped_unavailable": sync_row[7],
            "files_skipped_unsupported": sync_row[8],
            "files_failed": sync_row[9],
            "started_at": sync_row[10],
            "finished_at": sync_row[11],
        }
        if sync_row else {"status": "no_recorded_sync"}
    )
    latest_import_healthy = bool(
        import_row
        and import_row[4] == "imported"
        and int(import_row[5] or 0) == capability_count
        and int(import_row[9] or 0) == 0
    )
    evaluation_matches_registry = bool(
        evaluation
        and import_row
        and int(evaluation["capability_count"] or 0) == capability_count
        and evaluation["corpus_version"] == import_row[1]
        and evaluation["source_file_id"] == import_row[2]
        and evaluation["corpus_sha256"] == import_row[3]
        and evaluation["registry_sha256"] == registry_sha256
    )
    approval = _load_n8n_release_approval()
    approval_gates = {
        "approved_corpus_digest": bool(
            evaluation
            and approval["corpus_sha256"] == evaluation["corpus_sha256"]
        ),
        "approved_corpus_version": bool(
            evaluation
            and approval["corpus_version"] == evaluation["corpus_version"]
        ),
        "approved_evaluation_receipt": bool(
            evaluation
            and approval["evaluation_receipt_sha256"]
            == evaluation["receipt_sha256"]
        ),
    }
    release_gates = {
        **evaluation_gates,
        **approval_gates,
        "latest_import_healthy": latest_import_healthy,
        "evaluation_matches_registry": evaluation_matches_registry,
        "evaluated_source_synchronized": bool(
            evaluated_source_row
            and evaluated_source_row[0] in {"synchronized", "duplicate"}
            and evaluated_source_row[1]
            and sync.get("run_id") == evaluated_source_row[2]
            and evaluation
            and evaluation["corpus_sha256"] == evaluated_source_row[3]
        ),
        "census_complete": census_total > 0 and census_terminal == census_total,
        "synchronization_terminal": (
            sync.get("status")
            in {"completed", "partial_failed", "failed"}
            and bool(sync.get("finished_at"))
        ),
        "synchronization_healthy": (
            sync.get("status") == "completed"
            and int(sync.get("files_failed") or 0) == 0
            and int(sync.get("files_seen") or 0) > 0
        ),
    }
    production_ready = all(release_gates.values())
    latest_import = (
        {
            "run_id": import_row[0],
            "corpus_version": import_row[1],
            "source_file_id": import_row[2],
            "source_sha256": import_row[3],
            "status": import_row[4],
            "items_total": import_row[5],
            "records_created": import_row[6],
            "records_updated": import_row[7],
            "records_unchanged": import_row[8],
            "items_skipped_invalid": import_row[9],
            "started_at": import_row[10],
            "finished_at": import_row[11],
        }
        if import_row else None
    )
    return {
        "status": "ready" if production_ready else "blocked",
        "production_ready": production_ready,
        "ingestion_ready": (
            all(evaluation_gates.values())
            and all(approval_gates.values())
        ),
        "capability_count": capability_count,
        "source_hash_count": source_hash_count,
        "registry_version": int(capability_row[2] or 0),
        "registry_sha256": registry_sha256,
        "latest_import": latest_import,
        "latest_evaluation": evaluation,
        "latest_sync": sync,
        "release_gates": release_gates,
        "blocked_reasons": [
            name for name, passed in release_gates.items() if not passed
        ],
        "thresholds": {
            "capability_count_min": N8N_MIN_CAPABILITIES,
            "capability_count_max": N8N_MAX_CAPABILITIES,
            "inventory_coverage_min": N8N_MIN_INVENTORY_COVERAGE,
            "top5_retrieval_min": N8N_MIN_TOP5_RETRIEVAL,
            "security_warning_retrieval_required": 1.0,
            "inaccessible_source_disposition_required": 1.0,
            "invented_node_parameters_max": 0,
            "credential_exposures_max": 0,
        },
        "census": {
            "count": census_total,
            "terminal_count": census_terminal,
            "pending_count": census_total - census_terminal,
            "by_status": census_by_status,
            "sources": [
                {
                    "source_file_id": row[0],
                    "filename": row[1],
                    "canonical_url": row[2],
                    "disposition_status": row[3],
                    "disposition_detail": row[4],
                    "terminal": bool(row[5]),
                    "content_sha256": row[6],
                    "content_chars": row[7],
                    "source_version": row[8],
                    "last_seen_run_id": row[9],
                    "last_seen_at": row[10],
                    "terminal_at": row[11],
                }
                for row in census_detail_rows
            ],
        },
    }


def learn_from_vector_store(
        dry_run: bool = False,
        max_files: int = 0,
        overwrite_existing: bool = False,
        include_provisional: bool = False,
        max_chars_per_file: int = DEFAULT_MAX_CHARS_PER_FILE) -> dict:
    """Import template specs from every file in the configured vector store."""
    run_id = "lrn-" + str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    max_files = max(0, int(max_files or 0))
    max_chars_per_file = max(
        10_000,
        min(
            int(max_chars_per_file or DEFAULT_MAX_CHARS_PER_FILE),
            MAX_MAX_CHARS_PER_FILE,
        ),
    )
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
        "files_skipped_unavailable": 0,
        "templates_created": 0,
        "templates_updated": 0,
        "guidance_records_imported": 0,
        "capabilities_created": 0,
        "capabilities_updated": 0,
        "capabilities_unchanged": 0,
        "aliases_registered": 0,
        "items_skipped_provisional": 0,
        "items_skipped_invalid": 0,
    }
    file_reports = []
    errors = []
    staging_dir = tempfile.TemporaryDirectory(prefix="pj-vector-learn-")
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
        metadata_by_id = _list_openai_file_metadata(
            api_key,
            {
                str(entry.get("file_id") or entry.get("id") or "")
                for entry in files
            },
        )

        prepared_files = []
        for entry in files:
            content_file_id = entry.get("file_id") or entry.get("id")
            if not content_file_id:
                continue
            metadata = metadata_by_id.get(str(content_file_id), {})
            filename = str(
                metadata.get("filename")
                or entry.get("filename")
                or content_file_id
            )
            cached_text = _read_cached_vector_source(
                str(content_file_id),
                metadata,
                entry,
                max_chars_per_file,
            )
            if (
                cached_text is None
                and not _file_content_is_downloadable(metadata)
            ):
                totals["files_skipped_unavailable"] += 1
                file_reports.append({
                    "file_id": content_file_id,
                    "vector_store_file_id": entry.get("id"),
                    "filename": filename,
                    "purpose": metadata.get("purpose"),
                    "status": "skipped_content_unavailable",
                })
                continue
            if cached_text is not None:
                text, truncated = cached_text, False
            else:
                text, truncated = _read_openai_file_content(
                    content_file_id,
                    api_key,
                    max_chars_per_file,
                    expected_bytes=metadata.get("bytes") or 0,
                )
            if truncated:
                raise RuntimeError(
                    f"{content_file_id}: partial file reads are not eligible "
                    "for learning"
                )
            staged_path = (
                Path(staging_dir.name)
                / f"{len(prepared_files):08d}.txt"
            )
            staged_path.write_text(text, encoding="utf-8")
            preflight = _import_vector_content(
                text,
                overwrite_existing=overwrite_existing,
                include_provisional=include_provisional,
                dry_run=True,
                source_file_id=content_file_id,
                source_label=filename,
                parent_run_id=run_id,
                audit=False,
            )
            invalid_count = int(preflight.get("items_skipped_invalid", 0))
            import_errors = preflight.get("errors", [])
            if invalid_count or import_errors:
                totals["items_skipped_invalid"] += max(
                    invalid_count,
                    1 if import_errors else 0,
                )
                raise RuntimeError(
                    f"{preflight.get('corpus_type', 'vector corpus')} import "
                    f"was incomplete: {invalid_count} invalid item(s); "
                    f"{'; '.join(str(error) for error in import_errors[:3])}"
                )
            prepared_files.append({
                "entry": entry,
                "content_file_id": content_file_id,
                "filename": filename,
                "metadata": metadata,
                "staged_path": staged_path,
                "preflight": preflight,
            })

        for prepared in prepared_files:
            entry = prepared["entry"]
            content_file_id = prepared["content_file_id"]
            filename = prepared["filename"]
            metadata = prepared["metadata"]
            text = prepared["staged_path"].read_text(encoding="utf-8")
            imported = prepared["preflight"]
            if not dry_run:
                imported = _import_vector_content(
                    text,
                    overwrite_existing=overwrite_existing,
                    include_provisional=include_provisional,
                    dry_run=False,
                    source_file_id=content_file_id,
                    source_label=filename,
                    parent_run_id=run_id,
                )
            totals["files_processed"] += 1
            totals["templates_created"] += int(imported.get("templates_created", 0))
            totals["templates_updated"] += int(imported.get("templates_updated", 0))
            totals["guidance_records_imported"] += int(
                imported.get("record_count", 0)
                if imported.get("corpus_type") == "codeops_guidance"
                else 0
            )
            totals["capabilities_created"] += int(
                imported.get("capabilities_created", 0)
            )
            totals["capabilities_updated"] += int(
                imported.get("capabilities_updated", 0)
            )
            totals["capabilities_unchanged"] += int(
                imported.get("capabilities_unchanged", 0)
            )
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
                "filename": filename,
                "purpose": metadata.get("purpose"),
                "truncated": False,
                "items_total": imported.get("items_total", 0),
                "corpus_type": imported.get("corpus_type", "docops_templates"),
                "templates_created": imported.get("templates_created", 0),
                "templates_updated": imported.get("templates_updated", 0),
                "guidance_records_imported": (
                    imported.get("record_count", 0)
                    if imported.get("corpus_type") == "codeops_guidance"
                    else 0
                ),
                "capabilities_created": imported.get("capabilities_created", 0),
                "capabilities_updated": imported.get("capabilities_updated", 0),
                "capabilities_unchanged": imported.get(
                    "capabilities_unchanged", 0
                ),
                "status": imported.get("status"),
            })

        context = (
            f"vector_store_id={vector_store_id}; dry_run={bool(dry_run)}; "
            f"files_processed={totals['files_processed']}; "
            f"templates_created={totals['templates_created']}; "
            f"templates_updated={totals['templates_updated']}; "
            f"guidance_records_imported="
            f"{totals['guidance_records_imported']}; "
            f"capabilities_created={totals['capabilities_created']}; "
            f"capabilities_updated={totals['capabilities_updated']}; "
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
                "templates_updated=?, guidance_records_imported=?, "
                "capabilities_created=?, "
                "capabilities_updated=?, capabilities_unchanged=?, "
                "aliases_registered=?, "
                "items_skipped_provisional=?, items_skipped_invalid=?, "
                "status='completed', error=NULL, details_json=?, finished_at=? "
                "WHERE run_id=?",
                (
                    totals["files_seen"],
                    totals["files_processed"],
                    totals["templates_created"],
                    totals["templates_updated"],
                    totals["guidance_records_imported"],
                    totals["capabilities_created"],
                    totals["capabilities_updated"],
                    totals["capabilities_unchanged"],
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
            "files_skipped_unavailable": totals["files_skipped_unavailable"],
            "templates_created": totals["templates_created"],
            "templates_updated": totals["templates_updated"],
            "guidance_records_imported": totals["guidance_records_imported"],
            "capabilities_created": totals["capabilities_created"],
            "capabilities_updated": totals["capabilities_updated"],
            "capabilities_unchanged": totals["capabilities_unchanged"],
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
    finally:
        staging_dir.cleanup()


@contextmanager
def _vector_sync_lock():
    _SYNC_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = _SYNC_LOCK_PATH.open("a+")
    try:
        os.chmod(_SYNC_LOCK_PATH, 0o600)
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _stable_hash(value) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sync_policy_hash(overwrite_existing: bool,
                      include_provisional: bool) -> str:
    return _stable_hash({
        "importer_revision": SYNC_IMPORTER_REVISION,
        "overwrite_existing": bool(overwrite_existing),
        "include_provisional": bool(include_provisional),
    })


def _sync_version_hash(entry: dict, metadata: dict,
                       sync_policy_hash: str,
                       cache_fingerprint: str = "") -> str:
    return _stable_hash({
        "vector_store_file_id": entry.get("id"),
        "source_file_id": entry.get("file_id"),
        "vector_store_created_at": entry.get("created_at"),
        "attributes": entry.get("attributes"),
        "source_created_at": metadata.get("created_at"),
        "filename": metadata.get("filename"),
        "bytes": metadata.get("bytes"),
        "purpose": metadata.get("purpose"),
        "cache_fingerprint": cache_fingerprint,
        "sync_policy_hash": sync_policy_hash,
    })


def _is_n8n_vector_source(entry: dict, metadata: dict, text: str = "") -> bool:
    attributes = entry.get("attributes") or {}
    corpus_type = str(attributes.get("corpus_type") or "").strip().casefold()
    filename = str(
        metadata.get("filename")
        or entry.get("filename")
        or ""
    ).casefold()
    return bool(
        corpus_type in {"n8n", N8N_CORPUS_TYPE}
        or "n8n" in filename
        or (text and _looks_like_n8n_capability_corpus(text))
    )


def _record_n8n_source_census(
        vector_store_id: str,
        source_file_id: str,
        vector_store_file_id: str,
        filename: str,
        run_id: str,
        disposition_status: str,
        *,
        terminal: bool,
        detail: str = "",
        content_sha256: str = "",
        content_chars: int = 0,
        entry: dict = None,
        metadata: dict = None,
        preserve_disposition: bool = False):
    now = datetime.now(timezone.utc).isoformat()
    entry = entry or {}
    metadata = metadata or {}
    attributes = entry.get("attributes") or {}
    canonical_url = str(
        attributes.get("canonical_url")
        or attributes.get("source_url")
        or ""
    ).strip()
    canonical_url = _safe_https_source_url(canonical_url)
    source_version = str(
        attributes.get("version")
        or metadata.get("created_at")
        or entry.get("created_at")
        or ""
    ).strip()
    safe_attributes = {
        key: attributes.get(key)
        for key in (
            "canonical_url",
            "corpus_type",
            "source_sha256",
            "source_url",
            "version",
        )
        if attributes.get(key) not in (None, "")
    }
    safe_metadata = {
        "purpose": metadata.get("purpose"),
        "bytes": metadata.get("bytes"),
        "created_at": metadata.get("created_at"),
        "attributes": safe_attributes,
    }
    with _db() as conn:
        existing = conn.execute(
            "SELECT disposition_status, disposition_detail, terminal, "
            "content_sha256, content_chars, terminal_at "
            "FROM skillops_n8n_source_census WHERE source_file_id=?",
            (source_file_id,),
        ).fetchone()
        if preserve_disposition and existing:
            disposition_status = existing[0]
            detail = existing[1] or ""
            terminal = bool(existing[2])
            content_sha256 = existing[3] or content_sha256
            content_chars = int(existing[4] or content_chars)
            terminal_at = existing[5]
        else:
            terminal_at = now if terminal else None
        conn.execute(
            "INSERT INTO skillops_n8n_source_census "
            "(source_file_id, vector_store_id, vector_store_file_id, filename, "
            "canonical_url, disposition_status, disposition_detail, terminal, "
            "content_sha256, content_chars, source_version, last_seen_run_id, "
            "metadata_json, first_seen_at, last_seen_at, terminal_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(source_file_id) DO UPDATE SET "
            "vector_store_id=excluded.vector_store_id, "
            "vector_store_file_id=excluded.vector_store_file_id, "
            "filename=excluded.filename, canonical_url=excluded.canonical_url, "
            "disposition_status=excluded.disposition_status, "
            "disposition_detail=excluded.disposition_detail, "
            "terminal=excluded.terminal, content_sha256=excluded.content_sha256, "
            "content_chars=excluded.content_chars, "
            "source_version=excluded.source_version, "
            "last_seen_run_id=excluded.last_seen_run_id, "
            "metadata_json=excluded.metadata_json, "
            "last_seen_at=excluded.last_seen_at, terminal_at=excluded.terminal_at",
            (
                source_file_id,
                vector_store_id,
                vector_store_file_id,
                filename,
                canonical_url,
                disposition_status,
                (detail or "")[:1000],
                1 if terminal else 0,
                content_sha256,
                int(content_chars or 0),
                source_version,
                run_id,
                json.dumps(safe_metadata, sort_keys=True, default=str),
                now,
                now,
                terminal_at,
            ),
        )


def _mark_unseen_n8n_sources(
        vector_store_id: str,
        seen_source_ids: set[str],
        run_id: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    parameters = [
        "absent",
        (
            "Source was not present in the latest complete vector-store "
            f"census ({run_id})."
        ),
        now,
        vector_store_id,
    ]
    exclusion = ""
    if seen_source_ids:
        ordered_ids = sorted(seen_source_ids)
        exclusion = (
            " AND source_file_id NOT IN ("
            + ",".join("?" for _ in ordered_ids)
            + ")"
        )
        parameters.extend(ordered_ids)
    with _db() as conn:
        cursor = conn.execute(
            "UPDATE skillops_n8n_source_census SET disposition_status=?, "
            "disposition_detail=?, terminal=1, terminal_at=? "
            "WHERE vector_store_id=?"
            + exclusion,
            tuple(parameters),
        )
    return cursor.rowcount


def _get_sync_state(vector_store_id: str, source_file_id: str) -> dict:
    with _db() as conn:
        row = conn.execute(
            "SELECT success_version_hash, sync_policy_hash, content_sha256, content_chars, "
            "synchronized_at, last_attempt_status, last_attempt_error, "
            "terminal_status, terminal_detail, terminal_at "
            "FROM skillops_vector_sync_files "
            "WHERE vector_store_id=? AND source_file_id=?",
            (vector_store_id, source_file_id),
        ).fetchone()
    if not row:
        return {}
    return {
        "success_version_hash": row[0],
        "sync_policy_hash": row[1],
        "content_sha256": row[2],
        "content_chars": row[3],
        "synchronized_at": row[4],
        "last_attempt_status": row[5],
        "last_attempt_error": row[6],
        "terminal_status": row[7],
        "terminal_detail": row[8],
        "terminal_at": row[9],
    }


def _record_sync_attempt(vector_store_id: str, source_file_id: str,
                         vector_store_file_id: str, filename: str,
                         run_id: str, status: str, error: str = ""):
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        conn.execute(
            "INSERT INTO skillops_vector_sync_files "
            "(vector_store_id, source_file_id, vector_store_file_id, filename, "
            "last_attempt_run_id, last_attempt_status, last_attempt_error, "
            "last_attempt_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(vector_store_id, source_file_id) DO UPDATE SET "
            "vector_store_file_id=excluded.vector_store_file_id, "
            "filename=excluded.filename, "
            "last_attempt_run_id=excluded.last_attempt_run_id, "
            "last_attempt_status=excluded.last_attempt_status, "
            "last_attempt_error=excluded.last_attempt_error, "
            "last_attempt_at=excluded.last_attempt_at",
            (
                vector_store_id,
                source_file_id,
                vector_store_file_id,
                filename,
                run_id,
                status,
                (error or "")[:1000] or None,
                now,
            ),
        )

        if status != "skipped_unchanged":
            conn.execute(
                "UPDATE skillops_vector_sync_files SET terminal_status=?, "
                "terminal_detail=?, terminal_at=? "
                "WHERE vector_store_id=? AND source_file_id=?",
                (
                    status,
                    (error or "")[:1000] or None,
                    now,
                    vector_store_id,
                    source_file_id,
                ),
            )


def _record_sync_success(vector_store_id: str, source_file_id: str,
                         vector_store_file_id: str, filename: str,
                         version_hash: str, sync_policy_hash: str,
                         content_sha256: str,
                         content_chars: int, run_id: str, status: str):
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        conn.execute(
            "INSERT INTO skillops_vector_sync_files "
            "(vector_store_id, source_file_id, vector_store_file_id, filename, "
            "success_version_hash, sync_policy_hash, content_sha256, "
            "content_chars, synchronized_at, "
            "last_attempt_run_id, last_attempt_status, last_attempt_error, "
            "last_attempt_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?) "
            "ON CONFLICT(vector_store_id, source_file_id) DO UPDATE SET "
            "vector_store_file_id=excluded.vector_store_file_id, "
            "filename=excluded.filename, "
            "success_version_hash=excluded.success_version_hash, "
            "sync_policy_hash=excluded.sync_policy_hash, "
            "content_sha256=excluded.content_sha256, "
            "content_chars=excluded.content_chars, "
            "synchronized_at=excluded.synchronized_at, "
            "last_attempt_run_id=excluded.last_attempt_run_id, "
            "last_attempt_status=excluded.last_attempt_status, "
            "last_attempt_error=NULL, last_attempt_at=excluded.last_attempt_at",
            (
                vector_store_id,
                source_file_id,
                vector_store_file_id,
                filename,
                version_hash,
                sync_policy_hash,
                content_sha256,
                content_chars,
                now,
                run_id,
                status,
                now,
            ),
        )
        conn.execute(
            "UPDATE skillops_vector_sync_files SET terminal_status=?, "
            "terminal_detail=NULL, terminal_at=? "
            "WHERE vector_store_id=? AND source_file_id=?",
            (status, now, vector_store_id, source_file_id),
        )


def _record_sync_handled(vector_store_id: str, source_file_id: str,
                         vector_store_file_id: str, filename: str,
                         version_hash: str, sync_policy_hash: str,
                         run_id: str, status: str, detail: str = ""):
    """Persist a version that was safely classified without importing content."""
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        conn.execute(
            "INSERT INTO skillops_vector_sync_files "
            "(vector_store_id, source_file_id, vector_store_file_id, filename, "
            "success_version_hash, sync_policy_hash, content_sha256, "
            "content_chars, synchronized_at, "
            "last_attempt_run_id, last_attempt_status, last_attempt_error, "
            "last_attempt_at) VALUES (?,?,?,?,?,?,NULL,0,NULL,?,?,?,?) "
            "ON CONFLICT(vector_store_id, source_file_id) DO UPDATE SET "
            "vector_store_file_id=excluded.vector_store_file_id, "
            "filename=excluded.filename, "
            "success_version_hash=excluded.success_version_hash, "
            "sync_policy_hash=excluded.sync_policy_hash, "
            "content_sha256=NULL, content_chars=0, synchronized_at=NULL, "
            "last_attempt_run_id=excluded.last_attempt_run_id, "
            "last_attempt_status=excluded.last_attempt_status, "
            "last_attempt_error=excluded.last_attempt_error, "
            "last_attempt_at=excluded.last_attempt_at",
            (
                vector_store_id,
                source_file_id,
                vector_store_file_id,
                filename,
                version_hash,
                sync_policy_hash,
                run_id,
                status,
                (detail or "")[:1000] or None,
                now,
            ),
        )
        conn.execute(
            "UPDATE skillops_vector_sync_files SET terminal_status=?, "
            "terminal_detail=?, terminal_at=? "
            "WHERE vector_store_id=? AND source_file_id=?",
            (
                status,
                (detail or "")[:1000] or None,
                now,
                vector_store_id,
                source_file_id,
            ),
        )


def _content_was_synchronized(vector_store_id: str,
                              content_sha256: str,
                              sync_policy_hash: str) -> bool:
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM skillops_vector_sync_files "
            "WHERE vector_store_id=? AND content_sha256=? "
            "AND sync_policy_hash=? "
            "AND success_version_hash IS NOT NULL LIMIT 1",
            (vector_store_id, content_sha256, sync_policy_hash),
        ).fetchone()
    return bool(row)


def _start_sync_run(run_id: str, vector_store_id: str, *,
                    dry_run: bool, overwrite_existing: bool,
                    include_provisional: bool, force: bool,
                    max_files: int, max_chars_per_file: int,
                    status: str = "running"):
    with _db() as conn:
        conn.execute(
            "INSERT INTO skillops_learning_runs "
            "(run_id, vector_store_id, dry_run, overwrite_existing, "
            "include_provisional, force, max_files, max_chars_per_file, "
            "run_type, status, details_json, started_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                vector_store_id,
                1 if dry_run else 0,
                1 if overwrite_existing else 0,
                1 if include_provisional else 0,
                1 if force else 0,
                max_files,
                max_chars_per_file,
                "sync",
                status,
                "{}",
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def _finish_sync_run(run_id: str, status: str, totals: dict,
                     details: dict, error: str = ""):
    with _db() as conn:
        conn.execute(
            "UPDATE skillops_learning_runs SET "
            "files_seen=?, files_processed=?, files_skipped_unchanged=?, "
            "files_skipped_unavailable=?, files_skipped_unsupported=?, "
            "files_failed=?, templates_created=?, templates_updated=?, "
            "guidance_records_imported=?, "
            "capabilities_created=?, capabilities_updated=?, "
            "capabilities_unchanged=?, aliases_registered=?, "
            "items_skipped_provisional=?, "
            "items_skipped_invalid=?, status=?, error=?, details_json=?, "
            "finished_at=? WHERE run_id=?",
            (
                totals["files_seen"],
                totals["files_processed"],
                totals["files_skipped_unchanged"],
                totals["files_skipped_unavailable"],
                totals["files_skipped_unsupported"],
                totals["files_failed"],
                totals["templates_created"],
                totals["templates_updated"],
                totals["guidance_records_imported"],
                totals["capabilities_created"],
                totals["capabilities_updated"],
                totals["capabilities_unchanged"],
                totals["aliases_registered"],
                totals["items_skipped_provisional"],
                totals["items_skipped_invalid"],
                status,
                (error or "")[:1000] or None,
                json.dumps(details),
                datetime.now(timezone.utc).isoformat(),
                run_id,
            ),
        )


def _bounded_sync_details(totals: dict, reports: list, errors: list) -> dict:
    actionable = [
        report for report in reports
        if report.get("status") != "skipped_unchanged"
    ]
    unchanged = [
        report for report in reports
        if report.get("status") == "skipped_unchanged"
    ]
    retained = actionable[:MAX_SYNC_REPORT_DETAILS]
    retained.extend(
        unchanged[:max(0, MAX_SYNC_REPORT_DETAILS - len(retained))]
    )
    return {
        "totals": totals,
        "files": retained,
        "file_reports_total": len(reports),
        "file_reports_omitted": max(0, len(reports) - len(retained)),
        "errors": errors[:100],
    }


def sync_vector_store(
        dry_run: bool = False,
        max_files: int = 0,
        overwrite_existing: bool = True,
        include_provisional: bool = True,
        max_chars_per_file: int = DEFAULT_MAX_CHARS_PER_FILE,
        force: bool = False) -> dict:
    """Synchronize vector files into governed DocOps and coding registries."""
    max_files = max(0, int(max_files or 0))
    max_chars_per_file = max(
        10_000,
        min(
            int(max_chars_per_file or DEFAULT_MAX_CHARS_PER_FILE),
            MAX_MAX_CHARS_PER_FILE,
        ),
    )
    run_id = "syn-" + str(uuid.uuid4())[:8]
    totals = {
        "files_seen": 0,
        "files_processed": 0,
        "files_skipped_unchanged": 0,
        "files_skipped_unavailable": 0,
        "files_skipped_unsupported": 0,
        "files_failed": 0,
        "templates_created": 0,
        "templates_updated": 0,
        "guidance_records_imported": 0,
        "capabilities_created": 0,
        "capabilities_updated": 0,
        "capabilities_unchanged": 0,
        "aliases_registered": 0,
        "items_skipped_provisional": 0,
        "items_skipped_invalid": 0,
    }
    reports = []
    errors = []
    n8n_seen_source_ids = set()

    with _vector_sync_lock() as acquired:
        if not acquired:
            vector_store_id = ""
            try:
                vector_store_id = _require_vector_store_id()
            except ValueError:
                vector_store_id = "unknown"
            _start_sync_run(
                run_id,
                vector_store_id,
                dry_run=dry_run,
                overwrite_existing=overwrite_existing,
                include_provisional=include_provisional,
                force=force,
                max_files=max_files,
                max_chars_per_file=max_chars_per_file,
                status="locked",
            )
            _finish_sync_run(
                run_id,
                "locked",
                totals,
                {"totals": totals, "files": [], "errors": [
                    "another vector-store synchronization is already running"
                ]},
                "another vector-store synchronization is already running",
            )
            return {
                "status": "locked",
                "run_id": run_id,
                "error": "another vector-store synchronization is already running",
            }

        vector_store_id = "unknown"
        run_started = False
        try:
            vector_store_id = _require_vector_store_id()
            _start_sync_run(
                run_id,
                vector_store_id,
                dry_run=dry_run,
                overwrite_existing=overwrite_existing,
                include_provisional=include_provisional,
                force=force,
                max_files=max_files,
                max_chars_per_file=max_chars_per_file,
            )
            run_started = True
            api_key = _require_openai_api_key()
            files = _list_vector_store_files(vector_store_id, api_key, max_files)
            totals["files_seen"] = len(files)
            metadata_by_id = _list_openai_file_metadata(
                api_key,
                {
                    str(entry.get("file_id") or entry.get("id") or "")
                    for entry in files
                },
            )
            sync_policy_hash = _sync_policy_hash(
                overwrite_existing,
                include_provisional,
            )

            for entry in files:
                source_file_id = str(entry.get("file_id") or entry.get("id") or "")
                vector_store_file_id = str(entry.get("id") or "")
                filename = str(entry.get("filename") or source_file_id)
                metadata = {}
                text = ""
                content_sha256 = ""
                n8n_source = False
                census_failure_status = "failed"
                report = {
                    "file_id": source_file_id or None,
                    "vector_store_file_id": vector_store_file_id or None,
                    "filename": filename,
                }
                if source_file_id and _is_n8n_vector_source(entry, {}):
                    # The vector-store listing alone (no metadata fetch
                    # required) already identifies this as an n8n source.
                    # Mark it seen now so a later transient metadata-fetch
                    # failure for this same file cannot cause
                    # _mark_unseen_n8n_sources to misclassify a
                    # still-present source as absent.
                    n8n_seen_source_ids.add(source_file_id)
                try:
                    if not source_file_id:
                        raise ValueError("vector-store entry has no file identity")
                    metadata = metadata_by_id.get(source_file_id) or (
                        _get_openai_file_metadata(source_file_id, api_key)
                    )
                    filename = str(metadata.get("filename") or filename)
                    report["filename"] = filename
                    report["purpose"] = metadata.get("purpose")
                    n8n_source = _is_n8n_vector_source(entry, metadata)
                    if n8n_source:
                        n8n_seen_source_ids.add(source_file_id)
                    cache_fingerprint = _cached_vector_source_fingerprint(
                        source_file_id
                    )
                    version_hash = _sync_version_hash(
                        entry,
                        metadata,
                        sync_policy_hash,
                        cache_fingerprint,
                    )
                    state = _get_sync_state(vector_store_id, source_file_id)
                    if (
                        not force
                        and state.get("success_version_hash") == version_hash
                    ):
                        totals["files_skipped_unchanged"] += 1
                        report["status"] = "skipped_unchanged"
                        if not dry_run:
                            _record_sync_attempt(
                                vector_store_id,
                                source_file_id,
                                vector_store_file_id,
                                filename,
                                run_id,
                                "skipped_unchanged",
                            )
                            if n8n_source:
                                _record_n8n_source_census(
                                    vector_store_id,
                                    source_file_id,
                                    vector_store_file_id,
                                    filename,
                                    run_id,
                                    "skipped_unchanged",
                                    terminal=False,
                                    entry=entry,
                                    metadata=metadata,
                                    preserve_disposition=True,
                                )
                        reports.append(report)
                        continue

                    if n8n_source and not dry_run:
                        _record_n8n_source_census(
                            vector_store_id,
                            source_file_id,
                            vector_store_file_id,
                            filename,
                            run_id,
                            "pending",
                            terminal=False,
                            entry=entry,
                            metadata=metadata,
                        )
                    cached_text = _read_cached_vector_source(
                        source_file_id,
                        metadata,
                        entry,
                        max_chars_per_file,
                    )
                    if (
                        cached_text is None
                        and not _file_content_is_downloadable(metadata)
                    ):
                        totals["files_skipped_unavailable"] += 1
                        purpose = str(metadata.get("purpose") or "unknown")
                        report.update({
                            "status": "skipped_content_unavailable",
                            "reason": (
                                f"OpenAI does not expose content downloads for "
                                f"purpose={purpose}"
                            ),
                        })
                        if not dry_run:
                            _record_sync_handled(
                                vector_store_id,
                                source_file_id,
                                vector_store_file_id,
                                filename,
                                version_hash,
                                sync_policy_hash,
                                run_id,
                                "skipped_content_unavailable",
                                report["reason"],
                            )
                            if n8n_source:
                                _record_n8n_source_census(
                                    vector_store_id,
                                    source_file_id,
                                    vector_store_file_id,
                                    filename,
                                    run_id,
                                    "unavailable",
                                    terminal=True,
                                    detail=report["reason"],
                                    entry=entry,
                                    metadata=metadata,
                                )
                        reports.append(report)
                        continue

                    if cached_text is not None:
                        text, truncated = cached_text, False
                        report["content_source"] = "verified_local_cache"
                    else:
                        text, truncated = _read_openai_file_content(
                            source_file_id,
                            api_key,
                            max_chars_per_file,
                            expected_bytes=metadata.get("bytes") or 0,
                        )
                        report["content_source"] = "openai_files_api"
                    if truncated:
                        raise RuntimeError(
                            "partial file reads are not eligible for synchronization"
                        )
                    content_sha256 = hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest()
                    n8n_source = _is_n8n_vector_source(entry, metadata, text)
                    if n8n_source:
                        n8n_seen_source_ids.add(source_file_id)
                    if n8n_source and not dry_run:
                        _record_n8n_source_census(
                            vector_store_id,
                            source_file_id,
                            vector_store_file_id,
                            filename,
                            run_id,
                            "pending",
                            terminal=False,
                            content_sha256=content_sha256,
                            content_chars=len(text),
                            entry=entry,
                            metadata=metadata,
                        )
                    if (
                        not force
                        and _content_was_synchronized(
                            vector_store_id,
                            content_sha256,
                            sync_policy_hash,
                        )
                    ):
                        totals["files_skipped_unchanged"] += 1
                        report["status"] = "skipped_duplicate_content"
                        report["content_sha256"] = content_sha256
                        if not dry_run:
                            _record_sync_success(
                                vector_store_id,
                                source_file_id,
                                vector_store_file_id,
                                filename,
                                version_hash,
                                sync_policy_hash,
                                content_sha256,
                                len(text),
                                run_id,
                                "skipped_duplicate_content",
                            )
                            if n8n_source:
                                _record_n8n_source_census(
                                    vector_store_id,
                                    source_file_id,
                                    vector_store_file_id,
                                    filename,
                                    run_id,
                                    "duplicate",
                                    terminal=True,
                                    detail=(
                                        "Identical verified content was already "
                                        "synchronized."
                                    ),
                                    content_sha256=content_sha256,
                                    content_chars=len(text),
                                    entry=entry,
                                    metadata=metadata,
                                )
                        reports.append(report)
                        continue

                    preflight = _import_vector_content(
                        text,
                        overwrite_existing=overwrite_existing,
                        include_provisional=include_provisional,
                        dry_run=True,
                        source_file_id=source_file_id,
                        source_label=filename,
                        parent_run_id=run_id,
                        audit=False,
                    )
                    content_type = preflight.get(
                        "corpus_type", "docops_templates"
                    )
                    if (
                        int(preflight.get("items_total", 0)) == 0
                        and not preflight.get("errors")
                    ):
                        totals["files_skipped_unsupported"] += 1
                        report.update({
                            "status": "skipped_unsupported_content",
                            "content_sha256": content_sha256,
                            "content_chars": len(text),
                            "reason": (
                                "no supported DocOps, CodeOps, coding, or n8n "
                                "capability ITEM blocks"
                            ),
                        })
                        if not dry_run:
                            _record_sync_handled(
                                vector_store_id,
                                source_file_id,
                                vector_store_file_id,
                                filename,
                                version_hash,
                                sync_policy_hash,
                                run_id,
                                "skipped_unsupported_content",
                                report["reason"],
                            )
                            if n8n_source:
                                _record_n8n_source_census(
                                    vector_store_id,
                                    source_file_id,
                                    vector_store_file_id,
                                    filename,
                                    run_id,
                                    "unsupported",
                                    terminal=True,
                                    detail=report["reason"],
                                    content_sha256=content_sha256,
                                    content_chars=len(text),
                                    entry=entry,
                                    metadata=metadata,
                                )
                        reports.append(report)
                        continue
                    imported = preflight

                    invalid_count = int(
                        imported.get("items_skipped_invalid", 0)
                    )
                    import_errors = imported.get("errors", [])
                    if invalid_count or import_errors:
                        census_failure_status = "invalid"
                        totals["items_skipped_invalid"] += max(
                            invalid_count,
                            1 if import_errors else 0,
                        )
                        raise RuntimeError(
                            f"{imported.get('corpus_type', content_type)} import "
                            "was incomplete: "
                            f"{invalid_count} invalid item(s); "
                            f"{'; '.join(import_errors[:3])}"
                        )
                    if not dry_run:
                        imported = _import_vector_content(
                            text,
                            overwrite_existing=overwrite_existing,
                            include_provisional=include_provisional,
                            dry_run=False,
                            source_file_id=source_file_id,
                            source_label=filename,
                            parent_run_id=run_id,
                        )

                    totals["files_processed"] += 1
                    if content_type == "docops_templates":
                        for key in (
                            "templates_created",
                            "templates_updated",
                            "aliases_registered",
                            "items_skipped_provisional",
                            "items_skipped_invalid",
                        ):
                            totals[key] += int(imported.get(key, 0))
                    elif content_type == "codeops_guidance":
                        totals["guidance_records_imported"] += int(
                            imported.get("record_count", 0)
                        )
                        totals["items_skipped_provisional"] += int(
                            imported.get("items_skipped_provisional", 0)
                        )
                        totals["items_skipped_invalid"] += int(
                            imported.get("items_skipped_invalid", 0)
                        )
                    else:
                        for key in (
                            "capabilities_created",
                            "capabilities_updated",
                            "capabilities_unchanged",
                            "items_skipped_provisional",
                            "items_skipped_invalid",
                        ):
                            totals[key] += int(imported.get(key, 0))
                    report.update({
                        "status": (
                            "would_synchronize" if dry_run else "synchronized"
                        ),
                        "content_type": content_type,
                        "corpus_type": imported.get(
                            "corpus_type", content_type
                        ),
                        "content_sha256": content_sha256,
                        "content_chars": len(text),
                        "items_total": imported.get(
                            "items_total", imported.get("record_count", 0)
                        ),
                        "templates_created": imported.get(
                            "templates_created", 0
                        ),
                        "templates_updated": imported.get(
                            "templates_updated", 0
                        ),
                        "guidance_records_imported": imported.get(
                            "record_count", 0
                        ),
                        "capabilities_created": imported.get(
                            "capabilities_created", 0
                        ),
                        "capabilities_updated": imported.get(
                            "capabilities_updated", 0
                        ),
                        "capabilities_unchanged": imported.get(
                            "capabilities_unchanged", 0
                        ),
                    })
                    if not dry_run:
                        _record_sync_success(
                            vector_store_id,
                            source_file_id,
                            vector_store_file_id,
                            filename,
                            version_hash,
                            sync_policy_hash,
                            content_sha256,
                            len(text),
                            run_id,
                            "synchronized",
                        )
                        if n8n_source:
                            _record_n8n_source_census(
                                vector_store_id,
                                source_file_id,
                                vector_store_file_id,
                                filename,
                                run_id,
                                (
                                    "synchronized"
                                    if content_type == N8N_CORPUS_TYPE
                                    else "classified_non_n8n"
                                ),
                                terminal=True,
                                detail=(
                                    "Governed n8n capability corpus synchronized."
                                    if content_type == N8N_CORPUS_TYPE
                                    else "Source was classified and routed to a "
                                    "different governed corpus."
                                ),
                                content_sha256=content_sha256,
                                content_chars=len(text),
                                entry=entry,
                                metadata=metadata,
                            )
                except Exception as exc:
                    error = str(exc)
                    totals["files_failed"] += 1
                    errors.append(f"{source_file_id or vector_store_file_id}: {error}")
                    report.update({"status": "failed", "error": error[:1000]})
                    if source_file_id:
                        # This file is still present in the vector store's
                        # file listing for this run; a per-file identity or
                        # metadata error makes its n8n-source disposition
                        # indeterminate, not confirmed absent. Never let a
                        # transient failure feed the absence sweep below.
                        n8n_seen_source_ids.add(source_file_id)
                    if source_file_id and not dry_run:
                        _record_sync_attempt(
                            vector_store_id,
                            source_file_id,
                            vector_store_file_id,
                            filename,
                            run_id,
                            "failed",
                            error,
                        )
                        if n8n_source:
                            _record_n8n_source_census(
                                vector_store_id,
                                source_file_id,
                                vector_store_file_id,
                                filename,
                                run_id,
                                census_failure_status,
                                terminal=True,
                                detail=error,
                                content_sha256=content_sha256,
                                content_chars=len(text),
                                entry=entry,
                                metadata=metadata,
                            )
                reports.append(report)

            if not dry_run and max_files == 0:
                _mark_unseen_n8n_sources(
                    vector_store_id,
                    n8n_seen_source_ids,
                    run_id,
                )
            if totals["files_failed"]:
                final_status = "partial_failed"
            elif dry_run:
                final_status = "dry_run_complete"
            else:
                final_status = "completed"
            details = _bounded_sync_details(totals, reports, errors)
            _finish_sync_run(
                run_id,
                final_status,
                totals,
                details,
                errors[0] if errors else "",
            )
            return {
                "status": final_status,
                "run_id": run_id,
                "vector_store_id": vector_store_id,
                "dry_run": bool(dry_run),
                "force": bool(force),
                **totals,
                "file_reports": details["files"],
                "file_reports_total": details["file_reports_total"],
                "file_reports_omitted": details["file_reports_omitted"],
                "errors": errors[:50],
            }
        except Exception as exc:
            error = str(exc)
            if not run_started:
                _start_sync_run(
                    run_id,
                    vector_store_id,
                    dry_run=dry_run,
                    overwrite_existing=overwrite_existing,
                    include_provisional=include_provisional,
                    force=force,
                    max_files=max_files,
                    max_chars_per_file=max_chars_per_file,
                )
            _finish_sync_run(
                run_id,
                "failed",
                totals,
                _bounded_sync_details(totals, reports, [error]),
                error,
            )
            return {"status": "failed", "run_id": run_id, "error": error}


def get_vector_sync_status(limit: int = 10) -> dict:
    """Return recent durable sync runs and per-file synchronization state."""
    limit = max(1, min(int(limit or 10), 50))
    with _db() as conn:
        run_rows = conn.execute(
            "SELECT run_id, vector_store_id, status, dry_run, force, "
            "files_seen, files_processed, files_skipped_unchanged, "
            "files_skipped_unavailable, files_skipped_unsupported, files_failed, "
            "templates_created, templates_updated, guidance_records_imported, "
            "capabilities_created, capabilities_updated, "
            "capabilities_unchanged, items_skipped_provisional, "
            "items_skipped_invalid, error, "
            "started_at, finished_at "
            "FROM skillops_learning_runs WHERE run_type='sync' "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        file_rows = conn.execute(
            "SELECT vector_store_id, source_file_id, vector_store_file_id, "
            "filename, content_sha256, content_chars, synchronized_at, "
            "last_attempt_status, last_attempt_error, last_attempt_at, "
            "terminal_status, terminal_detail, terminal_at "
            "FROM skillops_vector_sync_files "
            "ORDER BY COALESCE(last_attempt_at, synchronized_at) DESC LIMIT 100"
        ).fetchall()
    return {
        "runs": [
            {
                "run_id": row[0],
                "vector_store_id": row[1],
                "status": row[2],
                "dry_run": bool(row[3]),
                "force": bool(row[4]),
                "files_seen": row[5],
                "files_processed": row[6],
                "files_skipped_unchanged": row[7],
                "files_skipped_unavailable": row[8],
                "files_skipped_unsupported": row[9],
                "files_failed": row[10],
                "templates_created": row[11],
                "templates_updated": row[12],
                "guidance_records_imported": row[13],
                "capabilities_created": row[14],
                "capabilities_updated": row[15],
                "capabilities_unchanged": row[16],
                "items_skipped_provisional": row[17],
                "items_skipped_invalid": row[18],
                "error": row[19],
                "started_at": row[20],
                "finished_at": row[21],
            }
            for row in run_rows
        ],
        "files": [
            {
                "vector_store_id": row[0],
                "file_id": row[1],
                "vector_store_file_id": row[2],
                "filename": row[3],
                "content_sha256": row[4],
                "content_chars": row[5],
                "synchronized_at": row[6],
                "last_attempt_status": row[7],
                "last_attempt_error": row[8],
                "last_attempt_at": row[9],
                "terminal_status": row[10],
                "terminal_detail": row[11],
                "terminal_at": row[12],
            }
            for row in file_rows
        ],
    }


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
                     "import supported ITEM corpora into DocOps templates or "
                     "the shared coding/n8n capability registry with persistent "
                     "audit."),
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
                                "description": "Complete-file safety limit (default 5,000,000; files over the limit fail without partial import)"},
     }, "required": []}},
    {"type": "function", "name": "sync_vector_store",
     "description": ("Idempotently synchronize hash-cached vector-store "
                    "sources into DocOps templates, CodeOps guidance, coding "
                    "capabilities, or governed n8n capabilities using durable "
                    "deduplication, source census, a cross-process lock, and "
                    "run audit. Provider-hosted inputs without an approved local "
                    "cache are classified as unavailable rather than partially "
                    "imported."),
     "parameters": {"type": "object", "properties": {
        "dry_run": {"type": "boolean",
                    "description": "Report changes without mutating DocOps, CodeOps, or file sync state"},
        "max_files": {"type": "integer",
                      "description": "Optional file cap (0=all)"},
        "overwrite_existing": {"type": "boolean",
                               "description": "Update existing DocOps templates (default true)"},
        "include_provisional": {"type": "boolean",
                                "description": "Import provisional instructional specs (default true)"},
        "max_chars_per_file": {"type": "integer",
                               "description": "Complete-file safety limit (default 5,000,000)"},
        "force": {"type": "boolean",
                  "description": "Reprocess files even when identity/content is unchanged"},
     }, "required": []}},
    {"type": "function", "name": "get_vector_sync_status",
     "description": ("Inspect recent durable vector-store sync runs, failures, "
                    "and per-file synchronization state."),
     "parameters": {"type": "object", "properties": {
        "limit": {"type": "integer",
                  "description": "Number of recent runs to return (1-50)"},
     }, "required": []}},
    {"type": "function", "name": "list_coding_capabilities",
     "description": ("List structured AI coding-tool capabilities learned from "
                    "training corpora, including freshness requirements, "
                    "appropriate tasks, workflows, safety controls, sources, "
                    "and recent import audit."),
     "parameters": {"type": "object", "properties": {
        "query": {"type": "string",
                  "description": "Optional title, ID, surface, or guidance search"},
        "tool_family": {"type": "string",
                        "description": "Optional tool-family filter"},
        "limit": {"type": "integer",
                  "description": "Maximum capabilities to return (1-100)"},
     }, "required": []}},
    {"type": "function", "name": "list_n8n_capabilities",
     "description": (
        "List governed n8n capabilities from the shared domain registry, "
        "including workflows, output contracts, safety controls, deployment "
        "differences, freshness requirements, and release-gate state."
     ),
     "parameters": {"type": "object", "properties": {
        "query": {"type": "string",
                  "description": "Optional title, ID, output, or guidance search"},
        "taxonomy": {"type": "string",
                     "description": "Optional taxonomy filter"},
        "task_type": {"type": "string",
                      "description": "Optional task-type filter"},
        "limit": {"type": "integer",
                  "description": "Maximum capabilities to return (1-100)"},
     }, "required": []}},
    {"type": "function", "name": "get_n8n_corpus_status",
     "description": (
        "Inspect the governed n8n corpus inventory, source census, evaluation "
        "thresholds, synchronization health, and fail-closed production gate."
     ),
     "parameters": {"type": "object", "properties": {
        "include_census": {
            "type": "boolean",
            "description": "Include bounded per-source census details",
        },
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
    "sync_vector_store": sync_vector_store,
    "get_vector_sync_status": get_vector_sync_status,
    "list_coding_capabilities": list_coding_capabilities,
    "list_n8n_capabilities": list_n8n_capabilities,
    "get_n8n_corpus_status": get_n8n_corpus_status,
    "deprecate_skill": deprecate_skill,
}
