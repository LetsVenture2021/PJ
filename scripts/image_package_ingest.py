#!/usr/bin/env python3
"""Fail-closed preflight for the canonical PJ image training package."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import imageops
from openai import OpenAI


def _existing_chunk_hashes(client, vector_store_id: str) -> set[str]:
    hashes = set()
    after = None
    while True:
        page = client.vector_stores.files.list(
            vector_store_id=vector_store_id,
            limit=100,
            **({"after": after} if after else {}),
        )
        data = list(page.data or [])
        for item in data:
            attributes = getattr(item, "attributes", None) or {}
            value = attributes.get("pj_image_chunk_sha256")
            if isinstance(value, str):
                hashes.add(value)
        if not getattr(page, "has_more", False) or not data:
            return hashes
        after = data[-1].id


def _wait_for_attachment(client, vector_store_id: str, file_id: str) -> None:
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        item = client.vector_stores.files.retrieve(
            vector_store_id=vector_store_id,
            file_id=file_id,
        )
        if item.status == "completed":
            return
        if item.status == "failed":
            raise imageops.ImageOpsError(
                "vector_ingestion_failed",
                f"hosted-vector indexing failed for file {file_id}",
            )
        time.sleep(2)
    raise imageops.ImageOpsError(
        "vector_ingestion_timeout",
        f"hosted-vector indexing timed out for file {file_id}",
    )


def _ingest(manifest_path: Path, inspection: dict) -> dict:
    approved = os.getenv("PJ_IMAGE_APPROVED_PACKAGE_SHA256", "").strip()
    if approved != inspection["manifest_sha256"]:
        raise imageops.ImageOpsError(
            "package_not_approved",
            "manifest SHA-256 does not match PJ_IMAGE_APPROVED_PACKAGE_SHA256",
        )
    vector_store_id = os.getenv("PJ_IMAGE_VECTOR_STORE_ID", "").strip()
    if not vector_store_id:
        raise imageops.ImageOpsError(
            "vector_store_unconfigured",
            "PJ_IMAGE_VECTOR_STORE_ID is required for hosted-vector ingestion",
        )
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise imageops.ImageOpsError(
            "provider_unconfigured",
            "OPENAI_API_KEY is required for hosted-vector ingestion",
        )
    client = OpenAI()
    existing = _existing_chunk_hashes(client, vector_store_id)
    uploaded = []
    skipped = []
    for chunk in inspection["chunks"]:
        if chunk["sha256"] in existing:
            skipped.append(chunk["filename"])
            continue
        path = manifest_path.parent / chunk["filename"]
        before = path.read_bytes()
        if hashlib.sha256(before).hexdigest() != chunk["sha256"]:
            raise imageops.ImageOpsError(
                "upload_toctou",
                f"chunk changed after preflight: {chunk['filename']}",
            )
        stream = io.BytesIO(before)
        stream.name = chunk["filename"]
        created = client.files.create(file=stream, purpose="assistants")
        attachment = client.vector_stores.files.create(
            vector_store_id=vector_store_id,
            file_id=created.id,
            attributes={
                "pj_image_package_sha256": inspection["manifest_sha256"],
                "pj_image_package_version": inspection["package_version"][:256],
                "pj_image_chunk_sha256": chunk["sha256"],
            },
        )
        _wait_for_attachment(client, vector_store_id, attachment.id)
        uploaded.append({
            "filename": chunk["filename"],
            "file_id": created.id,
            "attachment_id": attachment.id,
            "sha256": chunk["sha256"],
        })
        existing.add(chunk["sha256"])
    return {
        "status": "completed",
        "manifest_sha256": inspection["manifest_sha256"],
        "package_version": inspection["package_version"],
        "uploaded": uploaded,
        "skipped_existing": skipped,
        "vector_store_id": vector_store_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="perform approved hosted-vector mutation after full local preflight",
    )
    args = parser.parse_args()
    try:
        result = imageops.inspect_training_package(args.manifest)
        if args.ingest:
            result = _ingest(Path(args.manifest).resolve(), result)
        else:
            result["remote_mutation"] = "not_requested"
    except imageops.ImageOpsError as exc:
        print(json.dumps(exc.as_dict(), sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
