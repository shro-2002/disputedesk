"""HMAC-chained, tamper-evident audit log in SQLite. Owner: W2.

Each row's hash = HMAC-SHA256(key, prev_hash + canonical_json(body)), hex; body includes
record_id so deleting or reordering rows breaks the chain. Tamper-evident against anyone
without the key (env AUDIT_HMAC_KEY or explicit key=): they cannot forge, append, or
recompute a valid row. Only deterministic code and the gateway hold the key, never agents.
Tail truncation (deleting the newest rows) is still undetectable from the chain alone; it
needs an external anchor: the room-posted head hash from head().
"""

import hashlib
import hmac
import json
import os
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

from disputedesk.contracts import AuditEvent, AuditRecord

GENESIS_HASH = "0" * 64
KEY_ENV = "AUDIT_HMAC_KEY"
MIN_KEY_LENGTH = 32


class AuditKeyError(RuntimeError):
    """The audit HMAC key is missing or too weak. Never carries the key itself."""


def _resolve_key(key: bytes | str | None) -> bytes:
    if key is None:
        key = os.environ.get(KEY_ENV)
    if not key:
        raise AuditKeyError(f"audit HMAC key missing: pass key= or set {KEY_ENV}")
    if len(key) < MIN_KEY_LENGTH:
        raise AuditKeyError(f"audit HMAC key too short: need at least {MIN_KEY_LENGTH} characters")
    return key.encode("utf-8") if isinstance(key, str) else bytes(key)
COLUMNS = "record_id, dispute_id, event, actor, payload, created_at, prev_hash, hash"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispute_id TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
)
"""


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _compute_hash(key: bytes, prev_hash: str, record_id: int, dispute_id: str, event: str, actor: str, payload, created_at: str) -> str:
    body = {
        "record_id": record_id,
        "dispute_id": dispute_id,
        "event": event,
        "actor": actor,
        "payload": payload,
        "created_at": created_at,
    }
    message = (prev_hash + canonical_json(body)).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)


def _payload_text(payload) -> str:
    if not isinstance(payload, dict):
        raise ValueError(f"audit payload must be a dict, got {type(payload).__name__}")
    try:
        return canonical_json(payload)
    except (TypeError, ValueError) as err:
        raise ValueError(f"audit payload is not JSON-serialisable: {err}") from err


def append(conn: sqlite3.Connection, record: AuditRecord, *, key: bytes | str | None = None) -> AuditRecord:
    secret = _resolve_key(key)
    event = AuditEvent(record.event).value  # rejects unknown events
    payload_text = _payload_text(record.payload)
    payload = json.loads(payload_text)
    created_at = record.created_at or datetime.now(timezone.utc).isoformat()
    _ensure_table(conn)

    owns_txn = not conn.in_transaction
    if owns_txn:
        conn.execute("BEGIN IMMEDIATE")
    try:
        last = conn.execute("SELECT record_id, hash FROM audit_log ORDER BY record_id DESC LIMIT 1").fetchone()
        seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'audit_log'").fetchone()
        record_id = max(seq[0] if seq else 0, last[0] if last else 0) + 1
        prev_hash = last[1] if last else GENESIS_HASH
        digest = _compute_hash(secret, prev_hash, record_id, record.dispute_id, event, record.actor, payload, created_at)
        conn.execute(
            f"INSERT INTO audit_log ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, record.dispute_id, event, record.actor, payload_text, created_at, prev_hash, digest),
        )
        if owns_txn:
            conn.commit()
    except BaseException:
        if owns_txn:
            conn.rollback()
        raise
    return replace(
        record,
        event=AuditEvent(event),
        payload=payload,
        created_at=created_at,
        record_id=record_id,
        prev_hash=prev_hash,
        hash=digest,
    )


def _row_to_record(row) -> AuditRecord:
    return AuditRecord(
        record_id=row[0],
        dispute_id=row[1],
        event=AuditEvent(row[2]),
        actor=row[3],
        payload=json.loads(row[4]),
        created_at=row[5],
        prev_hash=row[6],
        hash=row[7],
    )


def get(conn: sqlite3.Connection, record_id: int) -> AuditRecord | None:
    _ensure_table(conn)
    row = conn.execute(f"SELECT {COLUMNS} FROM audit_log WHERE record_id = ?", (record_id,)).fetchone()
    return _row_to_record(row) if row else None


def list_for(conn: sqlite3.Connection, dispute_id: str) -> list[AuditRecord]:
    _ensure_table(conn)
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM audit_log WHERE dispute_id = ? ORDER BY record_id", (dispute_id,)
    ).fetchall()
    return [_row_to_record(row) for row in rows]


def _same(stored, expected: str) -> bool:
    # bytes compare: tampered cells may be non-ASCII or non-text, which str compare_digest rejects
    return hmac.compare_digest(str(stored).encode("utf-8"), expected.encode("utf-8"))


def head(conn: sqlite3.Connection) -> tuple[int | None, str]:
    """(latest record_id, latest hash); (None, GENESIS_HASH) when empty. Post it as the anchor."""
    _ensure_table(conn)
    row = conn.execute("SELECT record_id, hash FROM audit_log ORDER BY record_id DESC LIMIT 1").fetchone()
    return (row[0], row[1]) if row else (None, GENESIS_HASH)


def verify_chain(conn: sqlite3.Connection, *, key: bytes | str | None = None) -> tuple[bool, int | None]:
    secret = _resolve_key(key)
    _ensure_table(conn)
    expected_prev = GENESIS_HASH
    for row in conn.execute(f"SELECT {COLUMNS} FROM audit_log ORDER BY record_id"):
        record_id, dispute_id, event, actor, payload_text, created_at, prev_hash, digest = tuple(row)
        try:
            payload = json.loads(payload_text)
        except (TypeError, ValueError):
            return False, record_id
        recomputed = _compute_hash(secret, prev_hash, record_id, dispute_id, event, actor, payload, created_at)
        if not (_same(prev_hash, expected_prev) and _same(digest, recomputed)):
            return False, record_id
        expected_prev = digest
    return True, None
