"""Emit NUL-separated changed Python and document-quality inputs for CI."""

from __future__ import annotations

import argparse
import subprocess


def changed_files(base: str, head: str) -> list[str]:
    command = ["git", "diff", "--name-only", "--diff-filter=ACMR", "-z", base, head]
    return [item for item in subprocess.check_output(command).decode().split("\0") if item]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("head")
    parser.add_argument("--kind", choices=("python", "documents"), required=True)
    args = parser.parse_args()
    paths = changed_files(args.base, args.head)
    if args.kind == "python":
        selected = [path for path in paths if path.endswith(".py")]
    else:
        selected = [
            path
            for path in paths
            if path.startswith(
                ("docs/", "documents/", "ops/docs/quality/", "schemas/document-quality")
            )
        ]
    print("\0".join(selected), end="\0" if selected else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
