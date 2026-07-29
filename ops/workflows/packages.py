"""Signed/hash-addressed workflow package export and hostile import validation."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import zipfile
from pathlib import PurePosixPath
from typing import Collection

from .compiler import CompiledWorkflow, WorkflowCompiler
from .models import FORMAT_VERSION, WorkflowDefinition, WorkflowError, canonical_json

MAX_PACKAGE_BYTES = 2_000_000
ALLOWED_FILES = {"manifest.json", "checksums.json", "signature.txt"}


def export_package(compiled: CompiledWorkflow, signing_key: bytes | None = None) -> bytes:
    manifest = canonical_json(compiled.definition.as_dict())
    checksums = canonical_json({"manifest.json": hashlib.sha256(manifest).hexdigest()})
    signature = (
        hmac.new(signing_key, manifest + checksums, hashlib.sha256).hexdigest()
        if signing_key
        else ""
    )
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest)
        archive.writestr("checksums.json", checksums)
        archive.writestr("signature.txt", signature)
    return target.getvalue()


def import_package(
    data: bytes,
    available_tools: Collection[str],
    signing_key: bytes | None = None,
    *,
    max_cost_usd: float = 100.0,
) -> CompiledWorkflow:
    if len(data) > MAX_PACKAGE_BYTES:
        raise WorkflowError("workflow package is too large")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != ALLOWED_FILES:
                raise WorkflowError("package contains missing, duplicate, or unsupported files")
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
                    raise WorkflowError("package path traversal is prohibited")
                info = archive.getinfo(name)
                if info.file_size > MAX_PACKAGE_BYTES or info.is_dir():
                    raise WorkflowError("invalid package member")
            manifest, checksums = archive.read("manifest.json"), archive.read("checksums.json")
            signature = archive.read("signature.txt").decode("ascii")
    except (zipfile.BadZipFile, UnicodeDecodeError, KeyError) as exc:
        raise WorkflowError("invalid workflow package") from exc
    expected = json.loads(checksums)
    if expected != {"manifest.json": hashlib.sha256(manifest).hexdigest()}:
        raise WorkflowError("package checksum validation failed")
    if signing_key is not None:
        wanted = hmac.new(signing_key, manifest + checksums, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, wanted):
            raise WorkflowError("package signature validation failed")
    definition = WorkflowDefinition.from_dict(json.loads(manifest))
    if definition.compatibility_version != FORMAT_VERSION:
        raise WorkflowError("incompatible workflow package")
    return WorkflowCompiler(available_tools, max_cost_usd=max_cost_usd).compile(definition)
