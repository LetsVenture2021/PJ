"""Source normalization and atomic, bounded link validation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from ops.shared.interfaces import HttpProvider

from .models import SourceRecord, utc_now
from .safety import canonical_url, local_identity

MAX_SOURCES = 50
MAX_SOURCE_BYTES = 2 * 1024 * 1024
LINK_TIMEOUT_SECONDS = 5


def normalize_web_source(
    source_id: str,
    url: str,
    content: bytes,
    *,
    title: str,
    publisher: str | None = None,
    retrieval_method: str = "http",
    trust_class: str = "unreviewed",
    published_at: str | None = None,
    updated_at: str | None = None,
) -> SourceRecord:
    if len(content) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds byte limit")
    return SourceRecord(
        source_id,
        canonical_url(url),
        title,
        publisher,
        utc_now(),
        hashlib.sha256(content).hexdigest(),
        trust_class,
        retrieval_method,
        published_at,
        updated_at,
        len(content),
    )


def normalize_local_source(
    source_id: str,
    path: str | Path,
    content: bytes,
    *,
    title: str | None = None,
    owner: str | None = None,
    retrieval_method: str = "local_artifact",
    trust_class: str = "owner",
) -> SourceRecord:
    if len(content) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds byte limit")
    digest = hashlib.sha256(content).hexdigest()
    name = Path(path).name
    return SourceRecord(
        source_id,
        local_identity(name, digest),
        title or name,
        owner,
        utc_now(),
        digest,
        trust_class,
        retrieval_method,
        byte_count=len(content),
    )


def enforce_source_limits(contents: Iterable[bytes]) -> list[bytes]:
    values = list(contents)
    if len(values) > MAX_SOURCES:
        raise ValueError("source count exceeds limit")
    if any(len(value) > MAX_SOURCE_BYTES for value in values):
        raise ValueError("source exceeds byte limit")
    return values


def check_broken_links(sources: Iterable[SourceRecord], http: HttpProvider) -> dict[str, str]:
    """Check public links without redirects, streaming bodies, or retries."""
    results = {}
    for index, source in enumerate(sources):
        if index >= MAX_SOURCES or not source.identity.startswith(("http://", "https://")):
            continue
        try:
            url = canonical_url(source.identity)
            response = http.get(
                url,
                timeout=LINK_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
                headers={"Range": "bytes=0-0"},
            )
            results[source.id] = "ok" if 200 <= response.status_code < 400 else "broken"
            response.close()
        except (ValueError, *http.request_errors, *http.timeout_errors):
            results[source.id] = "broken"
    return results
