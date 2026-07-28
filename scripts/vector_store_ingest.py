#!/usr/bin/env python3
"""Upload an approved text source, cache it locally, and index it for PJ."""
import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import skillops  # noqa: E402


def _api_key(keychain_service: str) -> str:
    value = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if value:
        return value
    if not keychain_service:
        raise ValueError(
            "OPENAI_API_KEY is unset and no Keychain service was provided"
        )
    completed = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            keychain_service,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise RuntimeError("the configured Keychain service could not be read")
    return completed.stdout.strip()


def _cached_file_id(source_sha256: str, filename: str) -> str:
    cache_dir = skillops._VECTOR_SOURCE_CACHE_DIR
    if not cache_dir.is_dir():
        return ""
    for manifest_path in cache_dir.glob("file-*.json"):
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise RuntimeError(f"unsafe cache manifest: {manifest_path.name}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("source_sha256") == source_sha256
            and manifest.get("filename") == filename
        ):
            return skillops._safe_file_id(manifest.get("file_id"))
    return ""


def _membership(api_key: str, vector_store_id: str,
                file_id: str) -> tuple[int, dict]:
    response = requests.get(
        f"https://api.openai.com/v1/vector_stores/"
        f"{vector_store_id}/files/{file_id}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    if response.status_code == 404:
        return response.status_code, {}
    response.raise_for_status()
    return response.status_code, response.json()


def _wait_until_indexed(api_key: str, vector_store_id: str,
                        file_id: str, timeout_seconds: int) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while True:
        _, result = _membership(api_key, vector_store_id, file_id)
        status = result.get("status")
        if status == "completed":
            return result
        if status in {"failed", "cancelled"}:
            raise RuntimeError(
                f"vector indexing ended with {status}: "
                f"{result.get('last_error')}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError("timed out waiting for vector indexing")
        time.sleep(3)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Upload and cache a UTF-8 source so PJ can automatically import "
            "supported DocOps, CodeOps, coding, or governed n8n ITEM blocks."
        )
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--filename", default="")
    parser.add_argument(
        "--corpus-type",
        choices=("codeops", "docops", "n8n", "other"),
        default="other",
    )
    parser.add_argument("--version", default="")
    parser.add_argument(
        "--evaluation-receipt",
        type=Path,
        help="Required independent JSON evaluation receipt for an n8n corpus",
    )
    parser.add_argument(
        "--keychain-service",
        default="pj-openai-api-key",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
    )
    args = parser.parse_args()

    source_input = args.source.expanduser()
    if source_input.is_symlink():
        raise ValueError("source must be a regular non-symlink file")
    source = source_input.resolve(strict=True)
    if not source.is_file():
        raise ValueError("source must be a regular non-symlink file")
    if source.stat().st_size > skillops.MAX_MAX_CHARS_PER_FILE:
        raise ValueError(
            f"source exceeds {skillops.MAX_MAX_CHARS_PER_FILE} bytes"
        )
    content = source.read_bytes()
    if len(content) > skillops.MAX_MAX_CHARS_PER_FILE:
        raise ValueError(
            f"source exceeds {skillops.MAX_MAX_CHARS_PER_FILE} bytes"
        )
    source_text = content.decode("utf-8")

    filename = str(args.filename or source.name)
    if not filename or Path(filename).name != filename:
        raise ValueError("filename must be a basename")
    source_sha256 = hashlib.sha256(content).hexdigest()
    corpus_type = args.corpus_type
    version = str(args.version or "")
    n8n_preflight = None
    n8n_evaluation_receipt = None
    if args.corpus_type == "n8n":
        if args.evaluation_receipt is None:
            raise ValueError(
                "--evaluation-receipt is required for an n8n corpus"
            )
        n8n_evaluation_receipt = skillops.load_n8n_evaluation_receipt(
            args.evaluation_receipt
        )
        n8n_preflight = skillops.preflight_n8n_corpus_text(
            source_text,
            n8n_evaluation_receipt,
        )
        if not n8n_preflight["ingestion_ready"]:
            raise ValueError(
                "n8n corpus failed local preflight before credential access: "
                + "; ".join(n8n_preflight["errors"][:5])
            )
        corpus_type = skillops.N8N_CORPUS_TYPE
        declared_version = str(n8n_preflight.get("corpus_version") or "")
        if version and version != declared_version:
            raise ValueError(
                "--version does not match the n8n corpus_version "
                f"({version!r} != {declared_version!r})"
            )
        version = declared_version

    api_key = _api_key(args.keychain_service)
    vector_store_id = skillops._require_vector_store_id()
    file_id = _cached_file_id(source_sha256, filename)
    reused = False

    if file_id:
        metadata_response = requests.get(
            f"https://api.openai.com/v1/files/{file_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        if metadata_response.status_code == 200:
            metadata = metadata_response.json()
            reused = (
                metadata.get("filename") == filename
                and int(metadata.get("bytes") or -1) == len(content)
            )
        elif metadata_response.status_code != 404:
            metadata_response.raise_for_status()

    if not reused:
        handle = io.BytesIO(content)
        response = requests.post(
            "https://api.openai.com/v1/files",
            headers={"Authorization": f"Bearer {api_key}"},
            data={"purpose": "user_data"},
            files={"file": (filename, handle, "text/markdown")},
            timeout=180,
        )
        response.raise_for_status()
        file_id = response.json()["id"]

    membership_status, membership = _membership(
        api_key, vector_store_id, file_id
    )
    if membership_status == 404:
        response = requests.post(
            f"https://api.openai.com/v1/vector_stores/"
            f"{vector_store_id}/files",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "file_id": file_id,
                "attributes": {
                    "source_sha256": source_sha256,
                    "corpus_type": corpus_type,
                    "version": version,
                },
            },
            timeout=60,
        )
        response.raise_for_status()
        membership = response.json()

    membership = _wait_until_indexed(
        api_key,
        vector_store_id,
        file_id,
        max(30, min(int(args.timeout_seconds), 3600)),
    )
    cached = skillops.cache_vector_source(
        file_id,
        content,
        filename=filename,
        source_sha256=source_sha256,
    )
    evaluation_cache = None
    if n8n_evaluation_receipt is not None:
        evaluation_cache = skillops.cache_n8n_evaluation_receipt(
            source_sha256,
            n8n_evaluation_receipt,
        )
    print(json.dumps({
        "status": "indexed_and_cached",
        "file_id": file_id,
        "filename": filename,
        "bytes": len(content),
        "source_sha256": source_sha256,
        "vector_store_id": vector_store_id,
        "indexing_status": membership.get("status"),
        "cache_status": cached["status"],
        "reused": reused,
        "corpus_type": corpus_type,
        "corpus_version": version,
        "preflight": n8n_preflight,
        "evaluation_cache": evaluation_cache,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
