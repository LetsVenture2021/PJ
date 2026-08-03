#!/usr/bin/env python3
"""Preview or explicitly apply the governed document-library inventory."""

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.docs.quality_profiles import QUALITY_PROFILES  # noqa: E402
from ops.shared.io import read_json, sha256_file, write_json_atomic  # noqa: E402
from ops.shared.sqlite import atomic_sqlite_connection  # noqa: E402

DEFAULT_MANIFEST = ROOT / "documents" / "library-manifest.json"
DEFAULT_DB = ROOT / "pj_data.sqlite3"
CURRENT_RECORD_SCHEMA_VERSION = "1.0.0"
RELATIONSHIPS = (
    "supersedes",
    "superseded_by",
    "derived_from",
    "supports",
    "references",
    "generated_artifacts",
)


def _stable_id(path: str) -> str:
    token = hashlib.sha256(path.encode()).hexdigest()[:12].upper()
    return f"DOC-{token}"


def _existing_stable_id(previous: dict, relative: str) -> str:
    legacy_document_id = previous.get("document_id")
    if isinstance(legacy_document_id, str) and legacy_document_id:
        legacy_stable_id = legacy_document_id.upper()
    else:
        legacy_stable_id = None
    return previous.get("stable_id") or legacy_stable_id or _stable_id(relative)


def _validated_previous_schema_version(previous: dict, relative: str) -> None:
    version = previous.get("schema_version")
    if version is not None and version != CURRENT_RECORD_SCHEMA_VERSION:
        raise ValueError(
            f"{relative} uses unsupported document metadata schema_version {version!r}"
        )


def propose_records(manifest_path: Path = DEFAULT_MANIFEST) -> list[dict]:
    """Return proposed records without changing any file or database."""
    current = {}
    if manifest_path.exists():
        current = {r["path"]: r for r in read_json(manifest_path)["documents"]}
    records = []
    for folder in (ROOT / "documents", ROOT / "docs"):
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            if path.resolve() == manifest_path.resolve() or "exports" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            previous = current.get(relative, {})
            _validated_previous_schema_version(previous, relative)
            document_class = previous.get(
                "class", "structured-data" if path.suffix == ".json" else "technical"
            )
            records.append(
                {
                    "schema_version": CURRENT_RECORD_SCHEMA_VERSION,
                    "path": relative,
                    "stable_id": _existing_stable_id(previous, relative),
                    "class": document_class,
                    "owner": previous.get("owner", "PJ DocOps"),
                    "status": previous.get("status", "published"),
                    "classification": previous.get("classification", "internal"),
                    "source_of_truth": previous.get("source_of_truth", True),
                    "content_sha256": sha256_file(path),
                    "last_reviewed_at": previous.get("last_reviewed_at"),
                    "next_review_at": previous.get("next_review_at"),
                    "quality_profile": previous.get("quality_profile", document_class),
                    **{relation: previous.get(relation, []) for relation in RELATIONSHIPS},
                }
            )
    return records


def _import_database(path: Path, records: list[dict]) -> None:
    with atomic_sqlite_connection(path) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS docops_manifest_records (
            stable_id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            document_class TEXT NOT NULL, owner TEXT NOT NULL,
            lifecycle_status TEXT NOT NULL, source_of_truth INTEGER NOT NULL DEFAULT 0,
            content_sha256 TEXT NOT NULL, last_reviewed_at TEXT, next_review_at TEXT,
            quality_profile TEXT, imported_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS docops_relationships (
            stable_id TEXT NOT NULL, relationship TEXT NOT NULL, target_id TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stable_id, relationship, target_id))""")
        for record in records:
            connection.execute(
                "INSERT OR IGNORE INTO docops_manifest_records "
                "(stable_id,path,document_class,owner,lifecycle_status,source_of_truth,"
                "content_sha256,last_reviewed_at,next_review_at,quality_profile) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    record["stable_id"],
                    record["path"],
                    record["class"],
                    record["owner"],
                    record["status"],
                    record["source_of_truth"],
                    record["content_sha256"],
                    record["last_reviewed_at"],
                    record["next_review_at"],
                    record["quality_profile"],
                ),
            )
            for relationship in RELATIONSHIPS:
                for target in record[relationship]:
                    connection.execute(
                        "INSERT OR IGNORE INTO docops_relationships "
                        "(stable_id, relationship, target_id) VALUES (?,?,?)",
                        (record["stable_id"], relationship, target),
                    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="atomically replace the inventory; never corpus files",
    )
    parser.add_argument(
        "--apply-db",
        action="store_true",
        help="atomically import new records (existing records are immutable)",
    )
    args = parser.parse_args()
    records = propose_records(args.manifest)
    for record in records:
        print(f"PROPOSE {record['stable_id']} {record['path']} {record['content_sha256']}")
    if args.write_manifest:
        manifest = {
            "schema_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "inventory_scope": ["documents/", "docs/"],
            "excluded_paths": ["documents/library-manifest.json", "documents/exports/"],
            "quality_profile_versions": {
                name: profile.version for name, profile in QUALITY_PROFILES.items()
            },
            "documents": records,
        }
        write_json_atomic(args.manifest, manifest, mode=0o644)
        print(f"WROTE {args.manifest}")
    if args.apply_db:
        _import_database(args.database, records)
        print(f"IMPORTED {args.database}")
    if not args.write_manifest and not args.apply_db:
        print("DRY RUN: no files or database rows changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
