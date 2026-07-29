#!/usr/bin/env python3
"""Audit PJ's governed document library without reading content into output."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops.docs.quality import audit_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persist", action="store_true", help="persist hash-bound reports")
    parser.add_argument("--blocking", action="store_true", help="fail when critical findings exist")
    args = parser.parse_args()
    result = audit_manifest(persist=args.persist)
    summary = {key: result[key] for key in ("documents", "passing", "failing", "findings")}
    print(json.dumps(summary, sort_keys=True))
    return int(args.blocking and result["failing"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
