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


def _db():
    conn = sqlite3.connect(_DB_PATH)
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
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "doc"


# --------------------------------------------------------------- templates
def create_doc_template(name: str, description: str, sections_json: str,
                        optional_sections_json: str = "[]") -> dict:
    """Create or upgrade a versioned document template."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        return {"error": "template name must be a lowercase identifier"}
    try:
        sections = json.loads(sections_json)
        optional = json.loads(optional_sections_json or "[]")
        assert isinstance(sections, list) and sections and \
            all(isinstance(s, str) and s.strip() for s in sections)
        assert isinstance(optional, list)
    except Exception:
        return {"error": "sections_json must be a non-empty JSON array of "
                         "section names; optional_sections_json a JSON array"}
    with _db() as conn:
        row = conn.execute("SELECT version FROM docops_templates WHERE name=?",
                           (name,)).fetchone()
        if row:
            conn.execute(
                "UPDATE docops_templates SET version=version+1, description=?,"
                " sections=?, optional_sections=?, updated_at=? WHERE name=?",
                (description, json.dumps(sections), json.dumps(optional),
                 _now(), name))
            version = row[0] + 1
        else:
            conn.execute(
                "INSERT INTO docops_templates "
                "(name, description, sections, optional_sections) "
                "VALUES (?,?,?,?)",
                (name, description, json.dumps(sections),
                 json.dumps(optional)))
            version = 1
    return {"status": "saved", "template": name, "version": version,
            "required_sections": sections, "optional_sections": optional}


def list_doc_templates() -> dict:
    """List available document templates with their required sections."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT name, version, description, sections, optional_sections "
            "FROM docops_templates ORDER BY name").fetchall()
    return {"count": len(rows), "templates": [
        {"name": r[0], "version": r[1], "description": r[2],
         "required_sections": json.loads(r[3]),
         "optional_sections": json.loads(r[4])} for r in rows]}


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
    with _db() as conn:
        tpl = conn.execute(
            "SELECT name, version, description, sections, optional_sections "
            "FROM docops_templates WHERE name=?", (template,)).fetchone()
        if not tpl:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM docops_templates").fetchall()]
            return {"error": f"unknown template '{template}'",
                    "available": names}
        errors = _validate_sections(tpl, sections)
        if errors:
            return {"status": "rejected", "errors": errors}
        doc_id = "DOC-" + str(uuid.uuid4())[:8]
        result = _write_version(conn, doc_id, 1, title, template, tpl[1],
                                sections, tags, "initial draft")
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
