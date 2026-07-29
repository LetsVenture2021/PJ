"""Best-effort vector ingestion of exported documents.

Every successful export yields three surfaces: the chat text, the downloadable
artifact, and a vectorized copy added quietly to the configured owner vector
store. Vectorization never blocks or fails an export; failures are reported as
a boolean on the export result only.
"""

from __future__ import annotations

import io


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
        document = service.get_document(doc_id, version)
        content = document.get("content") or document.get("markdown") or ""
        if not isinstance(content, str) or not content.strip():
            return False
        from openai import OpenAI

        client = OpenAI()
        blob = io.BytesIO(content.encode("utf-8"))
        blob.name = f"{doc_id}_v{document.get('version', version) or 1}.md"
        uploaded = client.files.create(file=blob, purpose="assistants")
        client.vector_stores.files.create(store_ids[0], file_id=uploaded.id)
        return True
    except Exception:
        return False
