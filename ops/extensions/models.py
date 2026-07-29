"""Provider-neutral models for the PJ extension protocol."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ExtensionError(ValueError):
    """A package failed closed during validation or state transition."""


@dataclass(frozen=True)
class PackageIdentity:
    identifier: str
    publisher: str
    version: str
    digest: str


@dataclass(frozen=True)
class InstallPreview:
    identity: PackageIdentity
    manifest: dict[str, Any]
    permission_diff: dict[str, list[str]]
    policy_entries: dict[str, str]
    archive: Path
