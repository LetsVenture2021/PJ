"""Approval-gated Codex SDK delegation for coding tasks.

Runs a prompt on a local Codex thread via the official Python SDK. The sandbox
is read-only unless the owner-approved call explicitly asks for
workspace-write; full filesystem access is never offered. Requires a local
``codex login``; the tool reports cleanly when Codex is unavailable.
"""

from __future__ import annotations

import shutil
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
    from ops.shared.spend_guard import check_and_count

    guard_error = check_and_count(BASE_DIR / "pj_data.sqlite3", "codex")
    if guard_error:
        return {"error": guard_error}
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


def _register_workspace_outputs(workspace, session_id: str) -> list:
    """Register files Codex wrote in its scratch workspace as uploads."""
    import secrets as _secrets

    from ops.docs import uploads as document_uploads
    from ops.shared.io import sha256_file

    produced = sorted(
        path for path in workspace.rglob("*") if path.is_file() and not path.name.startswith(".")
    )
    if not produced:
        return []
    upload_id = f"UPL-{_secrets.token_hex(16)}"
    target_dir = document_uploads.UPLOADS_DIR / session_id / upload_id
    files = []
    for source in produced[:20]:
        if source.stat().st_size > 50 * 1024 * 1024:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / source.name
        destination.write_bytes(source.read_bytes())
        destination.chmod(0o600)
        files.append(
            {
                "saved_path": f"uploads/{session_id}/{upload_id}/{source.name}",
                "path": destination,
                "name": source.name,
                "mime": "application/octet-stream",
                "size": destination.stat().st_size,
                "sha256": sha256_file(destination),
            }
        )
    if not files:
        return []
    registered = document_uploads.register_uploaded_documents(upload_id, session_id, files)
    return registered["documents"]


def _register_canvas_artifacts(workspace: Path, session_id: str) -> list[dict]:
    """Promote generated files into immutable, downloadable artifact storage."""
    from ops.docs import service as docops

    target_dir = docops.EXPORTS_DIR / session_id
    artifacts = []
    for source in sorted(path for path in workspace.rglob("*") if path.is_file())[:20]:
        if source.stat().st_size > 50 * 1024 * 1024:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / source.name
        shutil.copyfile(source, destination)
        destination.chmod(0o600)
        suffix = source.suffix.lower().lstrip(".") or "bin"
        artifacts.append(
            docops.register_external_artifact(
                f"CANVAS-{session_id}", 1, suffix, destination, audience_ready=True
            )
        )
    return artifacts


def codex_generate_artifact(prompt: str = "") -> dict:
    """Have Codex produce deliverable files (canvases, diagrams, charts, data files).

    Writes are confined to a fresh scratch workspace; everything produced is
    copied into durable upload storage and returned as downloadable records.
    """
    import secrets as _secrets
    import tempfile

    task = str(prompt or "").strip()
    if not task:
        return {"error": "prompt is required"}
    from ops.shared.spend_guard import check_and_count

    guard_error = check_and_count(BASE_DIR / "pj_data.sqlite3", "codex")
    if guard_error:
        return {"error": guard_error}
    try:
        from openai_codex import Codex, Sandbox
    except ImportError:
        return {"error": "the openai-codex SDK is not installed in this environment"}
    session_id = f"codexgen_{_secrets.token_hex(8)}"
    with tempfile.TemporaryDirectory(prefix="pj-codex-artifact-") as scratch:
        workspace = Path(scratch)
        instruction = (
            f"Working directory: {workspace}. Create the requested deliverable "
            f"as one or more files saved in that directory (SVG preferred for "
            f"diagrams, PNG for charts, CSV/MD for data). For an interactive "
            f"experience or canvas, create a self-contained index.html with "
            f"inline CSS and JavaScript and no network dependencies. Task: {task}"
        )
        try:
            with Codex() as codex:
                thread = codex.thread_start(sandbox=Sandbox.workspace_write)
                result = thread.run(instruction)
        except Exception as exc:
            return {"error": f"codex_run_failed: {str(exc)[:300]}"}
        documents = _register_workspace_outputs(workspace, session_id)
        artifacts = _register_canvas_artifacts(workspace, session_id)
    if not documents:
        return {
            "status": "completed_no_files",
            "final_response": str(result.final_response or "")[:4000],
        }
    return {
        "status": "generated",
        "count": len(documents),
        "documents": documents,
        "artifact": artifacts[0],
        "artifacts": artifacts,
        "final_response": str(result.final_response or "")[:4000],
    }


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

CODEXOPS_SCHEMAS.append(
    {
        "type": "function",
        "name": "codex_analyze",
        "description": (
            "Automatically delegate code-related prompts (review, explain, "
            "debug, plan changes) to a local Codex thread in a read-only "
            "sandbox. Use run_codex_task with approval when edits are needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        },
    }
)

CODEXOPS_SCHEMAS.append(
    {
        "type": "function",
        "name": "codex_generate_artifact",
        "description": (
            "Generate deliverable files with Codex: interactive HTML canvases "
            "and mini-apps, diagrams (SVG), charts (PNG), data files, and other "
            "visual artifacts produced by code. Use a self-contained index.html "
            "for interactive experiences. Use this tool for any canvas, mini-app, "
            "game, diagram, graph, chart, or visualization request. "
            "Outputs are saved as downloadable documents automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        },
    }
)

CODEXOPS_DISPATCH = {
    "run_codex_task": lambda prompt="", sandbox="read-only": run_codex_task(prompt, sandbox),
    "codex_analyze": lambda prompt="": run_codex_task(prompt, "read-only"),
    "codex_generate_artifact": lambda prompt="": codex_generate_artifact(prompt),
}
