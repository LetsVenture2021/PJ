#!/usr/bin/env python3
"""Normalize local EML/MBOX exports into bounded, untrusted JSONL records."""

from __future__ import annotations

import argparse
import hashlib
import json
import mailbox
import os
import re
import tempfile
from datetime import timezone
from email.header import decode_header
from email.message import Message
from email.parser import BytesParser
from email.policy import default
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

ALLOWED_SUFFIXES = {".eml", ".mbox"}
SUSPICIOUS_NAME = re.compile(
    r"(^|[._-])(credential|credentials|password|passwd|secret|token|api[_-]?key)([._-]|$)",
    re.IGNORECASE,
)
MAX_HEADER_CHARS = 4_096
MAX_BODY_CHARS = 1_000_000


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in {"script", "style", "template"}:
            self._hidden += 1
        elif normalized in {"br", "p", "div", "li", "tr"}:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "template"} and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.chunks.append(data)


def _decode_header(value: str | None) -> str:
    pieces: list[str] = []
    for chunk, charset in decode_header(value or ""):
        if isinstance(chunk, bytes):
            try:
                pieces.append(chunk.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                pieces.append(chunk.decode("utf-8", errors="replace"))
        else:
            pieces.append(chunk)
    return " ".join("".join(pieces).split())[:MAX_HEADER_CHARS]


def _addresses(message: Message, header: str) -> list[str]:
    values = [_decode_header(value) for value in message.get_all(header, [])]
    return [address[:MAX_HEADER_CHARS] for _, address in getaddresses(values) if address]


def _payload_text(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
    except (LookupError, UnicodeError, ValueError):
        return ""
    charset = part.get_content_charset() or "utf-8"
    if isinstance(payload, bytes):
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")
    raw_payload = part.get_payload()
    return raw_payload if isinstance(raw_payload, str) else ""


def _body(message: Message) -> str:
    plain: list[str] = []
    html: list[str] = []
    for part in message.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type().casefold()
        if content_type not in {"text/plain", "text/html"}:
            continue
        text = _payload_text(part)
        if content_type == "text/plain":
            plain.append(text)
        else:
            parser = _VisibleText()
            parser.feed(text)
            html.append("".join(parser.chunks))
    return "\n".join(" ".join((plain or html)).splitlines()).strip()[:MAX_BODY_CHARS]


def _date(message: Message) -> str | None:
    try:
        parsed = parsedate_to_datetime(message.get("Date", ""))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _record(message: Message, source: str) -> dict[str, object]:
    content = _body(message)
    identity = {
        "message_id": _decode_header(message.get("Message-ID")) or None,
        "date": _date(message),
        "from": _addresses(message, "From"),
        "to": _addresses(message, "To"),
        "cc": _addresses(message, "Cc"),
        "subject": _decode_header(message.get("Subject")),
        "content": content,
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "schema_version": 1,
        "trust": "untrusted_email_content",
        "source": source,
        "digest": hashlib.sha256(canonical).hexdigest(),
        **identity,
    }


def _input_files(source: Path) -> list[Path]:
    if source.is_file():
        if source.suffix.casefold() not in ALLOWED_SUFFIXES:
            raise ValueError("source file must end in .eml or .mbox")
        return [source]
    candidates = [
        path
        for path in sorted(source.rglob("*"))
        if path.suffix.casefold() in ALLOWED_SUFFIXES and (path.is_file() or path.is_symlink())
    ]
    if not candidates:
        raise ValueError("source directory contains no .eml or .mbox files")
    return candidates


def _messages(path: Path, max_bytes: int) -> Iterable[tuple[str, Message]]:
    if path.is_symlink():
        raise ValueError("symlink refused")
    if SUSPICIOUS_NAME.search(path.name):
        raise ValueError("credential-shaped filename refused")
    if path.stat().st_size > max_bytes:
        raise ValueError("file exceeds byte limit")
    if path.suffix.casefold() == ".eml":
        yield str(path), BytesParser(policy=default).parsebytes(path.read_bytes())
        return
    box = mailbox.mbox(path, create=False)
    try:
        for index, message in enumerate(box):
            yield f"{path}#{index + 1}", BytesParser(policy=default).parsebytes(message.as_bytes())
    finally:
        box.close()


def _validate_paths(source: Path, output: Path) -> None:
    if not source.exists() or source.is_symlink():
        raise ValueError("source must be an existing, non-symlink file or directory")
    if output.exists() and output.is_symlink():
        raise ValueError("output must not be a symlink")
    source_resolved = source.resolve(strict=False)
    output_resolved = output.resolve(strict=False)
    if source_resolved == output_resolved:
        raise ValueError("output must not overwrite the source")
    if source.is_dir() and output_resolved.is_relative_to(source_resolved):
        raise ValueError("output must be outside the source directory")


def _skip_reason(exc: BaseException) -> str:
    reason = str(exc).splitlines()[0].strip()
    return (reason or type(exc).__name__)[:200]


def normalize(
    source: Path,
    output: Path,
    max_bytes: int,
    max_messages: int,
) -> dict[str, object]:
    _validate_paths(source, output)
    counts = {"discovered": 0, "written": 0, "duplicates": 0, "skipped": 0}
    skipped_sources: list[dict[str, str]] = []
    seen: set[str] = set()
    limit_reached = False
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as destination:
            for path in _input_files(source):
                if limit_reached:
                    break
                counts["discovered"] += 1
                try:
                    for source_name, message in _messages(path, max_bytes):
                        if counts["written"] >= max_messages:
                            limit_reached = True
                            break
                        record = _record(message, source_name)
                        digest = str(record["digest"])
                        if digest in seen:
                            counts["duplicates"] += 1
                            continue
                        seen.add(digest)
                        destination.write(json.dumps(record, ensure_ascii=False) + "\n")
                        counts["written"] += 1
                except (OSError, ValueError, mailbox.Error) as exc:
                    counts["skipped"] += 1
                    skipped_sources.append({"source": str(path), "reason": _skip_reason(exc)})
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_name, output)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return {
        "counts": counts,
        "limit_reached": limit_reached,
        "skipped_sources": skipped_sources,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-message-bytes", type=int, default=50_000_000)
    parser.add_argument("--max-messages", type=int, default=100_000)
    args = parser.parse_args()
    if args.max_message_bytes < 1 or args.max_messages < 1:
        parser.error("limits must be positive")
    try:
        summary = normalize(args.source, args.output, args.max_message_bytes, args.max_messages)
    except ValueError as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(summary, sort_keys=True), file=os.sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
