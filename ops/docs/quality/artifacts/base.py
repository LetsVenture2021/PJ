"""Shared adapter result and canonical comparison logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import CanonicalDocument, normalized_text, numeric_values


@dataclass(slots=True)
class Inspection:
    text_items: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)


class ArtifactAdapter:
    format = ""
    renderer_version = "unknown"

    def inspect(self, path: Path) -> Inspection:
        raise NotImplementedError

    def compare(self, canonical: CanonicalDocument, inspection: Inspection) -> None:
        exported = [
            normalized_text(item) for item in inspection.text_items if normalized_text(item)
        ]
        exported_stream = " \n ".join(exported)
        cursor = 0
        for position, expected in enumerate(canonical.reading_order):
            wanted = normalized_text(expected)
            if not wanted:
                continue
            found = exported_stream.find(wanted, cursor)
            if found < 0:
                anywhere = wanted in exported_stream
                code = "reordered_content" if anywhere else "omitted_or_truncated_text"
                inspection.errors.append(
                    {
                        "code": code,
                        "message": f"reading-order item {position + 1} was not preserved",
                    }
                )
                continue
            cursor = found + len(wanted)
        expected_numbers = numeric_values(" ".join(canonical.reading_order))
        actual_numbers = numeric_values(" ".join(inspection.text_items))
        missing_numbers = [number for number in expected_numbers if number not in actual_numbers]
        if missing_numbers:
            inspection.errors.append(
                {
                    "code": "altered_numeric_values",
                    "message": f"missing numeric values: {missing_numbers[:10]}",
                }
            )
        expected_tables = [block.rows for block in canonical.blocks if block.kind == "table"]
        actual_cells = {
            normalized_text(cell) for table in inspection.tables for row in table for cell in row
        }
        missing_cells = [
            cell
            for table in expected_tables
            for row in table
            for cell in row
            if normalized_text(cell) not in actual_cells
        ]
        if missing_cells:
            inspection.errors.append(
                {
                    "code": "missing_table_cells",
                    "message": f"missing table cells: {missing_cells[:10]}",
                }
            )
        expected_links = [
            (label, target)
            for block in canonical.blocks
            for label, target in block.metadata.get("links", ())
        ]
        actual_targets = {target for _, target in inspection.links}
        if any(target not in actual_targets for _, target in expected_links):
            inspection.errors.append(
                {"code": "link_loss", "message": "one or more link targets were not preserved"}
            )
        for block in canonical.blocks:
            if (
                block.kind in {"image", "code_block", "note"}
                and block.kind in inspection.unsupported
            ):
                inspection.errors.append(
                    {
                        "code": "unsupported_construct",
                        "message": f"{self.format} does not preserve {block.kind}",
                    }
                )
