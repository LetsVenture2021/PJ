#!/usr/bin/env bash
set -euo pipefail

attempts="${NPM_AUDIT_ATTEMPTS:-3}"
delay_seconds="${NPM_AUDIT_RETRY_DELAY_SECONDS:-5}"

for attempt in $(seq 1 "$attempts"); do
  set +e
  npm audit "$@"
  status=$?
  set -e

  if [[ "$status" -eq 0 ]]; then
    exit 0
  fi

  if [[ "$attempt" -eq "$attempts" ]]; then
    exit "$status"
  fi

  echo "npm audit failed on attempt ${attempt}/${attempts}; retrying in ${delay_seconds}s..." >&2
  sleep "$delay_seconds"
done
