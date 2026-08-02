#!/usr/bin/env sh
set -eu

mkdir -p /data/n8n-receipts

if [ ! -f /data/pj_data.sqlite3 ]; then
  touch /data/pj_data.sqlite3
fi
ln -sfn /data/pj_data.sqlite3 /app/pj_data.sqlite3

mkdir -p /data/documents
if [ -d /app/documents ] && [ ! -L /app/documents ]; then
  cp -a /app/documents/. /data/documents/
  rm -rf /app/documents
fi
ln -sfn /data/documents /app/documents

exec python realtime_server.py
