#!/usr/bin/env python3
"""PJ MCP server: expose PJ's local knowledge to MCP clients over stdio.

Dependency-free stdio transport mirroring huggingface_mcp_server.py. Provides
the search/fetch interface (compatible with deep-research-style consumers)
over PJ's notes, tasks, and uploaded documents, plus task listing. Read-only:
no tool mutates PJ state, and secrets are never accepted as input.

Run: python pj_mcp_server.py  (requires OPENAI_API_KEY for semantic search;
falls back to keyword search without it.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

SERVER_NAME = "pj-knowledge"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"
MAX_RESULTS = 20


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "search",
        "description": (
            "Search PJ's notes, tasks, and uploaded documents. Semantic when "
            "embeddings are available, keyword otherwise. Returns ids for fetch."
        ),
        "inputSchema": _schema(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS, "default": 8},
            },
            ["query"],
        ),
    },
    {
        "name": "fetch",
        "description": "Fetch one search result by id (note:<id>, task:<id>, or upload:<UPL-id>).",
        "inputSchema": _schema(
            {"id": {"type": "string", "minLength": 3, "maxLength": 300}}, ["id"]
        ),
    },
    {
        "name": "list_open_tasks",
        "description": "List PJ's open tasks.",
        "inputSchema": _schema({}),
    },
]


def _search(query: str, limit: int) -> list[dict]:
    from ops.shared.semantic_memory import semantic_search_memory

    result = semantic_search_memory(query, kinds="notes,tasks,uploads", limit=limit)
    if not result.get("error"):
        return [
            {
                "id": f"{m['kind'][:-1] if m['kind'] != 'uploads' else 'upload'}:{m['ref'].split(':')[0]}",
                "title": m["snippet"][:120],
                "score": m["score"],
            }
            for m in result["matches"]
        ]
    import skills

    keyword = skills.dispatch("search_notes", {"query": query})
    return [
        {"id": f"note:{note['id']}", "title": note["topic"], "score": None}
        for note in keyword.get("notes", [])[:limit]
    ]


def _fetch(ref: str) -> dict:
    kind, _, ident = str(ref).partition(":")
    import sqlite3

    from ops.shared.embeddings import DB_PATH

    if kind == "note":
        row = (
            sqlite3.connect(DB_PATH)
            .execute("SELECT topic, content, created_at FROM notes WHERE id=?", (ident,))
            .fetchone()
        )
        if row:
            return {"id": ref, "title": row[0], "text": row[1], "created_at": row[2]}
    elif kind == "task":
        row = (
            sqlite3.connect(DB_PATH)
            .execute("SELECT title, notes, priority, status FROM tasks WHERE id=?", (ident,))
            .fetchone()
        )
        if row:
            return {
                "id": ref,
                "title": row[0],
                "text": row[1] or "",
                "priority": row[2],
                "status": row[3],
            }
    elif kind == "upload":
        from ops.docs import uploads as document_uploads

        record = document_uploads.get_uploaded_document(ident)
        if not record.get("error"):
            return {
                "id": ref,
                "title": record.get("name", ident),
                "text": (record.get("content") or "")[:20000],
                "metadata": {
                    "saved_path": record.get("saved_path"),
                    "sha256": record.get("sha256"),
                },
            }
    return {"error": f"unknown id '{ref}'"}


def call_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "search":
        return {"results": _search(str(args.get("query", "")), int(args.get("limit", 8)))}
    if name == "fetch":
        return _fetch(str(args.get("id", "")))
    if name == "list_open_tasks":
        import skills

        return skills.dispatch("list_tasks", {"status": "open"})
    raise ValueError(f"unknown tool '{name}'")


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            payload = call_tool(str(params.get("name")), params.get("arguments") or {})
            return _result(
                request_id,
                {"content": [{"type": "text", "text": json.dumps(payload, default=str)}]},
            )
        except Exception as exc:
            return _error(request_id, -32000, str(exc)[:300])
    if request_id is None:
        return None
    return _error(request_id, -32601, f"method not found: {method}")


def serve() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        response = handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    serve()
