"""SQLite connection helper. Owner: lead. Applies db/schema.sql (owned by W1)."""

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
DEFAULT_DB = ROOT / "data" / "disputedesk.db"


def connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Open (and create if needed) the DisputeDesk database. ':memory:' is fine for tests."""
    db_path = str(path or os.environ.get("DISPUTEDESK_DB") or DEFAULT_DB)
    if db_path != ":memory:":
        resolved = Path(db_path)
        if not resolved.is_absolute():
            resolved = ROOT / resolved  # relative paths are relative to the repo, not the CWD
        resolved.parent.mkdir(parents=True, exist_ok=True)
        db_path = str(resolved)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if SCHEMA.exists():
        conn.executescript(SCHEMA.read_text())
    return conn
