"""Deploy generated sites to Cloudflare Pages.

Publishing is an outward-facing action, so the tool is approval-gated.
The site content comes from a registered upload (typically produced by
codex_generate_artifact), never from arbitrary paths, and project names are
namespaced with a pj- prefix so PJ can only touch projects it created.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

PROJECT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
PROTECTED_PROJECTS = {"pj-assistant-web"}
DEPLOY_TIMEOUT_SECONDS = 300


def deploy_generated_site(upload_id: str = "", project_name: str = "") -> dict:
    from ops.docs import uploads as document_uploads

    name = str(project_name or "").strip().lower()
    if not PROJECT_NAME_PATTERN.fullmatch(name):
        return {"error": "project_name must be 3-41 chars: lowercase letters, digits, hyphens"}
    if not name.startswith("pj-"):
        name = f"pj-{name}"
    if name in PROTECTED_PROJECTS:
        return {"error": f"'{name}' is a protected project"}

    record = document_uploads.list_uploaded_documents(query=str(upload_id or ""), limit=100)
    files = [d for d in record.get("documents", []) if d["upload_id"] == upload_id]
    if not files:
        return {"error": f"no registered upload '{upload_id}'"}

    token = os.getenv("PJ_CLOUDFLARE_PAGES_WRITE_ALL_ACCOUNTS", "").strip()
    account = os.getenv("PJ_CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not token:
        return {"error": "PJ_CLOUDFLARE_PAGES_WRITE_ALL_ACCOUNTS is not configured"}

    with tempfile.TemporaryDirectory(prefix="pj-site-deploy-") as staging:
        site_dir = Path(staging)
        has_index = False
        for doc in files[:200]:
            relative = doc["saved_path"].split(f"{upload_id}/", 1)[-1]
            source = document_uploads.UPLOADS_DIR.joinpath(*Path(doc["saved_path"]).parts[1:])
            if not source.is_file():
                continue
            destination = site_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            if destination.name == "index.html":
                has_index = True
        single = list(site_dir.rglob("*.html"))
        if not has_index and len(single) == 1:
            (site_dir / "index.html").write_bytes(single[0].read_bytes())
            has_index = True
        if not has_index:
            return {"error": "the upload contains no index.html (or single HTML file)"}

        env = {**os.environ, "CLOUDFLARE_API_TOKEN": token}
        if account:
            env["CLOUDFLARE_ACCOUNT_ID"] = account
        try:
            create = subprocess.run(
                [
                    "npx",
                    "wrangler",
                    "pages",
                    "project",
                    "create",
                    name,
                    "--production-branch",
                    "main",
                ],
                capture_output=True,
                text=True,
                timeout=DEPLOY_TIMEOUT_SECONDS,
                env=env,
            )
            deploy = subprocess.run(
                [
                    "npx",
                    "wrangler",
                    "pages",
                    "deploy",
                    str(site_dir),
                    "--project-name",
                    name,
                    "--branch",
                    "main",
                    "--commit-message",
                    f"PJ deploy of {upload_id}",
                ],
                capture_output=True,
                text=True,
                timeout=DEPLOY_TIMEOUT_SECONDS,
                env=env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"error": f"deploy_failed: {str(exc)[:200]}"}
    if deploy.returncode != 0:
        detail = (deploy.stderr or deploy.stdout or "")[-300:]
        return {"error": f"deploy_failed: {detail}"}
    match = re.search(r"https://[a-z0-9.-]+\.pages\.dev", deploy.stdout or "")
    return {
        "status": "deployed",
        "project": name,
        "url": f"https://{name}.pages.dev",
        "deployment_url": match.group(0) if match else None,
        "project_created": create.returncode == 0,
    }


SITEOPS_SCHEMAS = [
    {
        "type": "function",
        "name": "deploy_generated_site",
        "description": (
            "Publish a generated site (a registered upload containing "
            "index.html, e.g. from codex_generate_artifact) to its own "
            "Cloudflare Pages project and return the public URL. Publishing "
            "is outward-facing and requires owner approval."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "upload_id": {"type": "string", "description": "UPL-... id of the site files"},
                "project_name": {
                    "type": "string",
                    "description": "short name; becomes pj-<name>.pages.dev",
                },
            },
            "required": ["upload_id", "project_name"],
        },
    }
]

SITEOPS_DISPATCH = {
    "deploy_generated_site": lambda upload_id="", project_name="": deploy_generated_site(
        upload_id, project_name
    )
}
