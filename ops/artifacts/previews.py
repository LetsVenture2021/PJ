"""Bounded, non-executing preview and comparison adapters."""

from __future__ import annotations

import difflib
import hashlib
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image

MAX_PREVIEW_BYTES = 256_000
MAX_PREVIEW_CHARS = 40_000


def _bounded(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError("artifact content is unavailable")
    with path.open("rb") as handle:
        return handle.read(MAX_PREVIEW_BYTES + 1)[:MAX_PREVIEW_BYTES]


def _zip_xml(path: Path, prefixes: tuple[str, ...]) -> list[tuple[str, str]]:
    rows = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist()[:500]:
            if info.file_size > MAX_PREVIEW_BYTES or not info.filename.startswith(prefixes):
                continue
            root = ElementTree.fromstring(archive.read(info)[:MAX_PREVIEW_BYTES])
            text = " ".join(value.strip() for value in root.itertext() if value.strip())
            rows.append((info.filename, text[:MAX_PREVIEW_CHARS]))
    return rows


def preview(path: Path, media_type: str) -> dict:
    suffix = path.suffix.lower()
    if suffix in {
        ".md",
        ".markdown",
        ".txt",
        ".py",
        ".js",
        ".ts",
        ".css",
        ".html",
        ".json",
        ".yaml",
        ".yml",
    }:
        return {
            "kind": "text",
            "text": _bounded(path).decode("utf-8", "replace")[:MAX_PREVIEW_CHARS],
        }
    if suffix == ".pdf":
        data = _bounded(path)
        return {
            "kind": "pdf",
            "pages": len(re.findall(rb"/Type\s*/Page\b", data)),
            "byte_size": path.stat().st_size,
        }
    if suffix in {".docx", ".rtf"}:
        if suffix == ".docx":
            text = "\n".join(value for _, value in _zip_xml(path, ("word/document.xml",)))
        else:
            text = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", _bounded(path).decode("latin-1", "replace"))
        return {"kind": "document", "text": text[:MAX_PREVIEW_CHARS]}
    if suffix == ".xlsx":
        parts = _zip_xml(path, ("xl/worksheets/", "xl/sharedStrings.xml"))
        return {
            "kind": "spreadsheet",
            "sheets": [{"part": name, "content": text} for name, text in parts[:30]],
        }
    if suffix == ".pptx":
        parts = _zip_xml(path, ("ppt/slides/slide",))
        return {
            "kind": "presentation",
            "slides": [{"slide": name, "text": text} for name, text in parts[:100]],
        }
    if media_type.startswith("image/") or suffix in {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
    }:
        if suffix == ".svg":
            data = _bounded(path)
            if b"<!DOCTYPE" in data.upper():
                raise ValueError("SVG document types are not allowed")
            root = ElementTree.fromstring(data)
            return {
                "kind": "image",
                "format": "SVG",
                "width": root.get("width"),
                "height": root.get("height"),
                "digest_preview": hashlib.sha256(data).hexdigest()[:16],
            }
        with Image.open(path) as image:
            image.thumbnail((256, 256))
            sample = io.BytesIO()
            image.convert("RGB").save(sample, "PNG")
            return {
                "kind": "image",
                "format": image.format,
                "width": image.width,
                "height": image.height,
                "digest_preview": hashlib.sha256(sample.getvalue()).hexdigest()[:16],
            }
    raise ValueError(f"No safe preview adapter for {media_type}")


def compare(left: Path, right: Path, media_type: str) -> dict:
    before, after = preview(left, media_type), preview(right, media_type)
    kind = before["kind"]
    if kind in {"text", "document"}:
        lines = difflib.unified_diff(
            before.get("text", "").splitlines(), after.get("text", "").splitlines(), lineterm=""
        )
        return {"kind": "text_diff", "diff": list(lines)[:2000]}
    if kind == "spreadsheet":
        old = {x["part"]: x["content"] for x in before["sheets"]}
        new = {x["part"]: x["content"] for x in after["sheets"]}
        return {"kind": "cell_formula_diff", "parts": _mapping_diff(old, new)}
    if kind == "presentation":
        old = {x["slide"]: x["text"] for x in before["slides"]}
        new = {x["slide"]: x["text"] for x in after["slides"]}
        return {
            "kind": "slide_diff",
            "slides": _mapping_diff(old, new),
            "thumbnail_changed": old != new,
        }
    if kind == "image":
        return {
            "kind": "image_diff",
            "metadata_before": before,
            "metadata_after": after,
            "perceptually_changed": before["digest_preview"] != after["digest_preview"],
        }
    return {"kind": f"{kind}_diff", "changed": before != after, "before": before, "after": after}


def _mapping_diff(old: dict[str, str], new: dict[str, str]) -> list[dict]:
    return [
        {"region": key, "before": old.get(key), "after": new.get(key)}
        for key in sorted(old.keys() | new.keys())
        if old.get(key) != new.get(key)
    ]
