#!/bin/sh
set -eu

ROOT=${1:-"$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"}
PYTHON=${PJ_PYTHON:-"$ROOT/venv/bin/python"}
LOG_DIR=${PJ_LOG_DIR:-"$HOME/Library/Logs/PJ"}
KEYCHAIN_SERVICE=${PJ_OPENAI_KEYCHAIN_SERVICE:-"pj-openai-api-key"}
TEMPLATE="$ROOT/scripts/com.pj.vector-store-sync.plist"
DESTINATION="$HOME/Library/LaunchAgents/com.pj.vector-store-sync.plist"

if [ ! -x "$PYTHON" ]; then
  printf 'Python executable not found: %s\n' "$PYTHON" >&2
  exit 1
fi

mkdir -p "$LOG_DIR" "$(dirname "$DESTINATION")"
/usr/bin/sed \
  -e "s|__PJ_ROOT__|$ROOT|g" \
  -e "s|__PJ_PYTHON__|$PYTHON|g" \
  -e "s|__PJ_LOG_DIR__|$LOG_DIR|g" \
  -e "s|__PJ_KEYCHAIN_SERVICE__|$KEYCHAIN_SERVICE|g" \
  "$TEMPLATE" > "$DESTINATION"
/usr/bin/plutil -lint "$DESTINATION"
chmod 600 "$DESTINATION"

printf 'Installed %s\n' "$DESTINATION"
printf 'Store the API key without placing it in the plist:\n'
printf '  security add-generic-password -U -s %s -a "$USER" -w\n' \
  "$KEYCHAIN_SERVICE"
printf 'Then load the agent with: launchctl bootstrap gui/$(id -u) %s\n' \
  "$DESTINATION"
