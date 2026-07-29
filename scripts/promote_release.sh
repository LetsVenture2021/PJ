#!/usr/bin/env bash
# Staged, credential-safe promotion. Provider commands are injected by the operator.
set -euo pipefail

usage() {
  echo "Usage: $0 client|worker|runtime RELEASE_ID MANIFEST RECEIPT_DIR" >&2
  echo "Requires PJ_PREFLIGHT_CMD, PJ_PROMOTE_CMD, PJ_HEALTH_CMD; optional PJ_ROLLBACK_REF." >&2
}
[[ $# == 4 ]] || { usage; exit 2; }
stage="$1" release_id="$2" manifest="$3" receipt_dir="$4"
[[ "$stage" =~ ^(client|worker|runtime)$ ]] || { usage; exit 2; }
[[ -r "$manifest" ]] || { echo "manifest is not readable" >&2; exit 1; }
: "${PJ_PREFLIGHT_CMD:?PJ_PREFLIGHT_CMD is required}"
: "${PJ_PROMOTE_CMD:?PJ_PROMOTE_CMD is required}"
: "${PJ_HEALTH_CMD:?PJ_HEALTH_CMD is required}"

# Commands must source credentials themselves; this script never echoes environment or output.
bash -c "$PJ_PREFLIGHT_CMD" >/dev/null
bash -c "$PJ_PROMOTE_CMD" >/dev/null
bash -c "$PJ_HEALTH_CMD" >/dev/null
mkdir -p "$receipt_dir"
receipt="$receipt_dir/${release_id}-${stage}.json"
[[ ! -e "$receipt" ]] || { echo "immutable receipt already exists" >&2; exit 1; }
python3 - "$receipt" "$stage" "$release_id" "$manifest" "${PJ_ROLLBACK_REF:-none}" <<'PY'
import hashlib, json, os, sys
from datetime import datetime, timezone
path, stage, release_id, manifest, rollback = sys.argv[1:]
body = {"release_id": release_id, "stage": stage, "manifest_hash": hashlib.sha256(open(manifest, "rb").read()).hexdigest(), "rollback_pointer": rollback, "promoted_at": datetime.now(timezone.utc).isoformat(), "health_verified": True}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
with os.fdopen(fd, "w") as stream:
    json.dump(body, stream, sort_keys=True); stream.write("\n")
PY
printf 'promoted %s; immutable receipt: %s\n' "$stage" "$receipt"
