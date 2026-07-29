"""Daily call-count guard for paid tool paths.

Provider-priced calls (Codex delegation today; extend as needed) are counted
per UTC day in the local database. When a tool's daily limit is reached the
call is refused with a typed error instead of spending. Limits are
env-overridable and fail open only in the sense that a counting failure never
blocks work; the default limits are generous guards against runaway loops,
not accounting.
"""

from __future__ import annotations

import os
import sqlite3
import time

DEFAULT_LIMITS = {"codex": 100}


def check_and_count(db_path, name: str) -> str | None:
    """Count one call; return an error string when today's limit is exhausted."""
    limit = int(os.getenv(f"PJ_{name.upper()}_DAILY_CALL_LIMIT", DEFAULT_LIMITS.get(name, 0)))
    if limit <= 0:
        return None
    day = time.strftime("%Y-%m-%d", time.gmtime())
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS pj_daily_spend_counts ("
                "name TEXT NOT NULL, day TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY (name, day))"
            )
            row = conn.execute(
                "SELECT calls FROM pj_daily_spend_counts WHERE name=? AND day=?",
                (name, day),
            ).fetchone()
            calls = row[0] if row else 0
            if calls >= limit:
                return (
                    f"daily_limit_reached: '{name}' has used {calls}/{limit} calls today; "
                    f"raise PJ_{name.upper()}_DAILY_CALL_LIMIT to continue."
                )
            conn.execute(
                "INSERT INTO pj_daily_spend_counts (name, day, calls) VALUES (?,?,1) "
                "ON CONFLICT(name, day) DO UPDATE SET calls = calls + 1",
                (name, day),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return None
