"""Deep research: background analyst reports over web, stores, and code.

start_deep_research launches a background run (these take tens of minutes and
cost real money, so runs are capped per day and bounded by max_tool_calls);
get_deep_research polls it and, on completion, persists the report as a
registered downloadable document and returns the text. Web search and the
configured owner vector stores are the data sources; per the provider's
guidance, prompts should arrive fully formed - PJ's own refinement is the
rewriting step.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_MODEL = "o4-mini-deep-research"
MAX_TOOL_CALLS = 60
_RESPONSE_ID = re.compile(r"^resp_[A-Za-z0-9]{10,}$")


def _research_tools():
    from ops.realtime.orchestration import load_config

    cfg = load_config()
    tools = [{"type": "web_search_preview"}]
    store_ids = cfg.get("vector_store_ids") or (
        [cfg["vector_store_id"]] if cfg.get("vector_store_id") else []
    )
    if store_ids:
        tools.append({"type": "file_search", "vector_store_ids": store_ids[:2]})
    tools.append({"type": "code_interpreter", "container": {"type": "auto"}})
    return tools


def start_deep_research(prompt: str = "", client=None) -> dict:
    task = str(prompt or "").strip()
    if len(task) < 20:
        return {"error": "prompt must be a fully formed research brief (20+ chars)"}
    from ops.shared.spend_guard import check_and_count

    guard_error = check_and_count(BASE_DIR / "pj_data.sqlite3", "deep_research")
    if guard_error:
        return {"error": guard_error}
    if client is None:
        from openai import OpenAI

        client = OpenAI()
    try:
        response = client.responses.create(
            model=DEFAULT_MODEL,
            input=task,
            background=True,
            max_tool_calls=MAX_TOOL_CALLS,
            tools=_research_tools(),
        )
    except Exception as exc:
        return {"error": f"deep_research_start_failed: {str(exc)[:200]}"}
    return {
        "status": "started",
        "response_id": response.id,
        "detail": (
            "Deep research runs take ten minutes or more. Poll with "
            "get_deep_research when the owner asks for status or results."
        ),
    }


def get_deep_research(response_id: str = "", client=None) -> dict:
    if not _RESPONSE_ID.fullmatch(str(response_id or "")):
        return {"error": "response_id must look like resp_..."}
    if client is None:
        from openai import OpenAI

        client = OpenAI()
    try:
        response = client.responses.retrieve(response_id)
    except Exception as exc:
        return {"error": f"deep_research_poll_failed: {str(exc)[:200]}"}
    status = getattr(response, "status", "unknown")
    if status != "completed":
        return {"status": status, "response_id": response_id}
    text = getattr(response, "output_text", "") or ""
    saved = _persist_report(response_id, text)
    return {
        "status": "completed",
        "response_id": response_id,
        "report": text[:20000],
        "report_truncated": len(text) > 20000,
        **saved,
    }


def _persist_report(response_id: str, text: str) -> dict:
    try:
        from ops.docs import uploads as document_uploads
        from ops.shared.io import sha256_file

        if not text.strip():
            return {}
        session_id = f"research_{secrets.token_hex(8)}"
        upload_id = f"UPL-{secrets.token_hex(16)}"
        name = f"deep_research_{response_id[-8:]}.md"
        target_dir = document_uploads.UPLOADS_DIR / session_id / upload_id
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / name
        destination.write_text(text, encoding="utf-8")
        destination.chmod(0o600)
        registered = document_uploads.register_uploaded_documents(
            upload_id,
            session_id,
            [
                {
                    "saved_path": f"uploads/{session_id}/{upload_id}/{name}",
                    "path": destination,
                    "name": name,
                    "mime": "text/markdown",
                    "size": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                }
            ],
        )
        return {"saved_report": registered["documents"][0]["saved_path"]}
    except Exception:
        return {}


RESEARCH_SCHEMAS = [
    {
        "type": "function",
        "name": "start_deep_research",
        "description": (
            "Launch a long-running deep research analyst report (web search, "
            "owner knowledge stores, data analysis). Takes ten or more "
            "minutes; use for multi-source research reports, market and "
            "legal analysis - not quick lookups. Write a fully formed brief: "
            "scope, dimensions, source preferences, output format."
        ),
        "parameters": {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        },
    },
    {
        "type": "function",
        "name": "get_deep_research",
        "description": (
            "Check a deep research run. When complete, returns the report and "
            "saves it as a downloadable document."
        ),
        "parameters": {
            "type": "object",
            "properties": {"response_id": {"type": "string"}},
            "required": ["response_id"],
        },
    },
]

RESEARCH_DISPATCH = {
    "start_deep_research": lambda prompt="": start_deep_research(prompt),
    "get_deep_research": lambda response_id="": get_deep_research(response_id),
}
