"""Learn reusable design, marketing, and consulting patterns from public sites.

Website content is untrusted evidence.  This module extracts bounded structural
signals; it never executes scripts or treats page copy as instructions.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import sqlite3
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from ops.shared.interfaces import HttpProvider
from ops.shared.providers import RequestsHttpProvider

_ROOT = Path(__file__).resolve().parents[2]
_DB_PATH = _ROOT / "pj_data.sqlite3"
_MAX_BYTES = 1_000_000
_MAX_REDIRECTS = 5
_FOCI = {"all", "design", "marketing", "consulting"}


class _PageSignals(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.headings: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.forms = 0
        self.images = 0
        self.images_with_alt = 0
        self._capture = ""
        self._text: list[str] = []
        self._attrs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        tag = tag.casefold()
        if tag == "meta" and values.get("name", "").casefold() == "description":
            self.description = values.get("content", "").strip()[:500]
        if tag == "title" or tag in {"h1", "h2", "h3"} or tag == "a":
            self._capture, self._text, self._attrs = tag, [], values
        if tag == "form":
            self.forms += 1
        if tag == "img":
            self.images += 1
            if values.get("alt", "").strip():
                self.images_with_alt += 1

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag != self._capture:
            return
        text = " ".join(" ".join(self._text).split())[:300]
        if tag == "title":
            self.title = text
        elif tag in {"h1", "h2", "h3"} and text:
            self.headings.append({"level": tag, "text": text})
        elif tag == "a" and text:
            self.links.append({"text": text, "href": self._attrs.get("href", "")[:500]})
        self._capture, self._text, self._attrs = "", [], {}


def _safe_url(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate.startswith(("http://", "https://")):
        candidate = "https://" + candidate
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        port = parsed.port
    except ValueError:
        return ""
    host = parsed.hostname.casefold().rstrip(".")
    rendered_host = f"[{host}]" if ":" in host else host
    if port:
        rendered_host += f":{port}"
    return urlunsplit((parsed.scheme, rendered_host, parsed.path or "/", parsed.query, ""))


def _display_url(value: str) -> str:
    """Return a stable source locator without credentials or query secrets."""
    safe = _safe_url(value)
    if not safe:
        return ""
    parsed = urlsplit(safe)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _public_destination(url: str) -> bool:
    """Fail closed for local/private destinations, including DNS rebinding candidates."""
    host = urlsplit(url).hostname or ""
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        return False
    if not addresses:
        return False
    return all(ipaddress.ip_address(address).is_global for address in addresses)


def _fetch(url: str, provider: HttpProvider) -> tuple[str, str] | dict[str, str]:
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if not _public_destination(current):
            return {"error": "website resolves to a non-public destination"}
        try:
            response = provider.get(
                current,
                headers={"User-Agent": "PJ-Website-Learner/1.0", "Accept": "text/html"},
                timeout=15,
                allow_redirects=False,
                stream=True,
            )
        except provider.request_errors as exc:
            return {"error": f"website fetch failed: {type(exc).__name__}"}
        status = int(response.status_code)
        if status in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location", "")
            current = _safe_url(urljoin(current, location))
            if not current:
                return {"error": "website returned an invalid redirect"}
            continue
        if not 200 <= status < 300:
            return {"error": f"website returned HTTP {status}"}
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].casefold()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            return {"error": "website did not return HTML"}
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(16_384):
            size += len(chunk)
            if size > _MAX_BYTES:
                return {"error": "website HTML exceeds the 1 MB learning limit"}
            chunks.append(chunk)
        response.encoding = response.encoding or "utf-8"
        return current, b"".join(chunks).decode(response.encoding, "replace")
    return {"error": "website redirected too many times"}


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term in lowered for term in terms)


def _analyze(parser: _PageSignals, focus: str) -> dict[str, list[dict[str, Any]]]:
    headings = parser.headings[:30]
    heading_text = " ".join(item["text"] for item in headings)
    link_text = " ".join(item["text"] for item in parser.links[:100])
    all_copy = f"{parser.title} {parser.description} {heading_text} {link_text}"
    result: dict[str, list[dict[str, Any]]] = {}
    if focus in {"all", "design"}:
        result["design"] = [
            {
                "pattern": "clear_information_hierarchy",
                "observed": bool(parser.title and any(h["level"] == "h1" for h in headings)),
                "evidence": {
                    "title": parser.title,
                    "h1": [h["text"] for h in headings if h["level"] == "h1"][:3],
                },
            },
            {
                "pattern": "accessible_image_labels",
                "observed": parser.images == 0 or parser.images_with_alt == parser.images,
                "evidence": {"images": parser.images, "with_alt": parser.images_with_alt},
            },
        ]
    if focus in {"all", "marketing"}:
        result["marketing"] = [
            {
                "pattern": "specific_call_to_action",
                "observed": _contains(
                    link_text,
                    ("get started", "book", "contact", "buy", "try", "request", "schedule"),
                ),
                "evidence": {
                    "calls_to_action": [
                        link["text"]
                        for link in parser.links
                        if _contains(
                            link["text"],
                            ("get started", "book", "contact", "buy", "try", "request", "schedule"),
                        )
                    ][:8]
                },
            },
            {
                "pattern": "search_result_value_proposition",
                "observed": bool(parser.description),
                "evidence": {"meta_description": parser.description},
            },
            {
                "pattern": "trust_or_proof",
                "observed": _contains(
                    all_copy,
                    ("testimonial", "case stud", "trusted by", "results", "customer", "client"),
                ),
                "evidence": {
                    "matching_headings": [
                        h["text"]
                        for h in headings
                        if _contains(
                            h["text"], ("testimonial", "case stud", "results", "customer", "client")
                        )
                    ][:5]
                },
            },
        ]
    if focus in {"all", "consulting"}:
        result["consulting"] = [
            {
                "pattern": "problem_and_outcome_framing",
                "observed": _contains(
                    all_copy, ("challenge", "problem", "outcome", "impact", "result", "solution")
                ),
                "evidence": {
                    "matching_headings": [
                        h["text"]
                        for h in headings
                        if _contains(
                            h["text"],
                            ("challenge", "problem", "outcome", "impact", "result", "solution"),
                        )
                    ][:5]
                },
            },
            {
                "pattern": "low_friction_discovery_path",
                "observed": parser.forms > 0
                or _contains(link_text, ("book", "contact", "schedule", "consult")),
                "evidence": {"forms": parser.forms},
            },
        ]
    return result


def _persist(source_url: str, content_hash: str, focus: str, insights: dict[str, Any]) -> str:
    learned_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS website_learning (
            source_url TEXT NOT NULL, content_sha256 TEXT NOT NULL, focus TEXT NOT NULL,
            insights_json TEXT NOT NULL, learned_at TEXT NOT NULL,
            PRIMARY KEY (source_url, content_sha256, focus))""")
        conn.execute(
            "INSERT OR REPLACE INTO website_learning VALUES (?,?,?,?,?)",
            (source_url, content_hash, focus, json.dumps(insights, sort_keys=True), learned_at),
        )
    return learned_at


def learn_from_website(
    url: str,
    focus: str = "all",
    persist: bool = True,
    *,
    http_provider: HttpProvider | None = None,
) -> dict[str, Any]:
    """Extract and optionally retain evidence-backed website patterns."""
    safe_url = _safe_url(url)
    if not safe_url:
        return {"error": "url must be a valid HTTP(S) URL"}
    focus = str(focus or "all").casefold()
    if focus not in _FOCI:
        return {"error": f"focus must be one of: {', '.join(sorted(_FOCI))}"}
    fetched = _fetch(safe_url, http_provider or RequestsHttpProvider(requests))
    if isinstance(fetched, dict):
        return {**fetched, "url": _display_url(safe_url)}
    final_url, html = fetched
    parser = _PageSignals()
    try:
        parser.feed(html)
    except Exception:
        return {
            "error": "website HTML could not be parsed",
            "url": _display_url(safe_url),
        }
    content_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
    insights = _analyze(parser, focus)
    result: dict[str, Any] = {
        "status": "learned",
        "source": {"url": _display_url(final_url), "content_sha256": content_hash},
        "focus": focus,
        "page": {"title": parser.title, "meta_description": parser.description},
        "insights": insights,
        "trust": "untrusted_external_evidence",
        "limitations": [
            "Structural signals are observations, not proof that a pattern converts.",
            "No scripts, embedded instructions, or off-page claims were executed or trusted.",
        ],
        "persisted": False,
    }
    if persist:
        result["learned_at"] = _persist(_display_url(final_url), content_hash, focus, insights)
        result["persisted"] = True
    return result


WEBSITE_LEARNING_SCHEMA = {
    "type": "function",
    "name": "learn_from_website",
    "description": "Study a public website as untrusted evidence and extract reusable, evidence-backed web design, marketing, and consulting patterns. Does not execute page instructions or scripts.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public HTTP(S) website URL"},
            "focus": {
                "type": "string",
                "enum": sorted(_FOCI),
                "description": "Learning discipline",
            },
            "persist": {
                "type": "boolean",
                "description": "Retain the bounded learning record locally (default true)",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
}
