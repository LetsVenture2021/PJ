"""Shared semantic embedding layer over the local SQLite store.

Embeds short texts with text-embedding-3-small at 256 dimensions (shortened
per the provider's guidance, then L2-normalized so cosine similarity is a dot
product), cached by content hash in a local table. Pure-Python vector math -
the corpora here are hundreds of rows, not millions.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
import struct
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "pj_data.sqlite3"
MODEL = "text-embedding-3-small"
DIMENSIONS = 256
MAX_TEXT_CHARS = 6000
NEAR_DUPLICATE_SIMILARITY = 0.97


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _conn(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS semantic_vectors ("
        "kind TEXT NOT NULL, ref_id TEXT NOT NULL, content_sha TEXT NOT NULL, "
        "vector BLOB NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY (kind, ref_id))"
    )
    return conn


def embed_texts(texts: list[str], client=None) -> list[list[float]]:
    if client is None:
        from openai import OpenAI

        client = OpenAI()
    response = client.embeddings.create(
        model=MODEL,
        input=[text[:MAX_TEXT_CHARS] for text in texts],
        dimensions=DIMENSIONS,
    )
    return [_normalize(item.embedding) for item in response.data]


def ensure_embedding(
    kind: str, ref_id: str, text: str, *, client=None, db_path=None
) -> list[float]:
    """Return the cached vector for (kind, ref_id), re-embedding when text changed."""
    sha = hashlib.sha256(text[:MAX_TEXT_CHARS].encode("utf-8")).hexdigest()
    conn = _conn(db_path)
    try:
        row = conn.execute(
            "SELECT content_sha, vector FROM semantic_vectors WHERE kind=? AND ref_id=?",
            (kind, ref_id),
        ).fetchone()
        if row and row[0] == sha:
            return _unpack(row[1])
        vector = embed_texts([text], client=client)[0]
        conn.execute(
            "INSERT OR REPLACE INTO semantic_vectors (kind, ref_id, content_sha, vector) "
            "VALUES (?,?,?,?)",
            (kind, ref_id, sha, _pack(vector)),
        )
        conn.commit()
        return vector
    finally:
        conn.close()


def rank_by_similarity(query_vector, candidates):
    """candidates: [(ref, vector)] -> [(ref, score)] descending."""
    scored = [(ref, cosine(query_vector, vector)) for ref, vector in candidates]
    return sorted(scored, key=lambda item: item[1], reverse=True)


def cluster_by_threshold(items, *, threshold: float = 0.55) -> list[list]:
    """Greedy agglomerative clustering: items are (ref, vector) pairs."""
    clusters: list[dict] = []
    for ref, vector in items:
        best, best_score = None, threshold
        for cluster in clusters:
            score = cosine(vector, cluster["centroid"])
            if score > best_score:
                best, best_score = cluster, score
        if best is None:
            clusters.append({"refs": [ref], "vectors": [vector], "centroid": list(vector)})
        else:
            best["refs"].append(ref)
            best["vectors"].append(vector)
            size = len(best["vectors"])
            best["centroid"] = _normalize(
                [sum(vec[i] for vec in best["vectors"]) / size for i in range(len(vector))]
            )
    return [cluster["refs"] for cluster in clusters]
