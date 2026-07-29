"""Sandbox boundary for the exceptional execution of reviewed code packages."""

from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass

from ops.extensions.models import ExtensionError


@dataclass(frozen=True)
class SandboxLimits:
    cpu_seconds: int = 2
    memory_bytes: int = 128 * 1024 * 1024
    wall_seconds: int = 5
    filesystem_roots: tuple[str, ...] = ()
    environment: tuple[str, ...] = ()
    network_domains: tuple[str, ...] = ()


def sandbox_available() -> bool:
    return platform.system() == "Darwin" and shutil.which("/usr/bin/sandbox-exec") is not None


def require_sandbox(*, code_bearing: bool, explicitly_enabled: bool) -> None:
    if code_bearing and (not explicitly_enabled or not sandbox_available()):
        raise ExtensionError("arbitrary extension code is disabled without a supported sandbox")
