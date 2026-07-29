#!/usr/bin/env python3
"""Create or verify PJ signed release manifests without printing key material."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from ops.shared.continuity import build_release_manifest, sign_manifest, verify_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("create", "verify"))
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--key", type=Path, required=True, help="PEM private key for create; public key for verify"
    )
    args = parser.parse_args()
    key_data = args.key.read_bytes()
    if args.command == "verify":
        key = serialization.load_pem_public_key(key_data)
        verify_manifest(json.loads(args.manifest.read_text()), key)
        print("release manifest signature verified")
        return 0
    key = serialization.load_pem_private_key(key_data, password=None)
    root = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    routes = [
        "/health",
        "/session",
        "/token",
        "/tool-schemas",
        "/execute-tool",
        "/responses/*",
        "/upload/*",
    ]
    required = [
        "PJ_ALLOWED_ORIGINS",
        "CF_ACCESS_TEAM_DOMAIN",
        "CF_ACCESS_AUD",
        "PJ_TOOL_BRIDGE_URL",
        "PJ_TOOL_SCHEMAS_URL",
        "OPENAI_API_KEY",
        "PJ_OWNER_EMAILS",
        "PJ_TOOL_BRIDGE_TOKEN",
    ]
    payload = build_release_manifest(
        root,
        git_commit=commit,
        routes=routes,
        required_config_keys=required,
        deployed_at=datetime.now(timezone.utc).isoformat(),
    )
    args.manifest.write_text(
        json.dumps(sign_manifest(payload, key), indent=2, sort_keys=True) + "\n"
    )
    print(f"wrote signed release manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
