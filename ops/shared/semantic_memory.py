"""Semantic search and clustering over PJ's notes, tasks, and uploads."""

from __future__ import annotations

import sqlite3

from ops.shared import embeddings

MAX_BACKFILL_PER_CALL = 200


def delete_memory_vector(memory_id: str, *, db_path=None) -> None:
    """Low-level deletion adapter used by the memory domain."""
    conn = sqlite3.connect(db_path or embeddings.DB_PATH)
    try:
        conn.execute("DELETE FROM semantic_vectors WHERE kind='memory' AND ref_id=?", (memory_id,))
        conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()


def _rows(kind: str, db_path=None):
    conn = sqlite3.connect(db_path or embeddings.DB_PATH)
    try:
        if kind == "notes":
            fetched = conn.execute(
                "SELECT id, topic || ': ' || content FROM notes ORDER BY created_at DESC LIMIT 1000"
            ).fetchall()
        elif kind == "tasks":
            fetched = conn.execute(
                "SELECT id, title || ' ' || COALESCE(notes,'') FROM tasks ORDER BY created_at DESC LIMIT 1000"
            ).fetchall()
        else:
            return []
        return [(ref, text) for ref, text in fetched if (text or "").strip()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _upload_rows():
    from ops.docs import uploads as document_uploads

    listed = document_uploads.list_uploaded_documents(limit=100)
    return [
        (doc["upload_id"] + ":" + doc["saved_path"], doc["name"] + " " + doc["saved_path"])
        for doc in listed.get("documents", [])
    ]


def _candidates(kind: str, client=None, db_path=None):
    rows = _upload_rows() if kind == "uploads" else _rows(kind, db_path)
    pairs = []
    for ref, text in rows[:MAX_BACKFILL_PER_CALL]:
        vector = embeddings.ensure_embedding(kind, str(ref), text, client=client, db_path=db_path)
        pairs.append(((str(ref), text[:200]), vector))
    return pairs


def semantic_search_memory(
    query: str = "", kinds: str = "notes,tasks", limit: int = 8, client=None, db_path=None
) -> dict:
    """Meaning-based search across notes, tasks, and uploads."""
    text = str(query or "").strip()
    if not text:
        return {"error": "query is required"}
    kind_list = [
        k.strip() for k in str(kinds).split(",") if k.strip() in {"notes", "tasks", "uploads"}
    ]
    if not kind_list:
        return {"error": "kinds must include notes, tasks, or uploads"}
    try:
        query_vector = embeddings.embed_texts([text], client=client)[0]
        matches = []
        for kind in kind_list:
            for (ref, snippet), score in embeddings.rank_by_similarity(
                query_vector, _candidates(kind, client=client, db_path=db_path)
            )[: max(1, min(int(limit), 20))]:
                matches.append(
                    {"kind": kind, "ref": ref, "snippet": snippet, "score": round(score, 4)}
                )
        matches.sort(key=lambda item: item["score"], reverse=True)
        return {"count": len(matches[:limit]), "matches": matches[:limit]}
    except Exception as exc:
        return {"error": f"semantic_search_failed: {str(exc)[:200]}"}


def cluster_memory(kind: str = "notes", client=None, db_path=None) -> dict:
    """Group notes, tasks, or uploads into topic clusters by similarity."""
    if kind not in {"notes", "tasks", "uploads"}:
        return {"error": "kind must be notes, tasks, or uploads"}
    try:
        pairs = _candidates(kind, client=client, db_path=db_path)
        if not pairs:
            return {"count": 0, "clusters": []}
        groups = embeddings.cluster_by_threshold(pairs)
        clusters = [
            {
                "size": len(group),
                "items": [{"ref": ref, "snippet": snippet} for ref, snippet in group],
            }
            for group in sorted(groups, key=len, reverse=True)
        ]
        return {"count": len(clusters), "clusters": clusters}
    except Exception as exc:
        return {"error": f"clustering_failed: {str(exc)[:200]}"}


SEMANTIC_SCHEMAS = [
    {
        "type": "function",
        "name": "semantic_search_memory",
        "description": (
            "Meaning-based search across saved notes, tasks, and uploaded "
            "documents. Use when keyword search would miss paraphrases, e.g. "
            "'what did I decide about lender terms?'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kinds": {"type": "string", "description": "comma list: notes,tasks,uploads"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "cluster_memory",
        "description": "Group notes, tasks, or uploads into topic clusters by semantic similarity.",
        "parameters": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["notes", "tasks", "uploads"]}},
            "required": [],
        },
    },
]

SEMANTIC_DISPATCH = {
    "semantic_search_memory": lambda query="", kinds="notes,tasks", limit=8: semantic_search_memory(
        query, kinds, limit
    ),
    "cluster_memory": lambda kind="notes": cluster_memory(kind),
}
