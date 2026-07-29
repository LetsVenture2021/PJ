#!/usr/bin/env python3
"""Durable upload-processing worker for local document extraction."""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process queued upload extraction jobs.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Process currently ready jobs and exit.")
    mode.add_argument("--watch", action="store_true", help="Continuously watch and process jobs.")
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds for --watch mode (default: 2.0).",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=100,
        help="Maximum jobs to process during one --once cycle (default: 100).",
    )
    parser.add_argument(
        "--worker-id",
        default="",
        help="Optional stable worker ID. Defaults to a host+pid identifier.",
    )
    return parser.parse_args()


def _default_worker_id() -> str:
    return f"upload-processor:{socket.gethostname()}:{os.getpid()}"


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from ops.docs import uploads as document_uploads

    args = _parse_args()
    worker_id = args.worker_id.strip() or _default_worker_id()
    if args.once:
        result = document_uploads.run_upload_processor_once(worker_id, max_jobs=args.max_jobs)
        print(
            f"processed={result['processed']} completed={result['completed']} "
            f"retried={result['retried']} failed={result['failed']}"
        )
        return 0

    document_uploads.run_upload_processor_watch(
        worker_id=worker_id,
        poll_interval_seconds=args.interval,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
