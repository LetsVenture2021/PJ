"""Canonical, format-neutral representation used to verify document exports."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Block:
    """One item in the document's explicit reading order."""

    kind: str
    text: str = ""
    level: int | None = None
    items: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    target: str = ""
    alt: str = ""
    language: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CanonicalDocument:
    """All semantics an adapter must preserve, including reading order."""

    blocks: tuple[Block, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def reading_order(self) -> tuple[str, ...]:
        return tuple(block.text for block in self.blocks if block.text)

    def as_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata,
            "reading_order": list(self.reading_order),
            "blocks": [asdict(block) for block in self.blocks],
        }

    @classmethod
    def from_markdown(cls, source: str, *, metadata: dict[str, Any] | None = None):
        """Parse governed Markdown without interpreting embedded active HTML."""
        blocks: list[Block] = []
        lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        index = 0
        paragraph: list[str] = []

        def flush() -> None:
            if paragraph:
                text = " ".join(part.strip() for part in paragraph).strip()
                if text:
                    links = tuple(re.findall(r"\[([^]]+)\]\(([^)]+)\)", text))
                    blocks.append(Block("paragraph", text, metadata={"links": links}))
                paragraph.clear()

        while index < len(lines):
            line = lines[index]
            heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            fence = re.match(r"^```\s*([\w+-]*)", line)
            image = re.fullmatch(r"\s*!\[([^]]*)\]\(([^)]+)\)\s*", line)
            if heading:
                flush()
                blocks.append(Block("heading", heading.group(2), level=len(heading.group(1))))
            elif fence:
                flush()
                code: list[str] = []
                index += 1
                while index < len(lines) and not lines[index].startswith("```"):
                    code.append(lines[index])
                    index += 1
                blocks.append(Block("code_block", "\n".join(code), language=fence.group(1)))
            elif image:
                flush()
                blocks.append(Block("image", target=image.group(2), alt=image.group(1)))
            elif re.match(r"^\s*(?:[-*+] |\d+[.)] )", line):
                flush()
                items = []
                while index < len(lines) and re.match(r"^\s*(?:[-*+] |\d+[.)] )", lines[index]):
                    items.append(re.sub(r"^\s*(?:[-*+] |\d+[.)] )", "", lines[index]).strip())
                    index += 1
                blocks.append(Block("list", " ".join(items), items=tuple(items)))
                index -= 1
            elif (
                "|" in line
                and index + 1 < len(lines)
                and re.match(r"^\s*\|?\s*:?-+", lines[index + 1])
            ):
                flush()
                rows = [tuple(cell.strip() for cell in line.strip(" |").split("|"))]
                index += 2
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    rows.append(tuple(cell.strip() for cell in lines[index].strip(" |").split("|")))
                    index += 1
                blocks.append(
                    Block("table", " ".join(cell for row in rows for cell in row), rows=tuple(rows))
                )
                index -= 1
            elif line.startswith(">"):
                flush()
                blocks.append(Block("note", line.lstrip("> ")))
            elif not line.strip():
                flush()
            else:
                paragraph.append(line)
            index += 1
        flush()
        return cls(tuple(blocks), dict(metadata or {}))


def normalized_text(value: str) -> str:
    value = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[`*_>#]", "", value)
    return re.sub(r"\s+", " ", value).strip().casefold()


def numeric_values(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?%?", value))
