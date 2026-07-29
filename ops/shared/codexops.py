"""Approval-gated Codex SDK delegation for coding tasks.

Runs a prompt on a local Codex thread via the official Python SDK. The sandbox
is read-only unless the owner-approved call explicitly asks for
workspace-write; full filesystem access is never offered. Requires a local
``codex login``; the tool reports cleanly when Codex is unavailable.
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_SANDBOXES = {"read-only": "read_only", "workspace-write": "workspace_write"}


def run_codex_task(prompt: str = "", sandbox: str = "read-only") -> dict:
    task = str(prompt or "").strip()
    if not task:
        return {"error": "prompt is required"}
    preset_name = _SANDBOXES.get(str(sandbox or "read-only"))
    if preset_name is None:
        return {"error": "sandbox must be 'read-only' or 'workspace-write'"}
    try:
        from openai_codex import Codex, Sandbox
    except ImportError:
        return {"error": "the openai-codex SDK is not installed in this environment"}
    try:
        with Codex() as codex:
            thread = codex.thread_start(sandbox=getattr(Sandbox, preset_name))
            result = thread.run(task)
        return {
            "status": "completed",
            "sandbox": sandbox,
            "final_response": str(result.final_response or "")[:20000],
        }
    except Exception as exc:  # SDK/runtime failures surface as typed text
        return {"error": f"codex_run_failed: {str(exc)[:300]}"}


CODEXOPS_SCHEMAS = [
    {
        "type": "function",
        "name": "run_codex_task",
        "description": (
            "Delegate a coding task to a local Codex thread via the Codex SDK. "
            "Read-only sandbox by default; workspace-write only when the "
            "approved request requires edits. Never has full disk access."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "sandbox": {
                    "type": "string",
                    "enum": ["read-only", "workspace-write"],
                },
            },
            "required": ["prompt"],
        },
    }
]

CODEXOPS_DISPATCH = {
    "run_codex_task": lambda prompt="", sandbox="read-only": run_codex_task(prompt, sandbox)
}
