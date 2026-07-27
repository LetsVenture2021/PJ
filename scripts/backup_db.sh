#!/bin/zsh
# Nightly consistent backup of pj_data.sqlite3 using SQLite's online backup.
# Keeps the 14 most recent backups in ~/PJ/backups/.
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
DB="$DIR/pj_data.sqlite3"
OUT="$DIR/backups"
mkdir -p "$OUT"
[ -f "$DB" ] || exit 0
STAMP=$(date +%Y%m%d-%H%M%S)
/usr/bin/sqlite3 "$DB" ".backup '$OUT/pj_data-$STAMP.sqlite3'"
# prune: keep newest 14
ls -t "$OUT"/pj_data-*.sqlite3 2>/dev/null | tail -n +15 | while read -r f; do rm -f "$f"; done
