"""Owner consent, lifecycle, retrieval, and export policy for durable memory."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ops.shared import embeddings

from .models import SENSITIVE_CATEGORIES, content_hash, normalize_proposal, utc_now
from .store import MemoryStore
from .extraction import extract_proposals


class MemoryService:
    def __init__(self, database_path, *, enabled=True, automatic_ui_preferences=False):
        self.store = MemoryStore(database_path)
        self.enabled = enabled
        self.automatic_ui_preferences = automatic_ui_preferences

    def after_turn(self, turn_text, *, provider, config, source_ref, project_scope):
        """Optionally extract bounded proposals after a completed turn."""
        if not self.enabled or not config.get("extraction_enabled", False):
            return []
        maximum = max(1, min(int(config.get("max_proposals_per_turn", 3)), 10))
        model = config.get("model")
        if not isinstance(model, str) or not model.strip():
            return []
        proposals = extract_proposals(
            provider,
            turn_text,
            model=model,
            source_ref=source_ref,
            project_scope=project_scope,
            maximum=maximum,
        )
        created = []
        for proposal in proposals:
            auto = (
                self.automatic_ui_preferences
                and proposal["category"] == "ui_preference"
                and proposal["category"] not in SENSITIVE_CATEGORIES
            )
            created.append(self.store.insert(proposal, status="accepted" if auto else "proposed"))
        return created

    def propose(self, value, *, source_ref, project_scope):
        proposal = normalize_proposal(value, source_ref=source_ref, project_scope=project_scope)
        auto = (
            self.automatic_ui_preferences
            and proposal["category"] == "ui_preference"
            and proposal["category"] not in SENSITIVE_CATEGORIES
        )
        return self.store.insert(proposal, status="accepted" if auto else "proposed")

    def accept(self, memory_id, *, edited_content=None):
        row = self.store.get(memory_id)
        if not row or row["status"] != "proposed":
            raise ValueError("proposal is not pending")
        fields: dict[str, Any] = {"status": "accepted"}
        if edited_content is not None:
            clean = " ".join(str(edited_content).split())
            normalize_proposal(
                {
                    "content": clean,
                    "category": row["category"],
                    "confidence": row["confidence"],
                    "source_type": row["source_type"],
                },
                source_ref=row["source_ref"],
                project_scope=row["project_scope"],
            )
            fields.update(content=clean, content_hash=content_hash(clean))
        return self.store.update(memory_id, **fields)

    def reject(self, memory_id):
        return self.store.update(memory_id, status="rejected")

    def pin(self, memory_id, pinned=True):
        return self.store.update(memory_id, pinned=int(bool(pinned)))

    def expire(self, memory_id, when=None):
        return self.store.update(memory_id, expires_at=when or utc_now())

    def forget(self, memory_id):
        return self.store.delete(memory_id)

    def bulk_delete(self, ids):
        return {mid: self.forget(mid) for mid in ids[:100]}

    def correct(self, memory_id, content):
        old = self.store.get(memory_id)
        if not old or old["status"] != "accepted":
            raise ValueError("only accepted memories can be corrected")
        proposal = normalize_proposal(
            {
                "content": content,
                "category": old["category"],
                "confidence": old["confidence"],
                "source_type": "owner",
            },
            source_ref=old["source_ref"],
            project_scope=old["project_scope"],
        )
        return self.store.insert(proposal, status="accepted", supersedes_id=memory_id)

    def retrieve(self, query, *, project_scope, limit=8, client=None):
        if not self.enabled or not self.store.setting("enabled", True):
            return []
        disabled = set(self.store.setting("disabled_categories", []))
        now = utc_now()
        rows = [
            r
            for r in self.store.list(status="accepted", project_scope=project_scope)
            if r["category"] not in disabled and (not r["expires_at"] or r["expires_at"] > now)
        ]
        semantic = {}
        if query and rows and client is not None:
            qv = embeddings.embed_texts([query], client=client)[0]
            semantic = dict(
                embeddings.rank_by_similarity(
                    qv,
                    [
                        (
                            r["id"],
                            embeddings.ensure_embedding(
                                "memory",
                                r["id"],
                                r["content"],
                                client=client,
                                db_path=self.store.path,
                            ),
                        )
                        for r in rows
                    ],
                )
            )
        authority = {"owner": 3, "tool": 2, "assistant": 1, "untrusted": 0}

        def score(r):
            age = (
                datetime.fromisoformat(r["created_at"]).replace(tzinfo=timezone.utc)
                if "+" not in r["created_at"]
                else datetime.fromisoformat(r["created_at"])
            )
            recency = 1 / (1 + max(0, (datetime.now(timezone.utc) - age).days))
            return (
                4 * int(r["pinned"])
                + authority.get(r["source_type"], 0)
                + recency
                + 3 * semantic.get(r["id"], 0)
            )

        result = []
        for row in sorted(rows, key=score, reverse=True)[: max(1, min(limit, 20))]:
            reasons = ["project_scope", "accepted", "not_expired"]
            if row["pinned"]:
                reasons.append("pinned")
            if row["source_type"] == "owner":
                reasons.append("owner_authority")
            if row["id"] in semantic:
                reasons.append("semantic_match")
            result.append(
                {"memory_id": row["id"], "content": row["content"], "reason_codes": reasons}
            )
        return result

    def export(self):
        return {
            "exported_at": utc_now(),
            "memories": self.store.list(),
            "settings": {
                "enabled": self.store.setting("enabled", True),
                "disabled_categories": self.store.setting("disabled_categories", []),
            },
        }
