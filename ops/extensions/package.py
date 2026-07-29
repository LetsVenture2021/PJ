"""Fail-closed verification and extraction of signed, content-addressed packages."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ops.extensions.manifest import parse_manifest
from ops.extensions.models import ExtensionError

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 256
CREDENTIAL_NAME = re.compile(
    r"(?:^|[._-])(\.env|credentials?|id_rsa|private[_-]?key|token)(?:$|[._-])", re.I
)
ALLOWED_SUFFIXES = {".json", ".md", ".txt", ".py", ".js", ".wasm"}


def _canonical_manifest(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()


def verify_package(
    archive: str | Path,
    *,
    trusted_publishers: dict[str, bytes],
    revoked_publishers: set[str] | None = None,
    expected_digest: str | None = None,
) -> tuple[dict[str, Any], str]:
    path = Path(archive)
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ExtensionError("package is missing or exceeds size limit")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected_digest is not None and digest != expected_digest:
        raise ExtensionError("content address does not match package")
    try:
        with zipfile.ZipFile(path) as package:
            infos = package.infolist()
            if len(infos) > MAX_FILES:
                raise ExtensionError("package contains too many files")
            names = {info.filename for info in infos}
            if not {"manifest.json", "signature.ed25519"} <= names:
                raise ExtensionError("package is missing signing metadata")
            manifest = parse_manifest(package.read("manifest.json"))
            publisher = manifest["publisher"]
            if publisher in (revoked_publishers or set()):
                raise ExtensionError("publisher is revoked")
            key = trusted_publishers.get(publisher)
            if key is None:
                raise ExtensionError("publisher is not trusted")
            try:
                Ed25519PublicKey.from_public_bytes(key).verify(
                    package.read("signature.ed25519"), _canonical_manifest(manifest)
                )
            except (InvalidSignature, ValueError) as exc:
                raise ExtensionError("invalid package signature") from exc
            declared = set(manifest["files"]) | {"manifest.json", "signature.ed25519"}
            if names != declared:
                raise ExtensionError("archive contains undeclared files")
            for info in infos:
                pure = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                if pure.is_absolute() or ".." in pure.parts or "\\" in info.filename:
                    raise ExtensionError("unsafe archive path")
                file_type = stat.S_IFMT(mode)
                if info.is_dir() or stat.S_ISLNK(mode) or (file_type and not stat.S_ISREG(mode)):
                    raise ExtensionError("unsupported archive file type")
                if info.file_size > MAX_FILE_BYTES:
                    raise ExtensionError("archive member exceeds size limit")
                if info.filename not in {"manifest.json", "signature.ed25519"}:
                    suffix = pure.suffix.lower()
                    if CREDENTIAL_NAME.search(pure.name) or suffix not in ALLOWED_SUFFIXES:
                        raise ExtensionError("credential-shaped or unsupported file")
                    if mode & 0o111 or (
                        suffix in {".py", ".js", ".wasm"} and not manifest.get("code")
                    ):
                        raise ExtensionError("undeclared executable content")
                    actual = hashlib.sha256(package.read(info)).hexdigest()
                    if manifest["files"].get(info.filename) != actual:
                        raise ExtensionError("archive member hash mismatch")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ExtensionError("invalid extension archive") from exc
    return manifest, digest


def extract_verified(
    archive: str | Path, destination: str | Path, manifest: dict[str, Any]
) -> None:
    """Extract only an already verified manifest's declared payload, without ZipFile.extract."""
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as package:
        for name in manifest["files"]:
            target = root.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(target, flags, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(package.read(name))
