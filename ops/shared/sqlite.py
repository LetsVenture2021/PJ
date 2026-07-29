"""Transaction helpers for atomic SQLite state changes."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def atomic_sqlite_connection(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Commit all changes together, or roll the transaction back on failure."""
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
