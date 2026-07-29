"""Command-line document library audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import validate as validate_schema

from .model import report_schema
from .service import QualityGateError, validate_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ops.docs.quality.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate a document library manifest")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--json", dest="json_path", type=Path)
    check.add_argument("--changed-file", action="append", default=[])
    check.add_argument("--scheduled", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        reports = validate_manifest(
            args.manifest,
            changed=set(args.changed_file),
            scheduled=args.scheduled,
        )
    except (OSError, json.JSONDecodeError, QualityGateError) as exc:
        print(f"document quality: error: {exc}", file=sys.stderr)
        return 2
    payload = {
        "schema_version": "1.0",
        "reports": [report.as_dict() for report in reports],
        "status": "fail" if any(report.failed for report in reports) else "pass",
    }
    for report in reports:
        print(
            f"{report.as_dict()['status'].upper():4} {report.source} "
            f"({len(report.findings)} findings, {report.digest[:12]})"
        )
    if args.json_path:
        for report in payload["reports"]:
            validate_schema(report, report_schema())
        args.json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 1 if payload["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
