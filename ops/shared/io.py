"""Atomic filesystem and JSON helpers shared by operation domains."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: str | Path, value: Any, *, mode: int = 0o600) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy(
        source: str | Path,
        destination: str | Path,
        *,
        mode: int = 0o600) -> None:
    source_path = Path(source)
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination_path.parent,
            prefix=f".{destination_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        with source_path.open("rb") as source_handle, temporary.open("wb") as target:
            shutil.copyfileobj(source_handle, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        temporary.chmod(mode)
        temporary.replace(destination_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
