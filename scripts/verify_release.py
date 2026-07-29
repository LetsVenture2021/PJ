#!/usr/bin/env python3
"""Compare a release manifest with sanitized local/remote health observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ops.shared.continuity import compare_release


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("observations", type=Path, help="JSON produced by deployment health probes")
    args = parser.parse_args()
    mismatches = compare_release(
        json.loads(args.manifest.read_text()), json.loads(args.observations.read_text())
    )
    if mismatches:
        print("release parity mismatch: " + ", ".join(mismatches))
        return 1
    print("release parity verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
