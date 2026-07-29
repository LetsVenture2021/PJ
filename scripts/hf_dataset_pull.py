#!/usr/bin/env python3
"""Paginated pull from the Hugging Face datasets-server into a local JSONL file.

Operator workflow, not an assistant tool. Public datasets need no token; gated
datasets require HF_TOKEN. Every pull writes a manifest next to the JSONL so an
ingest can cite its source, and the pulled content is always marked untrusted.
The license field is recorded verbatim from the operator: the datasets-server
API does not expose license terms, so confirm them on the dataset card before
embedding pulled content anywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://datasets-server.huggingface.co"
PAGE = 100  # server-enforced maximum for `length`
MAX_ROWS_DEFAULT = 25_000  # fail-closed cap; raise deliberately
RETRY_STATUS = {429, 500, 502, 503, 504}
LICENSE_UNVERIFIED = "UNVERIFIED - check the dataset card before ingestion"


def _get(path: str, params: dict[str, str]) -> dict:
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    headers = {"accept": "application/json", "user-agent": "pj-dataset-pull/1"}
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["authorization"] = f"Bearer {token}"
    for attempt in range(8):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=60
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUS or attempt == 7:
                raise SystemExit(f"datasets-server error {exc.code}: {url}")
            time.sleep(2**attempt)
    raise SystemExit("unreachable")


def pull(
    dataset: str,
    config: str,
    split: str,
    out: Path,
    *,
    max_rows: int = MAX_ROWS_DEFAULT,
    license_text: str = LICENSE_UNVERIFIED,
    fetch=_get,
) -> dict:
    first = fetch(
        "rows",
        {"dataset": dataset, "config": config, "split": split, "offset": "0", "length": "1"},
    )
    total = min(int(first["num_rows_total"]), max_rows)
    features = [feature["name"] for feature in first["features"]]

    out.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    written = 0
    truncated_cells = 0
    with out.open("w", encoding="utf-8") as handle:
        for offset in range(0, total, PAGE):
            page = fetch(
                "rows",
                {
                    "dataset": dataset,
                    "config": config,
                    "split": split,
                    "offset": str(offset),
                    "length": str(min(PAGE, total - offset)),
                },
            )
            for item in page["rows"]:
                # truncated_cells means the server clipped a large value:
                # record it rather than silently ingesting partial text.
                truncated_cells += len(item.get("truncated_cells") or [])
                line = json.dumps(
                    {"row_idx": item["row_idx"], **item["row"]},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                handle.write(line + "\n")
                digest.update(line.encode("utf-8"))
                written += 1
            if fetch is _get:
                time.sleep(0.5)  # be a polite client

    manifest = {
        "dataset": dataset,
        "config": config,
        "split": split,
        "rows_available": int(first["num_rows_total"]),
        "rows_written": written,
        "features": features,
        "truncated_cells": truncated_cells,
        "sha256": digest.hexdigest(),
        "source": f"{BASE}/rows",
        "license": license_text,
        "pulled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "trusted": False,
    }
    out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="NousResearch/hermes-function-calling-v1")
    parser.add_argument("--config", default="func_calling_singleturn")
    parser.add_argument("--split", default="train")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("documents/datasets/hermes_fc_v1.jsonl"),
    )
    parser.add_argument("--max-rows", type=int, default=MAX_ROWS_DEFAULT)
    parser.add_argument(
        "--license",
        dest="license_text",
        default=LICENSE_UNVERIFIED,
        help="License terms confirmed on the dataset card, recorded in the manifest.",
    )
    args = parser.parse_args()
    manifest = pull(
        args.dataset,
        args.config,
        args.split,
        args.out,
        max_rows=args.max_rows,
        license_text=args.license_text,
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
