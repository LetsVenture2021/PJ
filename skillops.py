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

DEFAULT_MAX_CHARS_PER_FILE = 5_000_000
MAX_MAX_CHARS_PER_FILE = 25_000_000
MAX_SYNC_REPORT_DETAILS = 200
SYNC_IMPORTER_REVISION = "coding-capability-registry-v1"
DEFAULT_REQUEST_RETRIES = 4
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5
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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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
        if "sync_policy_hash" not in existing_sync_columns:
            conn.execute(
                "ALTER TABLE skillops_vector_sync_files "
                "ADD COLUMN sync_policy_hash TEXT"
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
                    "FROM skillops_coding_capabilities WHERE item_id=?",
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
                            "(canonical_title, tool_family, surface, "
                            "version_scope, corpus_status, "
                            "requires_current_docs_check, source_page_url, "
                            "source_record_id, source_content_sha256, "
                            "record_sha256, what_it_teaches, "
                            "appropriate_tasks_json, workflow_json, "
                            "safety_controls_json, authoritative_sources_json, "
                            "metadata_json, source_file_id, source_run_id, "
                            "created_at, updated_at, item_id) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
            "WHERE (?='' OR canonical_title LIKE ? OR item_id LIKE ? "
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


def _get_sync_state(vector_store_id: str, source_file_id: str) -> dict:
    with _db() as conn:
        row = conn.execute(
            "SELECT success_version_hash, sync_policy_hash, content_sha256, content_chars, "
            "synchronized_at, last_attempt_status, last_attempt_error "
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
                report = {
                    "file_id": source_file_id or None,
                    "vector_store_file_id": vector_store_file_id or None,
                    "filename": filename,
                }
                try:
                    if not source_file_id:
                        raise ValueError("vector-store entry has no file identity")
                    metadata = metadata_by_id.get(source_file_id) or (
                        _get_openai_file_metadata(source_file_id, api_key)
                    )
                    filename = str(metadata.get("filename") or filename)
                    report["filename"] = filename
                    report["purpose"] = metadata.get("purpose")
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
                        reports.append(report)
                        continue

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
                                "no supported DocOps, CodeOps, or coding "
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
                        reports.append(report)
                        continue
                    imported = preflight

                    invalid_count = int(
                        imported.get("items_skipped_invalid", 0)
                    )
                    import_errors = imported.get("errors", [])
                    if invalid_count or import_errors:
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
                except Exception as exc:
                    error = str(exc)
                    totals["files_failed"] += 1
                    errors.append(f"{source_file_id or vector_store_file_id}: {error}")
                    report.update({"status": "failed", "error": error[:1000]})
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
                reports.append(report)

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
            "last_attempt_status, last_attempt_error, last_attempt_at "
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
                     "the coding-capability registry with persistent audit."),
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
                    "sources into DocOps templates, CodeOps guidance, or coding "
                    "capabilities using durable deduplication, a cross-process "
                    "lock, and run audit. Provider-hosted inputs without an "
                    "approved local cache are classified as unavailable rather "
                    "than partially imported."),
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
    "deprecate_skill": deprecate_skill,
}
