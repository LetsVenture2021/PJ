"""Approval-gated environment variable placeholders for ``~/.env``.

Secret values never flow through the assistant. ``create_env_placeholder``
appends a ``NAME=`` line for an allowlisted variable name only, and
``open_env_file`` opens the file in the owner's local editor so the value is
typed there. Neither tool ever reads or returns file contents: the env file
holds credentials, and its contents must never enter a transcript or log.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ENV_PATH = Path(os.path.expanduser("~/.env"))
NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

# Names the assistant may create placeholders for: integration tokens the
# repository documents. Extend the list in code review, not at runtime.
ALLOWED_ENV_NAMES = frozenset(
    {
        "HF_TOKEN",
        "GITHUB_MCP_PAT",
        "NOTION_MCP_TOKEN",
        "STRIPE_MCP_TOKEN",
    }
)


def _existing_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    names = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            names.add(stripped.split("=", 1)[0].strip())
    return names


def create_env_placeholder(name: str = "") -> dict:
    """Append ``NAME=`` to the env file for an allowlisted variable name."""
    candidate = str(name or "").strip()
    if not NAME_PATTERN.fullmatch(candidate):
        return {"error": "variable name must be 3-64 chars of A-Z, 0-9, and underscores"}
    if candidate not in ALLOWED_ENV_NAMES:
        return {
            "error": f"'{candidate}' is not an allowlisted variable name",
            "allowed": sorted(ALLOWED_ENV_NAMES),
        }
    if candidate in _existing_names(ENV_PATH):
        return {
            "status": "exists",
            "name": candidate,
            "detail": "The variable is already present; its value was not read.",
        }
    with ENV_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{candidate}=\n")
    ENV_PATH.chmod(0o600)
    return {
        "status": "created",
        "name": candidate,
        "detail": "Empty placeholder appended. Add the value locally; it is never sent here.",
    }


def open_env_file() -> dict:
    """Open the env file in the owner's local text editor (macOS ``open -t``)."""
    if not ENV_PATH.is_file():
        return {"error": "the env file does not exist yet"}
    try:
        subprocess.run(
            ["/usr/bin/open", "-t", str(ENV_PATH)],
            check=True,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return {"error": "the local editor could not be opened"}
    return {
        "status": "opened",
        "detail": "Opened in the local text editor. File contents are never read here.",
    }


ENVFILE_SCHEMAS = [
    {
        "type": "function",
        "name": "create_env_placeholder",
        "description": (
            "Append an empty NAME= placeholder to the owner's ~/.env for an "
            "allowlisted integration variable (for example HF_TOKEN). Secret "
            "values are never accepted or read; the owner adds the value "
            "locally afterward."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Allowlisted variable name, for example HF_TOKEN.",
                }
            },
            "required": ["name"],
        },
    },
    {
        "type": "function",
        "name": "open_env_file",
        "description": (
            "Open the owner's ~/.env in their local text editor so they can "
            "add a secret value themselves. Never reads or returns contents."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]

ENVFILE_DISPATCH = {
    "create_env_placeholder": lambda name="": create_env_placeholder(name),
    "open_env_file": lambda: open_env_file(),
}
