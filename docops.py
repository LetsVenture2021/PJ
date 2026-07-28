"""
docops.py — PJ's mission-critical document engine (DocOps).

PJ is a standalone Chief of Staff. This module lets PJ automate and
streamline the creation of mission-critical documents with governance
adapted from production document-pipeline practice:

  TEMPLATES  — versioned, structured templates define required sections
               so every document of a type is complete and consistent.
  DRAFT      — PJ drafts content section-by-section; it is validated
               against the template (required sections present and
               non-empty) and written as a versioned markdown file.
  INTEGRITY  — every version gets a stable DOC id, version number,
               SHA-256 hash, and lineage (supersession chain).
  REVIEW     — documents start as 'draft' by default. finalize_document
               is an explicit gate that blocks on unresolved [TBD] /
               [VERIFY CURRENT] markers and seals the file hash.
               draft_document(finalize=True) produces a sealed FINAL in
               one pass when content is complete (same marker gate).
  PUBLISH    — export_document renders an audience-ready deliverable
               (styled HTML or DOCX/RTF via textutil) with clean
               typography and no internal metadata banner. Non-final
               versions are watermarked DRAFT; finals render clean with
               a discreet integrity footer.
  AUDIT      — registry + full version history in SQLite; files are
               never edited in place — revisions create new versions
               that supersede the old.

Files live in ~/PJ/documents/. Nothing is sent anywhere; PJ produces
artifacts, the human distributes them.
"""
import hashlib
import html as _html
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import fcntl
import presentationops

try:
    import markdown as _markdown
except ImportError:
    _markdown = None

_ROOT = Path(__file__).resolve().parent
_DB_PATH = _ROOT / "pj_data.sqlite3"
DOCS_DIR = _ROOT / "documents"
DOCS_DIR.mkdir(exist_ok=True)
EXPORTS_DIR = DOCS_DIR / "exports"
EXPORTS_DIR.mkdir(exist_ok=True)

# Markers that block finalization (unresolved facts / legal checks).
BLOCKING_MARKERS = ["[TBD", "[VERIFY CURRENT]", "{{", "TODO:"]

# Starter templates installed on first run.
_SEED_TEMPLATES = {
    "executive_brief": {
        "description": "Decision-ready executive brief",
        "sections": ["Purpose", "Situation", "Key Facts", "Options",
                     "Recommendation", "Risks", "Next Actions"],
        "optional_sections": ["Appendix"],
    },
    "sop": {
        "description": "Standard operating procedure",
        "sections": ["Objective", "Scope", "Roles", "Prerequisites",
                     "Procedure", "Exceptions", "Revision Notes"],
        "optional_sections": ["References"],
    },
    "meeting_memo": {
        "description": "Meeting memo with decisions and action items",
        "sections": ["Attendees", "Context", "Discussion", "Decisions",
                     "Action Items"],
        "optional_sections": ["Parking Lot"],
    },
    "proposal": {
        "description": "Business proposal / term outline",
        "sections": ["Summary", "Background", "Proposed Terms",
                     "Value & Rationale", "Assumptions", "Risks",
                     "Timeline", "Approval"],
        "optional_sections": ["Appendix"],
    },
    "status_report": {
        "description": "Periodic status report",
        "sections": ["Period", "Highlights", "Metrics", "Blockers",
                     "Next Period Plan"],
        "optional_sections": ["Notes"],
    },
    "slide_presentation": {
        "description": "Native presentation governed by a structured slide specification",
        "sections": ["Presentation"],
        "optional_sections": [],
    },
}


def _normalize_alias_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def _hash_file_path(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_copy(source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        with Path(source).open("rb") as source_handle, temporary.open("wb") as target:
            shutil.copyfileobj(source_handle, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        temporary.chmod(0o600)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _ensure_immutable_artifacts(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS docops_artifacts (
        artifact_id TEXT PRIMARY KEY,
        doc_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        format TEXT NOT NULL,
        filename TEXT NOT NULL,
        path TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        byte_size INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        status TEXT NOT NULL,
        audience_ready INTEGER NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_docops_artifacts_document "
        "ON docops_artifacts(doc_id, version, format, created_at)"
    )
    legacy_rows = conn.execute(
        "SELECT artifact_id, doc_id, version, format, filename, path, "
        "mime_type, byte_size, sha256, status, audience_ready, created_at "
        "FROM docops_exports WHERE artifact_id NOT IN "
        "(SELECT artifact_id FROM docops_artifacts)"
    ).fetchall()
    export_root = EXPORTS_DIR.resolve()
    for row in legacy_rows:
        source = Path(row[5])
        target = EXPORTS_DIR / ".artifacts" / row[0] / row[4]
        try:
            resolved = source.resolve(strict=True)
            resolved.relative_to(export_root)
            if (
                source.is_symlink()
                or not resolved.is_file()
                or resolved.stat().st_size != row[7]
                or _hash_file_path(resolved) != row[8]
            ):
                raise ValueError("legacy artifact integrity mismatch")
            if (
                not target.exists()
                or target.stat().st_size != row[7]
                or _hash_file_path(target) != row[8]
            ):
                _atomic_copy(resolved, target)
            migrated = target.resolve(strict=True)
            migrated.relative_to(export_root)
            if (
                target.is_symlink()
                or not migrated.is_file()
                or migrated.stat().st_size != row[7]
                or _hash_file_path(migrated) != row[8]
            ):
                raise ValueError("legacy artifact migration verification failed")
        except (OSError, ValueError):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO docops_artifacts "
            "(artifact_id, doc_id, version, format, filename, path, mime_type, "
            "byte_size, sha256, status, audience_ready, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (*row[:5], str(migrated), *row[6:]),
        )


@contextmanager
def _db():
    conn = sqlite3.connect(_DB_PATH)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS docops_templates (
            name TEXT PRIMARY KEY,
            version INTEGER DEFAULT 1,
            description TEXT DEFAULT '',
            sections TEXT NOT NULL,           -- JSON array (required)
            optional_sections TEXT DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS docops_documents (
            doc_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            title TEXT NOT NULL,
            template TEXT NOT NULL,
            template_version INTEGER NOT NULL,
            status TEXT DEFAULT 'draft',      -- draft | final | superseded
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            tags TEXT DEFAULT '',
            change_note TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            finalized_at TEXT,
            PRIMARY KEY (doc_id, version)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS docops_template_aliases (
            alias_key TEXT PRIMARY KEY,
            alias_raw TEXT NOT NULL,
            template_name TEXT NOT NULL,
            source_item_id TEXT DEFAULT '',
            canonical_title TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS docops_presentation_specs (
            doc_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            schema_version TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            spec_sha256 TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (doc_id, version),
            FOREIGN KEY (doc_id, version)
                REFERENCES docops_documents(doc_id, version)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS docops_exports (
            artifact_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            format TEXT NOT NULL,
            filename TEXT NOT NULL,
            path TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            audience_ready INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (doc_id, version, format),
            FOREIGN KEY (doc_id, version)
                REFERENCES docops_documents(doc_id, version)
        )""")
        _ensure_immutable_artifacts(conn)
        # Seed starter templates once.
        have = {r[0] for r in conn.execute(
            "SELECT name FROM docops_templates").fetchall()}
        for name, t in _SEED_TEMPLATES.items():
            if name not in have:
                conn.execute(
                    "INSERT INTO docops_templates "
                    "(name, description, sections, optional_sections) "
                    "VALUES (?,?,?,?)",
                    (name, t["description"], json.dumps(t["sections"]),
                     json.dumps(t["optional_sections"])))
        # Ensure every canonical template name resolves as an alias.
        for (name,) in conn.execute("SELECT name FROM docops_templates").fetchall():
            key = _normalize_alias_key(name)
            conn.execute(
                "INSERT INTO docops_template_aliases "
                "(alias_key, alias_raw, template_name, canonical_title) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(alias_key) DO UPDATE SET "
                "alias_raw=excluded.alias_raw, template_name=excluded.template_name, "
                "canonical_title=excluded.canonical_title, updated_at=CURRENT_TIMESTAMP",
                (key, name, name, name),
            )
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "doc"


def _dedupe_nonempty(values) -> list:
    seen = set()
    out = []
    for value in values:
        txt = str(value).strip()
        if not txt:
            continue
        key = txt.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(txt)
    return out


def _normalize_template_name(raw: str, fallback: str = "template") -> str:
    val = str(raw or "").lower()
    val = re.sub(r"[^a-z0-9]+", "_", val).strip("_")
    if not val:
        val = fallback
    if not re.match(r"[a-z]", val):
        val = f"t_{val}"
    return val[:80]


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return _dedupe_nonempty(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return _dedupe_nonempty(parsed)
            except Exception:
                pass
        for sep in ("|", ";", ","):
            if sep in text:
                return _dedupe_nonempty([p.strip() for p in text.split(sep)])
        return [text]
    return [str(value).strip()]


def _is_truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    txt = str(value or "").strip().lower()
    return txt in {"1", "true", "yes", "y", "on", "provisional", "draft"}


def _item_value(item: dict, *keys):
    for key in keys:
        if key in item:
            val = item[key]
            if val is None:
                continue
            if isinstance(val, str) and not val.strip():
                continue
            return val
    return ""


_QUOTE_TRANSLATION = str.maketrans({
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
})


def _parse_scalar(value: str):
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.translate(_QUOTE_TRANSLATION)
    if normalized[:1] in {"[", "{"}:
        try:
            return json.loads(normalized)
        except json.JSONDecodeError:
            pass
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
        try:
            return json.loads(normalized)
        except json.JSONDecodeError:
            return normalized[1:-1]
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == "'":
        return normalized[1:-1].replace("''", "'")
    lowered = normalized.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~"}:
        return None
    return normalized


def _parse_key_value_text(text: str) -> dict:
    item = {}
    current_key = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9 _-]*):(?:\s*(.*))?$", line)
        if match and not line.startswith("- "):
            key = re.sub(r"[^a-z0-9]+", "_", match.group(1).lower()).strip("_")
            value = (match.group(2) or "").strip()
            if value:
                item[key] = _parse_scalar(value)
            else:
                item[key] = []
            current_key = key
            continue
        if line.startswith("- ") and current_key:
            current_val = item.get(current_key)
            if not isinstance(current_val, list):
                current_val = _as_list(current_val)
            current_val.append(_parse_scalar(line[2:].strip()))
            item[current_key] = current_val
    return item


def _extract_markdown_field(text: str, label: str) -> str:
    match = re.search(
        rf"^\s*\*\*{re.escape(label)}:\*\*\s*(.*?)"
        rf"(?=^\s*\*\*[^*\r\n]+(?::)?\*\*|^\s*#{{1,6}}\s|\Z)",
        text or "",
        flags=re.M | re.S | re.I,
    )
    if not match:
        return ""
    return " ".join(part.strip() for part in match.group(1).splitlines()
                    if part.strip())


def _extract_ordered_markdown_list(text: str, label: str) -> list:
    section = re.search(
        rf"^\s*\*\*{re.escape(label)}(?::)?\*\*\s*$"
        rf"(.*?)(?=^\s*\*\*[^*\r\n]+(?::)?\*\*|^\s*#{{1,6}}\s|\Z)",
        text or "",
        flags=re.M | re.S | re.I,
    )
    if not section:
        return []
    return _dedupe_nonempty(
        match.group(1)
        for match in re.finditer(
            r"^\s*\d+[.)]\s+(.+?)\s*$",
            section.group(1),
            flags=re.M,
        )
    )


def _parse_knowledge_pack_block(block: str) -> dict:
    text = (block or "").strip()
    if not text:
        return {}

    # 1) direct JSON object
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # 2) leading fenced YAML metadata takes precedence over examples in content.
    yaml_fence = re.match(r"\s*```ya?ml\s*(.*?)\s*```", text, re.S | re.I)
    if yaml_fence:
        item = _parse_key_value_text(yaml_fence.group(1))
    else:
        item = {}

    # 3) fenced JSON object (used when no YAML metadata is present).
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if fenced and not item:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    # 4) simple key/value format (also fills fields omitted from YAML).
    for key, value in _parse_key_value_text(text).items():
        item.setdefault(key, value)

    purpose = _extract_markdown_field(text, "Purpose")
    if purpose:
        item["purpose"] = purpose
    recommended = _extract_ordered_markdown_list(text, "Recommended structure")
    if recommended:
        item["recommended_structure"] = recommended
    if not _item_value(item, "canonical_title", "title"):
        heading = re.search(r"^\s*###\s+(.+?)\s*$", text, re.M)
        if heading:
            item["canonical_title"] = heading.group(1).strip()
    return item


def _extract_item_blocks(knowledge_pack_text: str) -> list:
    matches = re.finditer(
        r"^---ITEM_START(?P<marker>[^\r\n]*)\r?\n"
        r"(?P<body>.*?)"
        r"^---ITEM_END(?P<end_marker>[^\r\n]*)$",
        knowledge_pack_text or "",
        flags=re.S | re.M | re.I,
    )
    blocks = []
    for match in matches:
        marker = _normalize_item_marker(match.group("marker"))
        end_marker = _normalize_item_marker(match.group("end_marker"))
        body = match.group("body").strip()
        if body:
            blocks.append({
                "marker_id": marker,
                "end_marker_id": end_marker,
                "content": body,
            })
    return blocks


def _normalize_item_marker(marker: str) -> str:
    normalized = str(marker or "").strip()
    if normalized.startswith(":"):
        normalized = normalized[1:].strip()
    return re.sub(r"---\s*$", "", normalized).strip()


def _register_template_alias(conn, alias_raw: str, template_name: str,
                             source_item_id: str = "",
                             canonical_title: str = "",
                             overwrite_existing: bool = True) -> str:
    alias_raw = str(alias_raw or "").strip()
    if not alias_raw:
        return "skipped"
    alias_key = _normalize_alias_key(alias_raw)
    if not alias_key:
        return "skipped"
    existing = conn.execute(
        "SELECT template_name FROM docops_template_aliases WHERE alias_key=?",
        (alias_key,),
    ).fetchone()
    if existing and existing[0] != template_name and not overwrite_existing:
        return "conflict_skipped"
    conn.execute(
        "INSERT INTO docops_template_aliases "
        "(alias_key, alias_raw, template_name, source_item_id, canonical_title, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(alias_key) DO UPDATE SET "
        "alias_raw=excluded.alias_raw, template_name=excluded.template_name, "
        "source_item_id=excluded.source_item_id, "
        "canonical_title=excluded.canonical_title, updated_at=excluded.updated_at",
        (
            alias_key,
            alias_raw,
            template_name,
            str(source_item_id or ""),
            str(canonical_title or ""),
            _now(),
            _now(),
        ),
    )
    if not existing:
        return "created"
    if existing[0] == template_name:
        return "unchanged"
    return "updated"


def _resolve_template_name(conn, template_ref: str) -> tuple[str | None, str]:
    ref = str(template_ref or "").strip()
    if not ref:
        return None, "empty"
    direct = conn.execute(
        "SELECT name FROM docops_templates WHERE name=?",
        (ref,),
    ).fetchone()
    if direct:
        return direct[0], "name"
    alias_key = _normalize_alias_key(ref)
    if alias_key:
        alias = conn.execute(
            "SELECT template_name FROM docops_template_aliases WHERE alias_key=?",
            (alias_key,),
        ).fetchone()
        if alias:
            return alias[0], "alias"
    fallback_name = _normalize_template_name(ref)
    fallback = conn.execute(
        "SELECT name FROM docops_templates WHERE name=?",
        (fallback_name,),
    ).fetchone()
    if fallback:
        return fallback[0], "normalized"
    return None, "missing"


def _upsert_template(conn, name: str, description: str,
                     required_sections: list, optional_sections: list) -> dict:
    row = conn.execute(
        "SELECT version FROM docops_templates WHERE name=?",
        (name,),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE docops_templates SET version=version+1, description=?, "
            "sections=?, optional_sections=?, updated_at=? WHERE name=?",
            (
                description,
                json.dumps(required_sections),
                json.dumps(optional_sections),
                _now(),
                name,
            ),
        )
        version = row[0] + 1
        action = "updated"
    else:
        conn.execute(
            "INSERT INTO docops_templates "
            "(name, description, sections, optional_sections, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                name,
                description,
                json.dumps(required_sections),
                json.dumps(optional_sections),
                _now(),
                _now(),
            ),
        )
        version = 1
        action = "created"
    return {"template": name, "version": version, "action": action}


# --------------------------------------------------------------- templates
def create_doc_template(name: str, description: str, sections_json: str,
                        optional_sections_json: str = "[]") -> dict:
    """Create or upgrade a versioned document template."""
    name = str(name or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        return {"error": "template name must be a lowercase identifier"}
    try:
        sections = _dedupe_nonempty(json.loads(sections_json))
        optional = _dedupe_nonempty(json.loads(optional_sections_json or "[]"))
        assert isinstance(sections, list) and sections and \
            all(isinstance(s, str) and s.strip() for s in sections)
        assert isinstance(optional, list)
    except Exception:
        return {"error": "sections_json must be a non-empty JSON array of "
                         "section names; optional_sections_json a JSON array"}
    optional = [s for s in optional if s not in set(sections)]
    with _db() as conn:
        saved = _upsert_template(conn, name, description, sections, optional)
        _register_template_alias(conn, name, name, canonical_title=name,
                                 overwrite_existing=True)
    return {"status": "saved", "template": name, "version": saved["version"],
            "required_sections": sections, "optional_sections": optional}


def list_doc_templates(query: str = "", limit: int = 50,
                       summary_only: bool = False) -> dict:
    """List or summarize templates without flooding tool context."""
    query = str(query or "").strip()
    limit = max(1, min(int(limit or 50), 100))
    like = f"%{query}%"
    with _db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM docops_templates"
        ).fetchone()[0]
        matched = conn.execute(
            "SELECT COUNT(*) FROM docops_templates "
            "WHERE name LIKE ? OR description LIKE ?",
            (like, like),
        ).fetchone()[0]
        if summary_only:
            return {
                "count": total,
                "matched_count": matched,
                "query": query or None,
                "templates": [],
            }
        rows = conn.execute(
            "SELECT name, version, description, sections, optional_sections "
            "FROM docops_templates "
            "WHERE name LIKE ? OR description LIKE ? "
            "ORDER BY name LIMIT ?",
            (like, like, limit),
        ).fetchall()
        alias_rows = conn.execute(
            "SELECT template_name, alias_raw FROM docops_template_aliases "
            "WHERE template_name IN ("
            "SELECT name FROM docops_templates "
            "WHERE name LIKE ? OR description LIKE ? ORDER BY name LIMIT ?"
            ") ORDER BY template_name, alias_raw",
            (like, like, limit),
        ).fetchall()
    alias_map = {}
    for template_name, alias_raw in alias_rows:
        alias_map.setdefault(template_name, []).append(alias_raw)
    return {"count": total, "matched_count": matched,
            "returned_count": len(rows), "query": query or None,
            "templates": [
        {"name": r[0], "version": r[1], "description": r[2],
         "required_sections": json.loads(r[3]),
         "optional_sections": json.loads(r[4]),
         "aliases": alias_map.get(r[0], [])}
        for r in rows]}


def docops_inventory_summary() -> dict:
    """Return bounded aggregate counts for evidence snapshots."""
    with _db() as conn:
        template_count = conn.execute(
           "SELECT COUNT(*) FROM docops_templates"
        ).fetchone()[0]
        alias_count = conn.execute(
           "SELECT COUNT(*) FROM docops_template_aliases"
        ).fetchone()[0]
        document_count = conn.execute(
           "SELECT COUNT(DISTINCT doc_id) FROM docops_documents"
        ).fetchone()[0]
        presentation_count = conn.execute(
           "SELECT COUNT(DISTINCT doc_id) FROM docops_presentation_specs"
        ).fetchone()[0]
        artifact_count = conn.execute(
           "SELECT COUNT(*) FROM docops_artifacts WHERE status='ready'"
        ).fetchone()[0]
    return {
        "templates": template_count,
        "aliases": alias_count,
        "documents": document_count,
        "presentations": presentation_count,
        "ready_artifacts": artifact_count,
    }


def import_doc_templates_from_knowledge_pack_text(
        knowledge_pack_text: str,
        overwrite_existing: bool = False,
        include_provisional: bool = False,
        dry_run: bool = False) -> dict:
    """Import DocOps templates from ---ITEM_START/---ITEM_END specs.

    The importer accepts flexible item formats (JSON or key/value blocks).
    Each item may define template_name/name/title/item_id plus required and
    optional sections. Imported aliases let draft_document resolve a template
    by canonical template name, item_id, or canonical title.
    """
    knowledge_pack_text = knowledge_pack_text or ""
    blocks = _extract_item_blocks(knowledge_pack_text)
    start_count = len(re.findall(
        r"^---ITEM_START(?:[^\r\n]*)$",
        knowledge_pack_text,
        flags=re.M | re.I,
    ))
    end_count = len(re.findall(
        r"^---ITEM_END(?:[^\r\n]*)$",
        knowledge_pack_text,
        flags=re.M | re.I,
    ))
    framing_invalid = 0
    framing_errors = []
    if start_count != end_count or len(blocks) != start_count:
        unmatched = max(start_count, end_count) - len(blocks)
        framing_invalid += max(1, unmatched)
        framing_errors.append(
            "knowledge pack has incomplete or unmatched ITEM markers "
            f"(starts={start_count}, ends={end_count}, complete={len(blocks)})"
        )
    mismatched_markers = [
        (block["marker_id"], block["end_marker_id"])
        for block in blocks
        if block["marker_id"] != block["end_marker_id"]
    ]
    if mismatched_markers:
        framing_invalid += len(mismatched_markers)
        for start_marker, end_marker in mismatched_markers[:10]:
            framing_errors.append(
                "ITEM marker IDs do not match "
                f"(start={start_marker!r}, end={end_marker!r})"
            )
    declared_count_match = re.search(
        r"^\s*source_record_count:\s*(\d+)\s*$",
        knowledge_pack_text,
        flags=re.M | re.I,
    )
    if declared_count_match:
        declared_count = int(declared_count_match.group(1))
        if declared_count != len(blocks):
            framing_invalid += abs(declared_count - len(blocks))
            framing_errors.append(
                "knowledge pack item count does not match source_record_count "
                f"(declared={declared_count}, complete={len(blocks)})"
            )
    if not blocks:
        return {
            "status": "no_items_found",
            "items_total": 0,
            "templates_created": 0,
            "templates_updated": 0,
            "aliases_registered": 0,
            "items_skipped_provisional": 0,
            "items_skipped_invalid": framing_invalid,
            "errors": framing_errors,
        }

    created = updated = alias_count = 0
    skipped_existing = skipped_provisional = 0
    skipped_invalid = framing_invalid
    imported = []
    errors = list(framing_errors)
    with _db() as conn:
        for idx, extracted in enumerate(blocks, start=1):
            block = extracted["content"]
            marker_id = extracted["marker_id"]
            item = _parse_knowledge_pack_block(block)
            item_id = str(_item_value(
                item, "item_id", "id", "spec_id", "template_id", "item")
                or marker_id).strip()
            title = str(_item_value(
                item, "canonical_title", "title", "template_title", "name")).strip()
            explicit_template = str(_item_value(
                item, "template_name", "template", "slug")).strip()
            description = str(_item_value(
                item, "description", "summary", "purpose")).strip()

            required = _as_list(_item_value(
                item, "required_sections", "sections", "required",
                "required_section_names", "recommended_structure"))
            optional = _as_list(_item_value(
                item, "optional_sections", "optional", "optional_section_names"))
            if not required:
                required = _dedupe_nonempty(
                    [m.group(1).strip()
                     for m in re.finditer(r"^##\s+(.+?)\s*$", block, re.M)]
                )
            optional = [s for s in _dedupe_nonempty(optional)
                        if s not in set(required)]
            lifecycle_values = [
                str(_item_value(item, key)).strip().lower()
                for key in (
                    "corpus_status", "status", "stage", "approval_state",
                    "source_status",
                )
            ]
            provisional = (
                _is_truthy(_item_value(item, "provisional", "is_provisional"))
                or any(
                    value in {
                        "provisional", "provisional_instructional_spec",
                        "draft", "experimental", "wip",
                    }
                    or "provisional" in value
                    for value in lifecycle_values
                    if value
                )
            )
            if provisional and not include_provisional:
                skipped_provisional += 1
                continue
            if not required:
                skipped_invalid += 1
                errors.append(
                    f"item {idx}: missing required sections "
                    f"(title={title!r}, item_id={item_id!r})")
                continue

            template_seed = explicit_template or title or item_id or f"template_{idx}"
            template_name = _normalize_template_name(template_seed, f"template_{idx}")
            if not re.fullmatch(r"[a-z][a-z0-9_]*", template_name):
                skipped_invalid += 1
                errors.append(
                    f"item {idx}: could not derive valid template name "
                    f"from {template_seed!r}")
                continue

            existing = conn.execute(
                "SELECT version FROM docops_templates WHERE name=?",
                (template_name,),
            ).fetchone()
            if existing and not overwrite_existing:
                skipped_existing += 1
                action = "skipped_existing"
                version = existing[0]
            elif dry_run:
                action = "would_update" if existing else "would_create"
                version = (existing[0] + 1) if existing else 1
                if existing:
                    updated += 1
                else:
                    created += 1
            else:
                saved = _upsert_template(
                    conn,
                    template_name,
                    description or f"Imported from knowledge item {item_id or idx}",
                    required,
                    optional,
                )
                action = saved["action"]
                version = saved["version"]
                if action == "created":
                    created += 1
                else:
                    updated += 1

            aliases = _dedupe_nonempty(
                [template_name, item_id, marker_id, title] +
                _as_list(_item_value(item, "aliases", "alias", "template_aliases"))
            )
            for alias in aliases:
                if dry_run:
                    alias_count += 1
                    continue
                status = _register_template_alias(
                    conn,
                    alias,
                    template_name,
                    source_item_id=item_id,
                    canonical_title=title,
                    overwrite_existing=overwrite_existing,
                )
                if status in {"created", "updated", "unchanged"}:
                    alias_count += 1

            imported.append({
                "item_index": idx,
                "item_id": item_id or None,
                "title": title or None,
                "template": template_name,
                "version": version,
                "action": action,
                "required_sections": required,
                "optional_sections": optional,
                "aliases": aliases,
            })

    status = "dry_run_complete" if dry_run else "imported"
    return {
        "status": status,
        "items_total": len(blocks),
        "templates_created": created,
        "templates_updated": updated,
        "templates_skipped_existing": skipped_existing,
        "items_skipped_provisional": skipped_provisional,
        "items_skipped_invalid": skipped_invalid,
        "aliases_registered": alias_count,
        "imports": imported,
        "errors": errors,
    }


# --------------------------------------------------------------- documents
def _validate_sections(template_row, sections: dict) -> list:
    """Return list of validation errors for section content vs template."""
    required = json.loads(template_row[3])
    optional = set(json.loads(template_row[4]))
    errors = []
    for sec in required:
        body = sections.get(sec, "")
        if not isinstance(body, str) or not body.strip():
            errors.append(f"required section '{sec}' is missing or empty")
    known = set(required) | optional
    for sec in sections:
        if sec not in known:
            errors.append(f"unknown section '{sec}' "
                          f"(template defines: {sorted(known)})")
    return errors


def _render(title: str, template: str, doc_id: str, version: int,
            ordered_sections: list, sections: dict, tags: str) -> str:
    lines = [f"# {title}", "",
             f"> **Doc:** {doc_id} v{version} · **Template:** {template} · "
             f"**Status:** DRAFT · **Created:** {_now()}"
             + (f" · **Tags:** {tags}" if tags else ""), ""]
    for sec in ordered_sections:
        if sec in sections and str(sections[sec]).strip():
            lines += [f"## {sec}", "", str(sections[sec]).strip(), ""]
    return "\n".join(lines)


def _write_version(conn, doc_id, version, title, template, tpl_version,
                   sections, tags, change_note):
    tpl_row = conn.execute(
        "SELECT name, version, description, sections, optional_sections "
        "FROM docops_templates WHERE name=?", (template,)).fetchone()
    ordered = json.loads(tpl_row[3]) + json.loads(tpl_row[4])
    content = _render(title, template, doc_id, version, ordered,
                      sections, tags)
    path = DOCS_DIR / f"{doc_id}-{_slug(title)}-v{version}.md"
    path.write_text(content)
    sha = _hash(content)
    conn.execute(
        "INSERT INTO docops_documents (doc_id, version, title, template, "
        "template_version, path, sha256, tags, change_note) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (doc_id, version, title, template, tpl_version, str(path), sha,
         tags, change_note))
    markers = sorted({m for m in BLOCKING_MARKERS if m in content})
    return {"doc_id": doc_id, "version": version, "status": "draft",
            "path": str(path), "sha256": sha,
            "unresolved_markers": markers or None,
            "next": ("resolve markers then finalize_document"
                     if markers else "finalize_document when approved")}


def draft_document(template: str, title: str, sections_json: str,
                   tags: str = "", finalize: bool = False) -> dict:
    """Draft a new mission-critical document from a template.

    sections_json maps section name -> markdown body. All required
    template sections must be present and non-empty. Use [TBD - ...] or
    [VERIFY CURRENT] for unresolved facts — they block finalization.
    finalize=True seals the document as FINAL in one pass (only when the
    user asked for a final/audience-ready document and no markers remain).
    """
    try:
        sections = json.loads(sections_json)
        assert isinstance(sections, dict)
    except Exception:
        return {"error": "sections_json must be a JSON object "
                         "{section_name: markdown_body}"}
    template_ref = str(template or "").strip()
    with _db() as conn:
        resolved_template, resolution_source = _resolve_template_name(
            conn, template_ref)
        if not resolved_template:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM docops_templates").fetchall()]
            alias_hits = conn.execute(
                "SELECT alias_raw, template_name FROM docops_template_aliases "
                "ORDER BY updated_at DESC LIMIT 12"
            ).fetchall()
            return {
                "error": f"unknown template '{template_ref}'",
                "available": names,
                "recent_aliases": [
                    {"alias": r[0], "template": r[1]} for r in alias_hits
                ],
            }
        tpl = conn.execute(
            "SELECT name, version, description, sections, optional_sections "
            "FROM docops_templates WHERE name=?", (resolved_template,)).fetchone()
        errors = _validate_sections(tpl, sections)
        if errors:
            return {"status": "rejected", "errors": errors}
        doc_id = "DOC-" + str(uuid.uuid4())[:8]
        result = _write_version(conn, doc_id, 1, title, resolved_template, tpl[1],
                                sections, tags, "initial draft")
    if resolution_source != "name":
        result["resolved_template"] = resolved_template
        result["template_resolution"] = resolution_source
    if finalize and not result.get("unresolved_markers"):
        sealed = finalize_document(doc_id)
        if sealed.get("status") == "final":
            result.update(status="final", sha256=sealed["sha256"],
                          next="export_document for an audience-ready file")
    elif finalize:
        result["next"] = ("finalize was requested but unresolved markers "
                          "remain; resolve via revise_document first")
    return _attach_source_artifact(result)


def revise_document(doc_id: str, sections_json: str,
                    change_note: str = "") -> dict:
    """Create a new version of a document (supersedes the previous one).

    sections_json contains only the sections to replace; unchanged
    sections carry forward from the latest version. Works on drafts and
    finals (a final gets superseded by the new draft).
    """
    try:
        updates = json.loads(sections_json)
        assert isinstance(updates, dict) and updates
    except Exception:
        return {"error": "sections_json must be a non-empty JSON object"}
    with _db() as conn:
        row = conn.execute(
            "SELECT version, title, template, template_version, path, tags "
            "FROM docops_documents WHERE doc_id=? "
            "ORDER BY version DESC LIMIT 1", (doc_id,)).fetchone()
        if not row:
            return {"error": f"unknown doc_id '{doc_id}'"}
        version, title, template, tpl_version, path, tags = row
        # Recover current sections from the stored markdown.
        text = Path(path).read_text() if Path(path).exists() else ""
        current = {}
        for m in re.finditer(r"^## (.+?)\n\n(.*?)(?=\n## |\Z)", text,
                             re.S | re.M):
            current[m.group(1).strip()] = m.group(2).strip()
        current.update({k: str(v) for k, v in updates.items()})
        tpl = conn.execute(
            "SELECT name, version, description, sections, optional_sections "
            "FROM docops_templates WHERE name=?", (template,)).fetchone()
        errors = _validate_sections(tpl, current)
        if errors:
            return {"status": "rejected", "errors": errors}
        conn.execute(
            "UPDATE docops_documents SET status='superseded' "
            "WHERE doc_id=? AND version=?", (doc_id, version))
        result = _write_version(
            conn,
            doc_id,
            version + 1,
            title,
            template,
            tpl_version,
            current,
            tags,
            change_note or "revision",
        )
    return _attach_source_artifact(result)


def _presentation_spec(title: str, audience: str, slides_json: str,
                       subtitle: str = "", fallback_spec: dict | None = None) -> dict:
    try:
        parsed = json.loads(slides_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise presentationops.PresentationValidationError(
            f"slides_json must be valid JSON: {exc}"
        ) from exc
    if isinstance(parsed, list):
        raw = {"slides": parsed}
    elif isinstance(parsed, dict):
        raw = dict(parsed)
    else:
        raise presentationops.PresentationValidationError(
            "slides_json must be a slide array or presentation object"
        )
    fallback_spec = fallback_spec or {}
    raw["title"] = str(title or raw.get("title") or "").strip()
    raw["audience"] = str(
        audience or raw.get("audience") or fallback_spec.get("audience") or ""
    ).strip()
    raw["subtitle"] = str(
        subtitle
        or raw.get("subtitle")
        or fallback_spec.get("subtitle")
        or ""
    ).strip()
    return presentationops.normalize_spec(raw)


def _presentation_markdown_body(spec: dict) -> str:
    companion = presentationops.spec_to_markdown(spec)
    return re.sub(r"^# .*?\n+", "", companion, count=1)


def _presentation_companion_matches(content: str, spec: dict) -> bool:
    parts = content.split("\n\n", 2)
    return (
        len(parts) == 3
        and parts[2].strip() == _presentation_markdown_body(spec).strip()
    )


def _write_presentation_version(conn, doc_id: str, version: int, title: str,
                                spec: dict, tags: str, change_note: str) -> dict:
    template = "slide_presentation"
    tpl = conn.execute(
        "SELECT version FROM docops_templates WHERE name=?", (template,)
    ).fetchone()
    if not tpl:
        raise RuntimeError("slide_presentation template is unavailable")
    body = _presentation_markdown_body(spec)
    content = (
        f"# {title}\n\n"
        f"> **Doc:** {doc_id} v{version} · **Template:** {template} · "
        f"**Status:** DRAFT · **Created:** {_now()}"
        + (f" · **Tags:** {tags}" if tags else "")
        + "\n\n"
        + body
    )
    path = DOCS_DIR / f"{doc_id}-{_slug(title)}-v{version}.md"
    path.write_text(content)
    sha = _hash(content)
    canonical_spec = json.dumps(
        spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    spec_sha = _hash(canonical_spec)
    conn.execute(
        "INSERT INTO docops_documents (doc_id, version, title, template, "
        "template_version, path, sha256, tags, change_note) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            doc_id, version, title, template, tpl[0], str(path), sha, tags,
            change_note,
        ),
    )
    conn.execute(
        "INSERT INTO docops_presentation_specs "
        "(doc_id, version, schema_version, spec_json, spec_sha256) "
        "VALUES (?,?,?,?,?)",
        (doc_id, version, spec["schema_version"], canonical_spec, spec_sha),
    )
    return {
        "doc_id": doc_id,
        "version": version,
        "status": "draft",
        "path": str(path),
        "sha256": sha,
        "presentation_spec_sha256": spec_sha,
        "slide_count": len(spec["slides"]),
        "unresolved_markers": None,
        "next": "finalize_document when approved, then export_document as pptx",
    }


def draft_presentation(title: str, audience: str, slides_json: str,
                       subtitle: str = "", tags: str = "",
                       finalize: bool = False) -> dict:
    """Create a governed presentation from an authoritative slide specification."""
    try:
        spec = _presentation_spec(title, audience, slides_json, subtitle)
    except presentationops.PresentationValidationError as exc:
        return {"status": "rejected", "error": str(exc)}
    doc_id = "DOC-" + str(uuid.uuid4())[:8]
    with _db() as conn:
        result = _write_presentation_version(
            conn, doc_id, 1, spec["title"], spec, tags, "initial presentation"
        )
    if finalize:
        sealed = finalize_document(doc_id)
        if sealed.get("status") in {"final", "already_final"}:
            result.update(
                status="final",
                sha256=sealed.get("sha256", result["sha256"]),
                next="export_document with format pptx",
            )
    return _attach_source_artifact(result)


def revise_presentation(doc_id: str, slides_json: str, audience: str = "",
                        subtitle: str = "", change_note: str = "",
                        finalize: bool = False) -> dict:
    """Issue a complete new structured presentation version."""
    with _db() as conn:
        row = conn.execute(
            "SELECT version, title, tags FROM docops_documents "
            "WHERE doc_id=? ORDER BY version DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        if not row:
            return {"error": f"unknown doc_id '{doc_id}'"}
        version, title, tags = row
        prior_row = conn.execute(
            "SELECT spec_json FROM docops_presentation_specs "
            "WHERE doc_id=? AND version=?",
            (doc_id, version),
        ).fetchone()
        prior_spec = json.loads(prior_row[0]) if prior_row else {}
        try:
            spec = _presentation_spec(
                title, audience, slides_json, subtitle, fallback_spec=prior_spec
            )
        except presentationops.PresentationValidationError as exc:
            return {"status": "rejected", "error": str(exc)}
        conn.execute(
            "UPDATE docops_documents SET status='superseded' "
            "WHERE doc_id=? AND version=?",
            (doc_id, version),
        )
        result = _write_presentation_version(
            conn,
            doc_id,
            version + 1,
            title,
            spec,
            tags,
            change_note or "presentation revision",
        )
    if finalize:
        sealed = finalize_document(doc_id)
        if sealed.get("status") in {"final", "already_final"}:
            result.update(
                status="final",
                sha256=sealed.get("sha256", result["sha256"]),
                next="export_document with format pptx",
            )
    return _attach_source_artifact(result)


def get_presentation_spec(doc_id: str, version: int = 0) -> dict:
    """Return the normalized slide specification for a governed presentation."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT p.version, p.schema_version, p.spec_json, p.spec_sha256, "
            "d.status FROM docops_presentation_specs p "
            "JOIN docops_documents d "
            "ON d.doc_id=p.doc_id AND d.version=p.version "
            "WHERE p.doc_id=? ORDER BY p.version",
            (doc_id,),
        ).fetchall()
    if not rows:
        return {"error": f"no presentation specification for '{doc_id}'"}
    target = next((row for row in rows if row[0] == version), None)
    if version and target is None:
        return {"error": f"presentation version {version} not found"}
    target = target or rows[-1]
    if _hash(target[2]) != target[3]:
        return {"status": "blocked", "reason": "presentation specification hash mismatch"}
    return {
        "doc_id": doc_id,
        "version": target[0],
        "schema_version": target[1],
        "status": target[4],
        "spec_sha256": target[3],
        "presentation": json.loads(target[2]),
    }


def finalize_document(doc_id: str) -> dict:
    """Review gate: mark the latest version FINAL and seal its hash.

    Blocks if unresolved [TBD]/[VERIFY CURRENT]/{{placeholder}}/TODO
    markers remain, or if the file was modified outside DocOps.
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT version, path, sha256, status FROM docops_documents "
            "WHERE doc_id=? ORDER BY version DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        if not row:
            return {"error": f"unknown doc_id '{doc_id}'"}
        version, path, sha, status = row
        p = Path(path)
        if not p.exists():
            return {"error": f"file missing: {path}"}
        content = p.read_text()
        if _hash(content) != sha:
            return {
                "status": "blocked",
                "reason": (
                    "file was modified outside DocOps; use "
                    "revise_document to issue a new version"
                ),
            }
        spec_row = conn.execute(
            "SELECT spec_json, spec_sha256 "
            "FROM docops_presentation_specs "
            "WHERE doc_id=? AND version=?",
            (doc_id, version),
        ).fetchone()
        if spec_row:
            if _hash(spec_row[0]) != spec_row[1]:
                return {
                    "status": "blocked",
                    "reason": "presentation specification hash mismatch",
                }
            try:
                normalized_spec = presentationops.normalize_spec(
                    json.loads(spec_row[0])
                )
            except presentationops.PresentationValidationError as exc:
                return {
                    "status": "blocked",
                    "reason": f"invalid presentation specification: {exc}",
                }
            if not _presentation_companion_matches(content, normalized_spec):
                return {
                    "status": "blocked",
                    "reason": (
                        "presentation companion diverges from the "
                        "authoritative slide specification"
                    ),
                }

        if status == "final":
            result = {
                "doc_id": doc_id,
                "version": version,
                "status": "already_final",
                "sha256": sha,
                "path": path,
            }
        else:
            markers = sorted({
                marker for marker in BLOCKING_MARKERS if marker in content
            })
            if markers:
                return {
                    "status": "blocked",
                    "unresolved_markers": markers,
                    "reason": "resolve markers via revise_document first",
                }
            content = content.replace(
                "**Status:** DRAFT", "**Status:** FINAL", 1
            )
            p.write_text(content)
            new_sha = _hash(content)
            conn.execute(
                "UPDATE docops_documents SET status='final', sha256=?, "
                "finalized_at=? WHERE doc_id=? AND version=?",
                (new_sha, _now(), doc_id, version),
            )
            result = {
                "doc_id": doc_id,
                "version": version,
                "status": "final",
                "sha256": new_sha,
                "path": path,
            }
    return _attach_source_artifact(result)


def list_documents(status: str = "all", query: str = "") -> dict:
    """List documents in the registry, filtered by status and/or keyword."""
    like = f"%{query}%"
    with _db() as conn:
        rows = conn.execute(
            "SELECT doc_id, version, title, template, status, path, "
            "sha256, created_at, finalized_at FROM docops_documents "
            "WHERE (status = ? OR ? = 'all') "
            "AND (title LIKE ? OR tags LIKE ? OR doc_id LIKE ?) "
            "ORDER BY created_at DESC LIMIT 50",
            (status, status, like, like, like)).fetchall()
    return {"count": len(rows), "documents": [
        {"doc_id": r[0], "version": r[1], "title": r[2], "template": r[3],
         "status": r[4], "path": r[5], "sha256": r[6][:12],
         "created_at": r[7], "finalized_at": r[8]} for r in rows]}


def get_document(doc_id: str, version: int = 0) -> dict:
    """Read a document's content and full version lineage."""
    with _db() as conn:
        history = conn.execute(
            "SELECT version, status, sha256, change_note, created_at, "
            "finalized_at, path FROM docops_documents WHERE doc_id=? "
            "ORDER BY version", (doc_id,)).fetchall()
    if not history:
        return {"error": f"unknown doc_id '{doc_id}'"}
    target = next((h for h in history if h[0] == version),
                  history[-1])
    path = Path(target[6])
    content = path.read_text() if path.exists() else "(file missing)"
    return {"doc_id": doc_id, "version": target[0], "status": target[1],
            "content": content[:20000],
            "lineage": [{"version": h[0], "status": h[1],
                         "sha256": h[2][:12], "change_note": h[3],
                         "created_at": h[4], "finalized_at": h[5]}
                        for h in history]}


# ------------------------------------------------------------- publishing
_LOGO_PATH = _ROOT / "assets" / "aimhi-logo.svg"

# Aimhi brand palette (from aimhi-manifest-portal assets).
_BRAND = {"primary": "#1557FF", "accent": "#00DEEB", "ink": "#050713",
          "muted": "#3e475a", "paper": "#F7F9FC", "line": "#d8dce6"}

_EXPORT_CSS = f"""
  @page {{ margin: 22mm; }}
  body {{ font-family: -apple-system, 'Helvetica Neue', 'Segoe UI', sans-serif;
         color: {_BRAND['ink']}; line-height: 1.6; max-width: 46em;
         margin: 0 auto; padding: 0 2em 2em; font-size: 12pt;
         background: #fff; }}
  header.brand {{ display: flex; align-items: center; gap: 0.8em;
         padding: 1.6em 0 1em; margin-bottom: 2.2em;
         border-bottom: 3px solid {_BRAND['primary']}; }}
  header.brand svg {{ width: 42px; height: 42px; flex: none; }}
  header.brand .wordmark {{ font-size: 1.15em; font-weight: 700;
         letter-spacing: 0.12em; color: {_BRAND['ink']}; }}
  header.brand .wordmark span {{ color: {_BRAND['primary']}; }}
  header.brand .tagline {{ margin-left: auto; color: {_BRAND['muted']};
         font-size: 0.8em; letter-spacing: 0.06em; }}
  h1 {{ font-size: 1.85em; letter-spacing: -0.015em; margin: 0 0 0.15em;
       color: {_BRAND['ink']}; }}
  .docmeta {{ color: {_BRAND['muted']}; font-size: 0.85em;
       margin: 0 0 2.2em; padding-bottom: 1em;
       border-bottom: 1px solid {_BRAND['line']}; }}
  h2 {{ font-size: 1.2em; margin-top: 2em; color: {_BRAND['primary']};
       padding-bottom: 0.2em; border-bottom: 1px solid {_BRAND['line']}; }}
  h3 {{ color: {_BRAND['muted']}; }}
  a {{ color: {_BRAND['primary']}; }}
  strong {{ color: {_BRAND['ink']}; }}
  code, pre {{ font-family: 'SF Mono', Menlo, monospace; font-size: 0.88em;
              background: {_BRAND['paper']}; border-radius: 4px; }}
  pre {{ padding: 0.9em 1.1em; overflow-x: auto;
        border-left: 3px solid {_BRAND['accent']}; }}
  code {{ padding: 0.1em 0.35em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ border: 1px solid {_BRAND['line']}; padding: 0.5em 0.75em;
           text-align: left; }}
  th {{ background: {_BRAND['primary']}; color: #fff; font-weight: 600; }}
  tr:nth-child(even) td {{ background: {_BRAND['paper']}; }}
  ul, ol {{ padding-left: 1.4em; }}
  li::marker {{ color: {_BRAND['primary']}; }}
  blockquote {{ border-left: 3px solid {_BRAND['accent']}; margin-left: 0;
               padding-left: 1em; color: {_BRAND['muted']}; }}
  footer {{ margin-top: 3.5em; padding-top: 0.8em;
           border-top: 2px solid {_BRAND['primary']};
           color: {_BRAND['muted']}; font-size: 0.75em;
           display: flex; justify-content: space-between; gap: 1em; }}
  .watermark {{ position: fixed; top: 40%; left: 0; right: 0; text-align:
               center; font-size: 7em; color: rgba(226, 26, 102, 0.10);
               transform: rotate(-28deg); z-index: -1; font-weight: 700;
               letter-spacing: 0.2em; pointer-events: none; }}
  @media print {{ body {{ margin: 0 auto; }} }}
"""


def _brand_header() -> str:
    logo = ""
    try:
        logo = _LOGO_PATH.read_text()
    except Exception:
        pass
    return (f'<header class="brand">{logo}'
            f'<div class="wordmark">AIM<span>HI</span></div>'
            f'<div class="tagline">PRYCELESS VENTURES</div></header>')


_EXPORT_MIME_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "rtf": "application/rtf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _export_lock(doc_id: str, version: int, format: str):
    lock_dir = EXPORTS_DIR / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / (
        f"{_slug(doc_id)}-{int(version)}-{_slug(format)}.lock"
    )
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _verified_export_path(path_value: str | Path) -> Path:
    root = EXPORTS_DIR.resolve()
    lexical_root = EXPORTS_DIR.absolute()
    raw = Path(path_value).absolute()
    try:
        lexical_parts = raw.relative_to(lexical_root).parts
        lexical = lexical_root
    except ValueError:
        try:
            lexical_parts = raw.relative_to(root).parts
            lexical = root
        except ValueError as exc:
            raise ValueError("artifact path is outside the export root") from exc
    for part in lexical_parts:
        lexical /= part
        if lexical.is_symlink():
            raise ValueError("artifact path may not contain symlinks")
    resolved = raw.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact path is outside the export root") from exc
    if not resolved.is_file():
        raise ValueError("artifact target is not a file")
    current = resolved.parent
    while current != root:
        if current.is_symlink():
            raise ValueError("artifact parent may not be a symlink")
        current = current.parent
    return resolved


def _register_export(doc_id: str, version: int, format: str, path: Path,
                     audience_ready: bool) -> dict:
    resolved = _verified_export_path(path)
    byte_size = resolved.stat().st_size
    sha = _hash_file(resolved)
    mime_type = _EXPORT_MIME_TYPES.get(
        format, mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    )
    identity = "\0".join((
        doc_id,
        str(version),
        format,
        resolved.name,
        sha,
        "1" if audience_ready else "0",
    ))
    artifact_id = "ART-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
    immutable_path = EXPORTS_DIR / ".artifacts" / artifact_id / resolved.name
    if (
        not immutable_path.exists()
        or immutable_path.stat().st_size != byte_size
        or _hash_file(immutable_path) != sha
    ):
        _atomic_copy(resolved, immutable_path)
    immutable = _verified_export_path(immutable_path)
    if immutable.stat().st_size != byte_size or _hash_file(immutable) != sha:
        raise ValueError("immutable artifact copy failed integrity validation")
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO docops_artifacts "
            "(artifact_id, doc_id, version, format, filename, path, mime_type, "
            "byte_size, sha256, status, audience_ready) "
            "VALUES (?,?,?,?,?,?,?,?,?,'ready',?)",
            (
                artifact_id,
                doc_id,
                version,
                format,
                resolved.name,
                str(immutable),
                mime_type,
                byte_size,
                sha,
                int(audience_ready),
            ),
        )
        stored = conn.execute(
            "SELECT doc_id, version, format, filename, path, mime_type, "
            "byte_size, sha256, status, audience_ready "
            "FROM docops_artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
    expected = (
        doc_id,
        version,
        format,
        resolved.name,
        str(immutable),
        mime_type,
        byte_size,
        sha,
        "ready",
        int(audience_ready),
    )
    if stored != expected:
        raise ValueError("artifact identity collision")
    return {
        "artifact_id": artifact_id,
        "doc_id": doc_id,
        "version": version,
        "format": format,
        "filename": resolved.name,
        "mime_type": mime_type,
        "byte_size": byte_size,
        "sha256": sha,
        "status": "ready",
        "audience_ready": bool(audience_ready),
        "download_url": f"/responses/artifacts/{artifact_id}",
    }


def register_external_artifact(
        doc_id: str,
        version: int,
        format: str,
        path: str | Path,
        *,
        audience_ready: bool = True) -> dict:
    """Register validated bytes produced by another governed capability."""
    if not isinstance(doc_id, str) or not doc_id.strip():
        raise ValueError("artifact document ID is required")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("artifact version must be a positive integer")
    if not isinstance(format, str) or not re.fullmatch(r"[a-z0-9]{2,12}", format):
        raise ValueError("artifact format is invalid")
    return _register_export(
        doc_id.strip(),
        version,
        format,
        Path(path),
        audience_ready=bool(audience_ready),
    )


def tombstone_export_artifact(
        artifact_id: str,
        *,
        connection: sqlite3.Connection | None = None) -> bool:
    """Disable delivery without deleting immutable artifact bytes or lineage."""
    if not isinstance(artifact_id, str) or not re.fullmatch(
        r"ART-[a-f0-9]{32}", artifact_id
    ):
        return False
    if connection is not None:
        cursor = connection.execute(
            "UPDATE docops_artifacts SET status='tombstoned' "
            "WHERE artifact_id=? AND status='ready'",
            (artifact_id,),
        )
        return cursor.rowcount == 1
    with _db() as conn:
        cursor = conn.execute(
            "UPDATE docops_artifacts SET status='tombstoned' "
            "WHERE artifact_id=? AND status='ready'",
            (artifact_id,),
        )
        return cursor.rowcount == 1


def _attach_source_artifact(result: dict) -> dict:
    if (
        not isinstance(result, dict)
        or result.get("status") not in {"draft", "final", "already_final"}
        or not result.get("path")
    ):
        return result
    source = Path(result["path"])
    if not source.is_file():
        return result
    doc_id = result["doc_id"]
    version = int(result["version"])
    audience_ready = result["status"] in {"final", "already_final"}
    try:
        with _export_lock(doc_id, version, "md"):
            expected_sha = result.get("sha256")
            if not expected_sha or _hash(source.read_text()) != expected_sha:
                raise ValueError(
                    "source document does not match its governed SHA-256"
                )
            staging = EXPORTS_DIR / "sources" / source.name
            _atomic_copy(source, staging)
            if _hash(staging.read_text()) != expected_sha:
                raise ValueError(
                    "source artifact copy failed governed-hash validation"
                )
            result["artifact"] = _register_export(
                doc_id,
                version,
                "md",
                staging,
                audience_ready=audience_ready,
            )
            if result["artifact"]["sha256"] != expected_sha:
                raise ValueError(
                    "source artifact bytes do not match the governed SHA-256"
                )
    except (OSError, ValueError) as exc:
        result["artifact_error"] = f"source artifact registration failed: {exc}"
    return result


def resolve_export_artifact(artifact_id: str, *, include_path: bool = False) -> dict:
    """Resolve and integrity-check a registered artifact by opaque ID."""
    with _db() as conn:
        row = conn.execute(
            "SELECT artifact_id, doc_id, version, format, filename, path, "
            "mime_type, byte_size, sha256, status, audience_ready, created_at "
            "FROM docops_artifacts WHERE artifact_id=?",
            (str(artifact_id or "").strip(),),
        ).fetchone()
    if not row:
        return {"error": "artifact not found", "status": "not_found"}
    try:
        path = _verified_export_path(row[5])
    except (OSError, ValueError) as exc:
        return {"error": str(exc), "status": "blocked"}
    actual_size = path.stat().st_size
    actual_sha = _hash_file(path)
    if actual_size != row[7] or actual_sha != row[8]:
        return {
            "error": "artifact integrity mismatch",
            "status": "blocked",
            "expected_sha256": row[8],
            "actual_sha256": actual_sha,
        }
    result = {
        "artifact_id": row[0],
        "doc_id": row[1],
        "version": row[2],
        "format": row[3],
        "filename": row[4],
        "mime_type": row[6],
        "byte_size": row[7],
        "sha256": row[8],
        "status": row[9],
        "audience_ready": bool(row[10]),
        "created_at": row[11],
        "download_url": f"/responses/artifacts/{row[0]}",
    }
    if include_path:
        result["path"] = str(path)
    return result


def open_export_artifact_snapshot(artifact_id: str):
    """Open, hash, and rewind the exact immutable bytes that will be served."""
    artifact = resolve_export_artifact(artifact_id, include_path=True)
    if artifact.get("status") != "ready":
        return artifact, None
    path = Path(artifact.pop("path"))
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        source = os.fdopen(descriptor, "rb")
    except OSError as exc:
        return {"status": "blocked", "error": str(exc)}, None
    snapshot = tempfile.SpooledTemporaryFile(
        max_size=8 * 1024 * 1024,
        mode="w+b",
    )
    try:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("artifact snapshot is not a regular file")
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            snapshot.write(chunk)
        actual_sha = digest.hexdigest()
        if opened.st_size != artifact["byte_size"] or actual_sha != artifact["sha256"]:
            raise ValueError("artifact snapshot integrity mismatch")
        snapshot.seek(0)
        source.close()
        return artifact, snapshot
    except (OSError, ValueError) as exc:
        source.close()
        snapshot.close()
        return {
            "status": "blocked",
            "error": str(exc),
            "expected_sha256": artifact.get("sha256"),
        }, None


def list_export_artifacts(doc_id: str = "", version: int = 0,
                          format: str = "") -> dict:
    """List verified artifact metadata without exposing server paths."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT artifact_id FROM docops_artifacts "
            "WHERE (?='' OR doc_id=?) AND (?=0 OR version=?) "
            "AND (?='' OR format=?) ORDER BY created_at DESC LIMIT 100",
            (doc_id, doc_id, version, version, format, format),
        ).fetchall()
    artifacts = []
    blocked = []
    for (artifact_id,) in rows:
        artifact = resolve_export_artifact(artifact_id)
        if artifact.get("status") == "ready":
            artifacts.append(artifact)
        else:
            blocked.append({
                "artifact_id": artifact_id,
                "status": artifact.get("status"),
                "error": artifact.get("error"),
            })
    return {
        "count": len(artifacts),
        "artifacts": artifacts,
        "blocked": blocked,
    }


def export_document(doc_id: str, format: str = "html",
                    version: int = 0) -> dict:
    """Render an audience-ready deliverable of a document.

    Formats: html, docx, rtf, and native pptx for governed presentation
    specifications. Every successful export is registered under an opaque,
    integrity-checked artifact ID.
    """
    format = str(format or "html").strip().lower()
    if format not in ("html", "docx", "rtf", "pptx"):
        return {"error": "format must be one of: html, docx, rtf, pptx"}
    if format != "pptx" and _markdown is None:
        return {"error": "python 'markdown' package not installed in venv"}
    with _db() as conn:
        history = conn.execute(
            "SELECT version, status, path, title, sha256 "
            "FROM docops_documents WHERE doc_id=? ORDER BY version",
            (doc_id,)).fetchall()
        presentation_rows = conn.execute(
            "SELECT version, spec_json, spec_sha256 "
            "FROM docops_presentation_specs WHERE doc_id=? ORDER BY version",
            (doc_id,),
        ).fetchall()
    if not history:
        return {"error": f"unknown doc_id '{doc_id}'"}
    target = next((h for h in history if h[0] == version), None)
    if version and target is None:
        return {"error": f"document version {version} not found"}
    target = target or history[-1]
    ver, status, path, title, sha = target
    p = Path(path)
    if not p.exists():
        return {"error": f"file missing: {path}"}
    text = p.read_text()
    if _hash(text) != sha:
        return {"status": "blocked",
                "reason": "file was modified outside DocOps; "
                          "revise_document to issue a clean version"}

    is_final = status == "final"
    stem = f"{doc_id}-{_slug(title)}-v{ver}" + ("" if is_final else "-DRAFT")
    if format == "pptx":
        spec_row = next(
            (row for row in presentation_rows if row[0] == ver), None
        )
        if not spec_row:
            return {
                "status": "rejected",
                "error": (
                    "native PPTX requires a governed presentation specification; "
                    "use draft_presentation or revise_presentation"
                ),
            }
        if _hash(spec_row[1]) != spec_row[2]:
            return {
                "status": "blocked",
                "reason": "presentation specification hash mismatch",
            }
        try:
            spec = presentationops.normalize_spec(json.loads(spec_row[1]))
            with _export_lock(doc_id, ver, format):
                out_path = EXPORTS_DIR / f"{stem}.pptx"
                render = presentationops.render_pptx(
                    spec,
                    out_path,
                    doc_id=doc_id,
                    version=ver,
                    status=status,
                    source_sha256=sha,
                )
                preview_dir = EXPORTS_DIR / f"{stem}-previews"
                previews = presentationops.render_previews(spec, preview_dir)
                artifact = _register_export(
                    doc_id, ver, format, out_path, audience_ready=is_final
                )
        except (OSError, ValueError, presentationops.PresentationValidationError) as exc:
            return {"status": "blocked", "error": f"PPTX export failed: {exc}"}
        return {
            "doc_id": doc_id,
            "version": ver,
            "format": format,
            "audience_ready": is_final,
            "watermarked_draft": not is_final,
            "path": str(out_path),
            "artifact": artifact,
            "validation": render["validation"],
            "slide_count": render["slides"],
            "preview_count": len(previews),
            "note": (
                "validated native PowerPoint export"
                if is_final
                else "validated native PowerPoint export with DRAFT watermark"
            ),
        }

    # Strip internal metadata banner and title (re-rendered cleanly).
    body_md = re.sub(r"^# .*?\n+> \*\*Doc:\*\*.*?\n+", "", text, count=1,
                     flags=re.S)
    body_html = _markdown.markdown(body_md,
                                   extensions=["tables", "fenced_code"])
    date = datetime.now(timezone.utc).strftime("%B %d, %Y")
    watermark = "" if is_final else '<div class="watermark">DRAFT</div>'
    meta = f'<p class="docmeta">{date}</p>'
    footer = (f"<footer><span>{_html.escape(title)} · {doc_id} v{ver} · "
              f"{'Final' if is_final else 'Working draft'}</span>"
              f"<span>integrity {sha[:12]}</span></footer>")
    page = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{_html.escape(title)}</title>"
            f"<style>{_EXPORT_CSS}</style></head><body>{watermark}"
            f"{_brand_header()}"
            f"<h1>{_html.escape(title)}</h1>{meta}{body_html}{footer}"
            f"</body></html>")

    try:
        with _export_lock(doc_id, ver, format):
            html_path = EXPORTS_DIR / f"{stem}.html"
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=EXPORTS_DIR,
                prefix=f".{html_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                html_temporary = Path(handle.name)
                handle.write(page)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                html_temporary.chmod(0o600)
                out_path = html_path
                if format == "html":
                    html_temporary.replace(html_path)
                else:
                    out_path = EXPORTS_DIR / f"{stem}.{format}"
                    descriptor, temporary_name = tempfile.mkstemp(
                        dir=EXPORTS_DIR,
                        prefix=f".{out_path.name}.",
                        suffix=f".{format}",
                    )
                    os.close(descriptor)
                    out_temporary = Path(temporary_name)
                    out_temporary.unlink(missing_ok=True)
                    try:
                        conv = subprocess.run(
                            [
                                "textutil",
                                "-convert",
                                format,
                                str(html_temporary),
                                "-output",
                                str(out_temporary),
                            ],
                            capture_output=True,
                            text=True,
                            timeout=60,
                        )
                        if (
                            conv.returncode != 0
                            or not out_temporary.is_file()
                            or out_temporary.stat().st_size == 0
                        ):
                            return {
                                "error": "textutil conversion failed",
                                "detail": conv.stderr.strip()[:300],
                            }
                        out_temporary.chmod(0o600)
                        out_temporary.replace(out_path)
                    finally:
                        out_temporary.unlink(missing_ok=True)
            finally:
                html_temporary.unlink(missing_ok=True)
            artifact = _register_export(
                doc_id, ver, format, out_path, audience_ready=is_final
            )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {
            "status": "blocked",
            "error": f"artifact export failed: {exc}",
        }
    return {"doc_id": doc_id, "version": ver, "format": format,
            "audience_ready": is_final,
            "watermarked_draft": not is_final,
            "path": str(out_path),
            "artifact": artifact,
            "note": ("clean final export" if is_final else
                     "non-final version exported with DRAFT watermark; "
                     "finalize_document first for a clean deliverable")}


# ------------------------------------------------------------ tool schemas
DOCOPS_SCHEMAS = [
    {"type": "function", "name": "list_doc_templates",
     "description": ("Search or summarize DocOps templates. Use "
                     "summary_only=true for counts; never request the entire "
                     "catalog when a count or narrow lookup is sufficient."),
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string",
                   "description": "Optional template name/description filter"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 100},
         "summary_only": {"type": "boolean",
                          "description": "Return counts without template records"}},
         "required": []}},
    {"type": "function", "name": "create_doc_template",
     "description": ("Create or upgrade a versioned document template that "
                     "defines the required sections for a class of "
                     "mission-critical documents."),
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string",
                  "description": "lowercase_identifier template name"},
         "description": {"type": "string"},
         "sections_json": {"type": "string",
                           "description": "JSON array of required section names, in order"},
         "optional_sections_json": {"type": "string",
                                    "description": "JSON array of optional section names"}},
         "required": ["name", "description", "sections_json"]}},
    {"type": "function", "name": "draft_document",
     "description": ("Create a mission-critical document from a template. "
                     "Provide every required section's markdown body. Mark "
                     "unknown facts [TBD - what's needed] and "
                     "time/jurisdiction-sensitive claims [VERIFY CURRENT]; "
                     "these block finalization until resolved. Set "
                     "finalize=true to produce a sealed FINAL in one pass "
                     "when the user wants an audience-ready document and "
                     "all content is complete. Returns a versioned, hashed "
                     "file."),
     "parameters": {"type": "object", "properties": {
         "template": {"type": "string", "description": "Template name"},
         "title": {"type": "string", "description": "Document title"},
         "sections_json": {"type": "string",
                           "description": "JSON object {section_name: markdown_body}"},
         "tags": {"type": "string",
                  "description": "Optional comma-separated tags"},
         "finalize": {"type": "boolean",
                      "description": "Seal as FINAL immediately (blocked if any TBD/VERIFY markers remain)"}},
         "required": ["template", "title", "sections_json"]}},
    {"type": "function", "name": "revise_document",
     "description": ("Issue a new version of a document. Pass only the "
                     "sections to replace; others carry forward. The prior "
                     "version is marked superseded (never edited in place)."),
     "parameters": {"type": "object", "properties": {
         "doc_id": {"type": "string"},
         "sections_json": {"type": "string",
                           "description": "JSON object of sections to replace"},
         "change_note": {"type": "string",
                         "description": "What changed and why"}},
         "required": ["doc_id", "sections_json"]}},
    {"type": "function", "name": "draft_presentation",
     "description": ("Create a reusable native-presentation source from a "
                     "strict structured slide specification. Use this tool, "
                     "not draft_document, whenever the requested deliverable "
                     "is PowerPoint/PPTX. Supported layouts: title, hero, "
                     "bullets, two_column, comparison, metrics, process, "
                     "cards, table, risk_matrix, timeline, bar_chart, sources, "
                     "closing. Do not substitute DOCX for PPTX."),
     "parameters": {"type": "object", "properties": {
         "title": {"type": "string"},
         "audience": {"type": "string"},
         "subtitle": {"type": "string"},
         "slides_json": {
             "type": "string",
             "description": (
                 "JSON array of slide objects or a full presentation object. "
                 "Every slide requires layout and title."
             ),
         },
         "tags": {"type": "string"},
         "finalize": {
             "type": "boolean",
             "description": "Seal as FINAL when content and evidence are complete",
         }},
         "required": ["title", "audience", "slides_json"]}},
    {"type": "function", "name": "revise_presentation",
     "description": ("Issue a complete new structured presentation version. "
                     "Use this to correct or upgrade an existing governed "
                     "presentation while preserving prior versions."),
     "parameters": {"type": "object", "properties": {
         "doc_id": {"type": "string"},
         "slides_json": {"type": "string"},
         "audience": {"type": "string"},
         "subtitle": {"type": "string"},
         "change_note": {"type": "string"},
         "finalize": {"type": "boolean"}},
         "required": ["doc_id", "slides_json"]}},
    {"type": "function", "name": "get_presentation_spec",
     "description": "Read the normalized slide specification and integrity hash.",
     "parameters": {"type": "object", "properties": {
         "doc_id": {"type": "string"},
         "version": {"type": "integer"}},
         "required": ["doc_id"]}},
    {"type": "function", "name": "finalize_document",
     "description": ("Review gate: seal the latest version as FINAL. Blocks "
                     "on unresolved [TBD]/[VERIFY CURRENT] markers or "
                     "out-of-band file edits. Only finalize with the user's "
                     "explicit approval."),
     "parameters": {"type": "object", "properties": {
         "doc_id": {"type": "string"}}, "required": ["doc_id"]}},
    {"type": "function", "name": "export_document",
     "description": ("Render and register the exact requested deliverable. "
                     "Use format=pptx for PowerPoint requests, which succeeds "
                     "only for a validated structured presentation. A file is "
                     "not complete until this returns artifact.status=ready. "
                     "Finals export clean; drafts are watermarked."),
     "parameters": {"type": "object", "properties": {
         "doc_id": {"type": "string"},
         "format": {"type": "string", "enum": ["html", "docx", "rtf", "pptx"],
                    "description": "Output format (default html)"},
         "version": {"type": "integer",
                     "description": "Specific version (default latest)"}},
         "required": ["doc_id"]}},
    {"type": "function", "name": "list_export_artifacts",
     "description": ("List registered, integrity-verified document artifacts "
                    "without exposing server filesystem paths."),
     "parameters": {"type": "object", "properties": {
         "doc_id": {"type": "string"},
         "version": {"type": "integer"},
         "format": {"type": "string",
                   "enum": ["", "html", "docx", "rtf", "pptx"]}},
         "required": []}},
    {"type": "function", "name": "list_documents",
     "description": "List documents in the DocOps registry by status (draft/final/superseded/all) and keyword.",
     "parameters": {"type": "object", "properties": {
         "status": {"type": "string",
                    "enum": ["draft", "final", "superseded", "all"]},
         "query": {"type": "string",
                   "description": "Keyword in title/tags/doc_id"}},
         "required": []}},
    {"type": "function", "name": "get_document",
     "description": "Read a document's content plus its full version lineage (hashes, change notes).",
     "parameters": {"type": "object", "properties": {
         "doc_id": {"type": "string"},
         "version": {"type": "integer",
                     "description": "Specific version (default latest)"}},
         "required": ["doc_id"]}},
]

DOCOPS_DISPATCH = {
    "list_doc_templates": list_doc_templates,
    "create_doc_template": create_doc_template,
    "draft_document": draft_document,
    "revise_document": revise_document,
    "draft_presentation": draft_presentation,
    "revise_presentation": revise_presentation,
    "get_presentation_spec": get_presentation_spec,
    "finalize_document": finalize_document,
    "export_document": export_document,
    "list_export_artifacts": list_export_artifacts,
    "list_documents": list_documents,
    "get_document": get_document,
}
