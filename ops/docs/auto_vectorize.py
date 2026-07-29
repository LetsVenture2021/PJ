"""Best-effort vector ingestion of exported documents, deduplicated by content.

Every successful export yields three surfaces: the chat text, the downloadable
artifact, and a vectorized copy added quietly to the configured owner vector
store. A local ledger keyed by content hash and store id prevents re-exporting
the same document version from embedding duplicates. Vectorization never
blocks or fails an export.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3


def _ledger_seen(db_path, sha256: str, store_id: str, *, record: bool = False) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS docops_vector_ingest_ledger ("
            "sha256 TEXT NOT NULL, store_id TEXT NOT NULL, "
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (sha256, store_id))"
        )
        if record:
            conn.execute(
                "INSERT OR IGNORE INTO docops_vector_ingest_ledger (sha256, store_id) "
                "VALUES (?, ?)",
                (sha256, store_id),
            )
            conn.commit()
            return True
        row = conn.execute(
            "SELECT 1 FROM docops_vector_ingest_ledger WHERE sha256=? AND store_id=?",
            (sha256, store_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def vectorize_document_export(doc_id: str, version: int = 0) -> bool:
    try:
        from ops.docs import service
        from ops.realtime.orchestration import load_config

        cfg = load_config()
        store_ids = cfg.get("vector_store_ids") or (
            [cfg["vector_store_id"]] if cfg.get("vector_store_id") else []
        )
        if not store_ids:
            return False
        store_id = store_ids[0]
        document = service.get_document(doc_id, version)
        content = document.get("content") or document.get("markdown") or ""
        if not isinstance(content, str) or not content.strip():
            return False
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if _ledger_seen(service._DB_PATH, digest, store_id):
            return True  # already embedded; identical re-exports are a no-op
        if _near_duplicate(service._DB_PATH, doc_id, content, store_id):
            _ledger_seen(service._DB_PATH, digest, store_id, record=True)
            return True  # semantically near-identical to an embedded version
        from openai import OpenAI

        client = OpenAI()
        blob = io.BytesIO(content.encode("utf-8"))
        blob.name = f"{doc_id}_v{document.get('version', version) or 1}.md"
        uploaded = client.files.create(file=blob, purpose="assistants")
        client.vector_stores.files.create(store_id, file_id=uploaded.id)
        _ledger_seen(service._DB_PATH, digest, store_id, record=True)
        return True
    except Exception:
        return False


def _near_duplicate(db_path, doc_id: str, content: str, store_id: str) -> bool:
    """A re-export with trivial edits should not re-embed the document."""
    try:
        from ops.shared import embeddings

        kind = f"vecdedupe:{store_id}"
        vector = embeddings.embed_texts([content])[0]
        conn = embeddings._conn(db_path)
        try:
            rows = conn.execute(
                "SELECT ref_id, vector FROM semantic_vectors WHERE kind=?", (kind,)
            ).fetchall()
            for ref_id, blob in rows:
                if (
                    ref_id != doc_id
                    and embeddings.cosine(vector, embeddings._unpack(blob))
                    >= embeddings.NEAR_DUPLICATE_SIMILARITY
                ):
                    return True
            import hashlib as _hashlib

            conn.execute(
                "INSERT OR REPLACE INTO semantic_vectors (kind, ref_id, content_sha, vector)"
                " VALUES (?,?,?,?)",
                (
                    kind,
                    doc_id,
                    _hashlib.sha256(content.encode()).hexdigest(),
                    embeddings._pack(vector),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return False
    return False
