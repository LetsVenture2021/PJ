#!/usr/bin/env python3
"""Discover process variants and bottlenecks from PJ structured logs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ops.shared.io import write_json_atomic  # noqa: E402
from ops.shared.process_mining import analyze_jsonl  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mine metadata-only PJ JSONL logs for variants, latency, and failures."
    )
    parser.add_argument("log", help="Path to a PJ structured JSONL log")
    parser.add_argument("--output", help="Optional JSON report path (stdout by default)")
    args = parser.parse_args()
    report = analyze_jsonl(args.log)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        write_json_atomic(args.output, report)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
