#!/usr/bin/env python3
"""Run PJ's durable vector-store synchronization outside the web process."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import skillops  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize changed vector-store files into PJ DocOps."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-files", type=int, default=0)
    parser.add_argument(
        "--max-chars-per-file",
        type=int,
        default=skillops.DEFAULT_MAX_CHARS_PER_FILE,
    )
    parser.add_argument(
        "--no-overwrite-existing",
        dest="overwrite_existing",
        action="store_false",
        default=True,
    )
    parser.add_argument(
        "--exclude-provisional",
        dest="include_provisional",
        action="store_false",
        default=True,
    )
    parser.add_argument(
        "--keychain-service",
        default="",
        help=(
            "Optional macOS Keychain generic-password service containing "
            "OPENAI_API_KEY; used only when the environment variable is absent"
        ),
    )
    args = parser.parse_args()
    if not os.getenv("OPENAI_API_KEY") and args.keychain_service:
        completed = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                args.keychain_service,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if completed.returncode != 0:
            print(
                json.dumps({
                    "status": "failed",
                    "error": (
                        "OPENAI_API_KEY is unset and the configured Keychain "
                        "service could not be read"
                    ),
                }),
                file=sys.stderr,
            )
            return 1
        os.environ["OPENAI_API_KEY"] = completed.stdout.strip()
    result = skillops.sync_vector_store(
        dry_run=args.dry_run,
        force=args.force,
        max_files=args.max_files,
        max_chars_per_file=args.max_chars_per_file,
        overwrite_existing=args.overwrite_existing,
        include_provisional=args.include_provisional,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") in {
        "completed", "dry_run_complete", "locked"
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
