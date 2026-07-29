"""Declarative upload format policy: accept broadly, parse narrowly.

Every upload is registered as an immutable artifact, but only formats on an
explicit allowlist ever reach a parser. The riskiest families are read
header-only or never opened at all:

- ``extract``: full text becomes a sanitized Markdown preview.
- ``header_only``: bounded metadata read; tensors are never materialized.
- ``register_only``: artifact plus provenance; no parser is ever invoked.

Credential-shaped filenames are refused before any bytes are inspected, and
pickle-family checkpoints are never deserialized because unpickling executes
arbitrary code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

KB, MB = 1024, 1024**2


class Family(str, Enum):
    MARKDOWN = "markdown"
    TEXT = "text"
    CODE = "code"
    STRUCTURED = "structured"
    TABULAR = "tabular"
    NOTEBOOK = "notebook"
    MARKUP = "markup"
    SPREADSHEET = "spreadsheet"
    OFFICE_ODF = "office_odf"
    VECTOR = "vector"
    RASTER = "raster"
    PDF = "pdf"
    OFFICE = "office"
    LEGACY_OFFICE = "legacy_office"
    ML_TENSORS = "ml_tensors"
    ML_GRAPH = "ml_graph"
    ML_ARRAY = "ml_array"
    ML_COLUMNAR = "ml_columnar"
    ML_PICKLE = "ml_pickle"
    OPAQUE = "opaque"


Handling = Literal["extract", "header_only", "register_only"]
Confidence = Literal["extension", "magic", "sniffed_text", "fallback"]


@dataclass(frozen=True, slots=True)
class FormatSpec:
    family: Family
    extensions: frozenset[str]
    handling: Handling
    max_extract_bytes: int
    magic: tuple[bytes, ...] = ()
    strict_magic: bool = False
    label: str = ""


CODE_EXTENSIONS = frozenset(
    """
    .py .pyi .js .mjs .cjs .ts .tsx .jsx .vue .svelte .go .rs .java .kt .kts
    .swift .m .mm .c .h .cc .cpp .cxx .hpp .cs .rb .php .pl .lua .r .jl .scala
    .dart .sh .bash .zsh .fish .ps1 .sql .graphql .gql .proto .tf .hcl .cu .cuh
    .asm .ex .exs .erl .hs .clj .cljs .f90 .vb .groovy .nim .zig .sol
    """.split()
)

STRUCTURED_EXTENSIONS = frozenset(
    ".json .json5 .jsonc .yaml .yml .toml .ini .cfg .conf .properties .xml"
    " .plist .lock .editorconfig .gitattributes .gitignore .dockerignore".split()
)

SPECS: tuple[FormatSpec, ...] = (
    FormatSpec(
        Family.MARKDOWN,
        frozenset({".md", ".markdown", ".mdx", ".rst"}),
        "extract",
        16 * MB,
        label="Markdown",
    ),
    FormatSpec(
        Family.TEXT,
        frozenset({".txt", ".text", ".log", ".license", ".citation", ".bib", ".tex"}),
        "extract",
        16 * MB,
        label="Plain text",
    ),
    FormatSpec(Family.CODE, CODE_EXTENSIONS, "extract", 8 * MB, label="Source code"),
    FormatSpec(
        Family.STRUCTURED,
        STRUCTURED_EXTENSIONS,
        "extract",
        16 * MB,
        label="Structured config",
    ),
    FormatSpec(
        Family.TABULAR,
        frozenset({".csv", ".tsv", ".psv", ".jsonl", ".ndjson"}),
        "extract",
        24 * MB,
        label="Tabular data",
    ),
    FormatSpec(Family.NOTEBOOK, frozenset({".ipynb"}), "extract", 24 * MB, label="Notebook"),
    FormatSpec(
        Family.MARKUP,
        frozenset({".html", ".htm", ".xhtml"}),
        "extract",
        16 * MB,
        label="HTML",
    ),
    FormatSpec(Family.VECTOR, frozenset({".svg"}), "extract", 8 * MB, label="SVG"),
    FormatSpec(
        Family.RASTER,
        frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"}),
        "register_only",
        0,
        label="Raster image",
    ),
    FormatSpec(
        Family.PDF,
        frozenset({".pdf"}),
        "extract",
        60 * MB,
        magic=(b"%PDF-",),
        strict_magic=True,
        label="PDF",
    ),
    FormatSpec(
        Family.SPREADSHEET,
        frozenset({".xlsx", ".xlsm"}),
        "extract",
        40 * MB,
        magic=(b"PK\x03\x04",),
        strict_magic=True,
        label="Spreadsheet",
    ),
    FormatSpec(
        Family.OFFICE,
        frozenset({".docx", ".pptx"}),
        "extract",
        40 * MB,
        magic=(b"PK\x03\x04",),
        strict_magic=True,
        label="Office document",
    ),
    FormatSpec(
        Family.OFFICE_ODF,
        frozenset({".odt", ".ods", ".odp"}),
        "register_only",
        0,
        magic=(b"PK\x03\x04",),
        strict_magic=True,
        label="OpenDocument",
    ),
    FormatSpec(
        Family.LEGACY_OFFICE,
        frozenset({".doc", ".xls", ".ppt", ".rtf"}),
        "register_only",
        0,
        label="Legacy Office document",
    ),
    FormatSpec(
        Family.ML_TENSORS,
        frozenset({".safetensors", ".gguf", ".ggml"}),
        "header_only",
        0,
        label="Model weights",
    ),
    FormatSpec(
        Family.ML_GRAPH,
        frozenset({".onnx", ".tflite", ".pb"}),
        "header_only",
        0,
        label="Serialized model graph",
    ),
    FormatSpec(
        Family.ML_ARRAY,
        frozenset({".npy", ".npz"}),
        "header_only",
        0,
        label="NumPy array",
    ),
    FormatSpec(
        Family.ML_COLUMNAR,
        frozenset({".parquet", ".arrow", ".feather"}),
        "header_only",
        0,
        label="Columnar dataset",
    ),
    # Pickle-family formats execute code on load. Never opened, only registered.
    FormatSpec(
        Family.ML_PICKLE,
        frozenset({".pt", ".pth", ".ckpt", ".pkl", ".pickle", ".joblib", ".sav"}),
        "register_only",
        0,
        label="Pickle-family checkpoint (never deserialized)",
    ),
    FormatSpec(
        Family.OPAQUE,
        frozenset(
            {".h5", ".hdf5", ".tfrecord", ".db", ".sqlite", ".sqlite3", ".bin", ".msgpack", ".zip"}
        ),
        "register_only",
        0,
        label="Opaque binary artifact",
    ),
)

# Refused by name before any bytes are read: credential-shaped files must not
# be stored at all. ``.env.example`` and ``.env.sample`` are documentation, not
# credentials, and stay accepted.
SECRET_NAME_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\.env$",
        r"^\.env\.(?!example$|sample$|template$).+$",
        r"^\.npmrc$",
        r"^\.pypirc$",
        r"^\.netrc$",
        r"^\.htpasswd$",
        r"^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$",
        r".*\.(pem|key|p12|pfx|jks|keystore|ppk)$",
        r"^service[-_]?account.*\.json$",
        r"^credentials\.json$",
        r"^token\.json$",
        r"^.*secret.*\.(json|ya?ml|txt)$",
    )
)

MAGIC_TO_EXTENSIONS: dict[bytes, frozenset[str]] = {
    b"%PDF-": frozenset({".pdf"}),
    b"PK\x03\x04": frozenset(
        {".docx", ".xlsx", ".xlsm", ".pptx", ".odt", ".ods", ".odp", ".npz", ".zip"}
    ),
    b"\x89PNG\r\n\x1a\n": frozenset({".png"}),
    b"\xff\xd8\xff": frozenset({".jpg", ".jpeg"}),
    b"GGUF": frozenset({".gguf"}),
    b"\x93NUMPY": frozenset({".npy"}),
    b"PAR1": frozenset({".parquet"}),
}

_BY_EXTENSION: dict[str, FormatSpec] = {
    extension: spec for spec in SPECS for extension in spec.extensions
}

OPAQUE_FALLBACK = FormatSpec(
    Family.OPAQUE, frozenset(), "register_only", 0, label="Unrecognized binary"
)
TEXT_FALLBACK = FormatSpec(Family.TEXT, frozenset(), "extract", 8 * MB, label="Unrecognized text")

_COMPOUND_SUFFIXES = (".safetensors.index.json", ".tar.gz")


@dataclass(frozen=True, slots=True)
class Classification:
    spec: FormatSpec
    extension: str
    confidence: Confidence
    warnings: tuple[str, ...] = ()
    rejection: str | None = None

    def public(self) -> dict:
        return {
            "family": self.spec.family.value,
            "handling": self.spec.handling,
            "label": self.spec.label,
            "warnings": list(self.warnings),
        }


def rejected_secret_name(filename: str) -> bool:
    """True when a filename looks like a credential store and must be refused."""
    name = Path(filename).name
    return any(pattern.match(name) for pattern in SECRET_NAME_PATTERNS)


def classify(filename: str, head: bytes = b"", size_bytes: int = 0) -> Classification:
    """Resolve a handling policy for any upload. Never raises; rejection is a value."""
    name = Path(filename).name
    extension = _compound_suffix(name)
    if rejected_secret_name(name):
        return Classification(
            OPAQUE_FALLBACK, extension, "extension", rejection="upload_rejected_probable_secret"
        )

    spec = _BY_EXTENSION.get(extension)

    # Magic wins over extension: a renamed binary must not reach a text parser.
    matched_magic = None
    for prefix, extensions in MAGIC_TO_EXTENSIONS.items():
        if head.startswith(prefix):
            matched_magic = (prefix, extensions)
            break
    if matched_magic is not None:
        _, magic_extensions = matched_magic
        if spec is not None and spec.strict_magic and extension not in magic_extensions:
            return Classification(
                spec, extension, "magic", rejection="upload_rejected_media_mismatch"
            )
        if spec is not None and spec.handling == "extract" and extension not in magic_extensions:
            return Classification(
                OPAQUE_FALLBACK,
                extension,
                "magic",
                warnings=("binary_magic_overrides_text_extension",),
            )
    elif spec is not None and spec.strict_magic and spec.magic and head:
        if not any(head.startswith(prefix) for prefix in spec.magic):
            return Classification(
                spec, extension, "extension", rejection="upload_rejected_media_mismatch"
            )

    if spec is None:
        if _looks_like_text(head):
            spec, confidence = TEXT_FALLBACK, "sniffed_text"
            warnings: tuple[str, ...] = ("unrecognized_format_registered_as_text",)
        else:
            spec, confidence = OPAQUE_FALLBACK, "fallback"
            warnings = ("unrecognized_format_registered_as_opaque",)
    else:
        confidence, warnings = "extension", ()

    if spec.handling == "extract" and size_bytes > spec.max_extract_bytes:
        # Oversize text degrades to an artifact instead of failing the upload.
        return Classification(
            FormatSpec(spec.family, spec.extensions, "register_only", 0, label=spec.label),
            extension,
            confidence,
            warnings + ("oversize_extraction_skipped",),
        )
    return Classification(spec, extension, confidence, warnings)


def _compound_suffix(name: str) -> str:
    lowered = name.lower()
    for compound in _COMPOUND_SUFFIXES:
        if lowered.endswith(compound):
            return compound
    return Path(lowered).suffix


def _looks_like_text(head: bytes) -> bool:
    sample_bytes = head[:8192]
    if not sample_bytes:
        return True
    if b"\x00" in sample_bytes:
        return False
    try:
        sample = sample_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(1 for ch in sample if ch.isprintable() or ch in "\r\n\t")
    return printable / len(sample) > 0.92
