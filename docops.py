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
import re
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

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
}


def _normalize_alias_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


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

    # 2) fenced JSON block
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    # 3) simple key-value parser with list support
    item = {}
    current_key = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        if ":" in line and not line.startswith("- "):
            k, v = line.split(":", 1)
            key = re.sub(r"[^a-z0-9]+", "_", k.lower()).strip("_")
            value = v.strip()
            if value:
                item[key] = value
                current_key = key
            else:
                item[key] = []
                current_key = key
            continue
        if line.startswith("- ") and current_key:
            current_val = item.get(current_key)
            if not isinstance(current_val, list):
                current_val = _as_list(current_val)
            current_val.append(line[2:].strip())
            item[current_key] = current_val
    return item


def _extract_item_blocks(knowledge_pack_text: str) -> list:
    matches = re.findall(
        r"---ITEM_START\b(.*?)---ITEM_END",
        knowledge_pack_text or "",
        flags=re.S | re.I,
    )
    return [m.strip() for m in matches if m.strip()]


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


def list_doc_templates() -> dict:
    """List available document templates with their required sections."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT name, version, description, sections, optional_sections "
            "FROM docops_templates ORDER BY name").fetchall()
        alias_rows = conn.execute(
            "SELECT template_name, alias_raw FROM docops_template_aliases "
            "ORDER BY template_name, alias_raw"
        ).fetchall()
    alias_map = {}
    for template_name, alias_raw in alias_rows:
        alias_map.setdefault(template_name, []).append(alias_raw)
    return {"count": len(rows), "templates": [
        {"name": r[0], "version": r[1], "description": r[2],
         "required_sections": json.loads(r[3]),
         "optional_sections": json.loads(r[4]),
         "aliases": alias_map.get(r[0], [])}
        for r in rows]}


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
    blocks = _extract_item_blocks(knowledge_pack_text or "")
    if not blocks:
        return {
            "status": "no_items_found",
            "items_total": 0,
            "templates_created": 0,
            "templates_updated": 0,
            "aliases_registered": 0,
            "errors": [],
        }

    created = updated = alias_count = 0
    skipped_existing = skipped_provisional = skipped_invalid = 0
    imported = []
    errors = []
    with _db() as conn:
        for idx, block in enumerate(blocks, start=1):
            item = _parse_knowledge_pack_block(block)
            item_id = str(_item_value(
                item, "item_id", "id", "spec_id", "template_id", "item")).strip()
            title = str(_item_value(
                item, "canonical_title", "title", "template_title", "name")).strip()
            explicit_template = str(_item_value(
                item, "template_name", "template", "slug")).strip()
            description = str(_item_value(
                item, "description", "summary", "purpose")).strip()

            required = _as_list(_item_value(
                item, "required_sections", "sections", "required", "required_section_names"))
            optional = _as_list(_item_value(
                item, "optional_sections", "optional", "optional_section_names"))
            if not required:
                required = _dedupe_nonempty(
                    [m.group(1).strip()
                     for m in re.finditer(r"^##\s+(.+?)\s*$", block, re.M)]
                )
            optional = [s for s in _dedupe_nonempty(optional)
                        if s not in set(required)]
            provisional = _is_truthy(_item_value(item, "provisional", "is_provisional")) \
                or str(_item_value(item, "status", "stage")).strip().lower() in {
                    "provisional", "draft", "experimental", "wip"
                }
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
                [template_name, item_id, title] +
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
    return result


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
        return _write_version(conn, doc_id, version + 1, title, template,
                              tpl_version, current, tags,
                              change_note or "revision")


def finalize_document(doc_id: str) -> dict:
    """Review gate: mark the latest version FINAL and seal its hash.

    Blocks if unresolved [TBD]/[VERIFY CURRENT]/{{placeholder}}/TODO
    markers remain, or if the file was modified outside DocOps.
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT version, path, sha256, status FROM docops_documents "
            "WHERE doc_id=? ORDER BY version DESC LIMIT 1",
            (doc_id,)).fetchone()
        if not row:
            return {"error": f"unknown doc_id '{doc_id}'"}
        version, path, sha, status = row
        if status == "final":
            return {"doc_id": doc_id, "version": version,
                    "status": "already_final"}
        p = Path(path)
        if not p.exists():
            return {"error": f"file missing: {path}"}
        content = p.read_text()
        if _hash(content) != sha:
            return {"status": "blocked",
                    "reason": "file was modified outside DocOps; "
                              "use revise_document to issue a new version"}
        markers = sorted({m for m in BLOCKING_MARKERS if m in content})
        if markers:
            return {"status": "blocked", "unresolved_markers": markers,
                    "reason": "resolve markers via revise_document first"}
        content = content.replace("**Status:** DRAFT",
                                  "**Status:** FINAL", 1)
        p.write_text(content)
        new_sha = _hash(content)
        conn.execute(
            "UPDATE docops_documents SET status='final', sha256=?, "
            "finalized_at=? WHERE doc_id=? AND version=?",
            (new_sha, _now(), doc_id, version))
    return {"doc_id": doc_id, "version": version, "status": "final",
            "sha256": new_sha, "path": path}


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


def export_document(doc_id: str, format: str = "html",
                    version: int = 0) -> dict:
    """Render an audience-ready deliverable of a document.

    Formats: html (styled, print-to-PDF ready), docx, rtf. The internal
    metadata banner is removed; finals get a discreet integrity footer,
    non-final versions get a DRAFT watermark so a rough cut can never be
    mistaken for the finished piece.
    """
    if format not in ("html", "docx", "rtf"):
        return {"error": "format must be one of: html, docx, rtf"}
    if _markdown is None:
        return {"error": "python 'markdown' package not installed in venv"}
    with _db() as conn:
        history = conn.execute(
            "SELECT version, status, path, title, sha256 "
            "FROM docops_documents WHERE doc_id=? ORDER BY version",
            (doc_id,)).fetchall()
    if not history:
        return {"error": f"unknown doc_id '{doc_id}'"}
    target = next((h for h in history if h[0] == version), history[-1])
    ver, status, path, title, sha = target
    p = Path(path)
    if not p.exists():
        return {"error": f"file missing: {path}"}
    text = p.read_text()
    if _hash(text) != sha:
        return {"status": "blocked",
                "reason": "file was modified outside DocOps; "
                          "revise_document to issue a clean version"}

    # Strip internal metadata banner and title (re-rendered cleanly).
    body_md = re.sub(r"^# .*?\n+> \*\*Doc:\*\*.*?\n+", "", text, count=1,
                     flags=re.S)
    body_html = _markdown.markdown(body_md,
                                   extensions=["tables", "fenced_code"])
    date = datetime.now(timezone.utc).strftime("%B %d, %Y")
    is_final = status == "final"
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

    stem = f"{doc_id}-{_slug(title)}-v{ver}" + ("" if is_final else "-DRAFT")
    html_path = EXPORTS_DIR / f"{stem}.html"
    html_path.write_text(page)
    out_path = html_path
    if format in ("docx", "rtf"):
        out_path = EXPORTS_DIR / f"{stem}.{format}"
        conv = subprocess.run(
            ["textutil", "-convert", format, str(html_path),
             "-output", str(out_path)],
            capture_output=True, text=True, timeout=60)
        if conv.returncode != 0 or not out_path.exists():
            return {"error": "textutil conversion failed",
                    "detail": conv.stderr.strip()[:300],
                    "html_fallback": str(html_path)}
    return {"doc_id": doc_id, "version": ver, "format": format,
            "audience_ready": is_final,
            "watermarked_draft": not is_final,
            "path": str(out_path),
            "note": ("clean final export" if is_final else
                     "non-final version exported with DRAFT watermark; "
                     "finalize_document first for a clean deliverable")}


# ------------------------------------------------------------ tool schemas
DOCOPS_SCHEMAS = [
    {"type": "function", "name": "list_doc_templates",
     "description": ("List DocOps document templates (name, required and "
                     "optional sections). Always check before drafting."),
     "parameters": {"type": "object", "properties": {}, "required": []}},
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
    {"type": "function", "name": "finalize_document",
     "description": ("Review gate: seal the latest version as FINAL. Blocks "
                     "on unresolved [TBD]/[VERIFY CURRENT] markers or "
                     "out-of-band file edits. Only finalize with the user's "
                     "explicit approval."),
     "parameters": {"type": "object", "properties": {
         "doc_id": {"type": "string"}}, "required": ["doc_id"]}},
    {"type": "function", "name": "export_document",
     "description": ("Render an audience-ready deliverable of a document: "
                     "professionally styled HTML (print-to-PDF ready), DOCX "
                     "or RTF. Finals export clean; non-final versions get a "
                     "DRAFT watermark. Use after finalize_document to hand "
                     "the user a shareable file."),
     "parameters": {"type": "object", "properties": {
         "doc_id": {"type": "string"},
         "format": {"type": "string", "enum": ["html", "docx", "rtf"],
                    "description": "Output format (default html)"},
         "version": {"type": "integer",
                     "description": "Specific version (default latest)"}},
         "required": ["doc_id"]}},
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
    "finalize_document": finalize_document,
    "export_document": export_document,
    "list_documents": list_documents,
    "get_document": get_document,
}
