"""Pure validators. Rules never perform I/O or expose matched sensitive text."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .model import Finding, QualityConfig

RULES = {
    "title": "DOC-STRUCT-001",
    "heading_depth": "DOC-STRUCT-002",
    "heading_jump": "DOC-STRUCT-003",
    "table_size": "DOC-TABLE-001",
    "long_token": "DOC-READ-001",
    "url": "DOC-LINK-001",
    "formula": "DOC-SEC-001",
    "secret": "DOC-SEC-002",
    "placeholder": "DOC-CONTENT-001",
}

_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|authorization|password|secret|token)\s*[:=]\s*\S+|"
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{12,})\b"
)
_URL = re.compile(r"(?i)\b(?:https?|ftp)://[^\s<>]+")
_TOKEN = re.compile(r"\S+")
_FORMULA = re.compile(r"^[\t ]*(?:[=+@]|-(?=\S))")


def _finding(rule: str, severity: str, message: str, line: int, column: int = 1) -> Finding:
    return Finding(RULES[rule], severity, message, line, column)


def validate_text(text: str, config: QualityConfig) -> Iterable[Finding]:
    lines = text.splitlines()
    headings: list[tuple[int, int]] = []
    if config.require_title and not any(re.match(r"^#\s+\S", line) for line in lines):
        yield _finding("title", "major", "Document needs one level-one title.", 0, 0)

    table_run: list[tuple[int, str]] = []
    for number, line in enumerate(lines, 1):
        heading = re.match(r"^(#{1,})(?:\s+|$)", line)
        if heading:
            depth = len(heading.group(1))
            headings.append((number, depth))
            if depth > config.max_heading_depth:
                yield _finding(
                    "heading_depth",
                    "major",
                    f"Heading depth {depth} exceeds {config.max_heading_depth}.",
                    number,
                )

        if line.lstrip().startswith("|") and line.rstrip().endswith("|"):
            table_run.append((number, line))
        elif table_run:
            yield from _validate_table(table_run, config)
            table_run = []

        for token in _TOKEN.finditer(line):
            if len(token.group()) > config.max_token_length and not _URL.fullmatch(token.group()):
                yield _finding(
                    "long_token",
                    "advisory",
                    f"Token exceeds {config.max_token_length} characters.",
                    number,
                    token.start() + 1,
                )
        for match in _URL.finditer(line):
            value = match.group().rstrip(".,);]")
            if (
                ".." in value
                or re.search(r"[\x00-\x20]", value)
                or not re.match(
                    r"^https?://(?:[\w\-]+\.)+[A-Za-z]{2,}(?::\d+)?(?:[/#?].*)?$", value
                )
            ):
                yield _finding(
                    "url",
                    "major",
                    "URL is malformed or uses a disallowed scheme.",
                    number,
                    match.start() + 1,
                )
        if _FORMULA.match(line):
            yield _finding(
                "formula", "critical", "Potential spreadsheet formula injection.", number
            )
        if _SECRET.search(line):
            yield _finding(
                "secret",
                "blocker",
                "Potential sensitive value detected; value omitted.",
                number,
            )
        if re.search(r"\[(?:TBD|VERIFY CURRENT)\]|\{\{[^}]+\}\}|\bTODO\b", line, re.I):
            yield _finding(
                "placeholder", "major", "Unresolved placeholder or verification marker.", number
            )
    if table_run:
        yield from _validate_table(table_run, config)
    for (_, previous), (line, current) in zip(headings, headings[1:]):
        if current > previous + 1:
            yield _finding("heading_jump", "major", "Heading levels must not be skipped.", line)


def _validate_table(rows: list[tuple[int, str]], config: QualityConfig) -> Iterable[Finding]:
    columns = max((len(line.strip("|").split("|")) for _, line in rows), default=0)
    if len(rows) > config.max_table_rows or columns > config.max_table_columns:
        yield _finding(
            "table_size",
            "major",
            f"Table is {len(rows)} rows by {columns} columns; split it for readability.",
            rows[0][0],
        )
