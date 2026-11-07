import hashlib
import hmac
import json
import sqlite3

import pytest

from disputedesk import audit
from disputedesk.contracts import AuditEvent, AuditRecord

TEST_KEY = "test-key-" + "k" * 32
WRONG_KEY = "wrong-key-" + "w" * 32


@pytest.fixture(autouse=True)
def audit_key(monkeypatch):
    monkeypatch.setenv(audit.KEY_ENV, TEST_KEY)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def seed(conn, n=4):
    return [
        audit.append(conn, AuditRecord(f"DSP-{'AB'[i % 2]}", AuditEvent.CE3_CHECKED, "ce3-agent", {"i": i, "note": "ünï"}))
        for i in range(n)
    ]


def row(conn, record_id):
    return conn.execute(
        "SELECT prev_hash, dispute_id, event, actor, payload, created_at FROM audit_log WHERE record_id = ?", (record_id,)
    ).fetchone()


def test_empty_log_verifies(conn):
    assert audit.verify_chain(conn) == (True, None)


def test_append_links_hashes_and_returns_new_record(conn):
    original = AuditRecord("DSP-A", AuditEvent.DISPOSITION, "policy", {"outcome": "AUTO_EXECUTE"})
    first = audit.append(conn, original)
    second = audit.append(conn, AuditRecord("DSP-A", AuditEvent.AUTO_APPROVED, "policy"))
    assert original.record_id is None and original.hash == ""
    assert first.record_id == 1 and first.prev_hash == audit.GENESIS_HASH and len(first.hash) == 64
    assert first.created_at.endswith("+00:00")
    assert second.record_id == 2 and second.prev_hash == first.hash
    assert audit.verify_chain(conn) == (True, None)


def test_hash_is_hmac_of_prev_plus_canonical_body(conn):
    rec = audit.append(conn, AuditRecord("DSP-A", AuditEvent.SUBMITTED, "gw", {"r": 1}, created_at="2026-11-06T00:00:00+00:00"))
    body = {"record_id": 1, "dispute_id": "DSP-A", "event": "SUBMITTED", "actor": "gw", "payload": {"r": 1},
            "created_at": "2026-11-06T00:00:00+00:00"}
    msg = (audit.GENESIS_HASH + audit.canonical_json(body)).encode()
    assert rec.hash == hmac.new(TEST_KEY.encode(), msg, hashlib.sha256).hexdigest()


def test_explicit_key_overrides_env(conn, monkeypatch):
    monkeypatch.delenv(audit.KEY_ENV)
    audit.append(conn, AuditRecord("DSP-A", AuditEvent.SUBMITTED, "gw"), key=TEST_KEY.encode())
    assert audit.verify_chain(conn, key=TEST_KEY) == (True, None)
    assert audit.verify_chain(conn, key=WRONG_KEY) == (False, 1)


def test_get_and_list_for_round_trip(conn):
    records = seed(conn)
    assert audit.get(conn, 2) == records[1]
    assert isinstance(audit.get(conn, 2).event, AuditEvent)
    assert audit.get(conn, 99) is None
    assert audit.list_for(conn, "DSP-A") == [records[0], records[2]]


def test_works_with_row_factory():
    from disputedesk.db import connect
    c = connect(":memory:")
    rec = audit.append(c, AuditRecord("DSP-A", AuditEvent.SUBMITTED, "gateway", {"receipt": "R1"}))
    assert audit.get(c, rec.record_id) == rec and audit.verify_chain(c) == (True, None)
    assert audit.head(c) == (rec.record_id, rec.hash)


def test_rejects_non_serialisable_payload(conn):
    with pytest.raises(ValueError, match="JSON"):
        audit.append(conn, AuditRecord("DSP-A", AuditEvent.SUBMITTED, "x", {"bad": object()}))
    assert audit.verify_chain(conn) == (True, None)
    assert audit.get(conn, 1) is None


@pytest.mark.parametrize("fn", ["append", "verify"])
def test_missing_key_raises(conn, monkeypatch, fn):
    monkeypatch.delenv(audit.KEY_ENV)
    with pytest.raises(audit.AuditKeyError, match=audit.KEY_ENV):
        if fn == "append":
            audit.append(conn, AuditRecord("DSP-A", AuditEvent.SUBMITTED, "x"))
        else:
            audit.verify_chain(conn)


@pytest.mark.parametrize("short", ["s3cr3t-tiny", b"x" * 31, "y" * 31])
def test_short_key_rejected_without_leaking_it(conn, short):
    with pytest.raises(audit.AuditKeyError, match="too short") as exc:
        audit.append(conn, AuditRecord("DSP-A", AuditEvent.SUBMITTED, "x"), key=short)
    assert str(short) not in str(exc.value)


def test_head(conn):
    assert audit.head(conn) == (None, audit.GENESIS_HASH)
    records = seed(conn, 3)
    assert audit.head(conn) == (3, records[-1].hash)


@pytest.mark.parametrize(
    "sql,bad_id",
    [
        ("UPDATE audit_log SET payload = '{\"i\":99}' WHERE record_id = 2", 2),
        ("UPDATE audit_log SET event = 'HUMAN_APPROVED' WHERE record_id = 3", 3),
        ("UPDATE audit_log SET actor = 'mallory' WHERE record_id = 1", 1),
        ("UPDATE audit_log SET created_at = '2020-01-01T00:00:00+00:00' WHERE record_id = 4", 4),
        ("UPDATE audit_log SET hash = '" + "f" * 64 + "' WHERE record_id = 2", 2),
        ("UPDATE audit_log SET hash = 'ünï' WHERE record_id = 2", 2),
        ("DELETE FROM audit_log WHERE record_id = 2", 3),
        ("UPDATE audit_log SET record_id = 10 WHERE record_id = 1", 2),
    ],
)
def test_tampering_detected(conn, sql, bad_id):
    seed(conn)
    conn.execute(sql)
    conn.commit()
    assert audit.verify_chain(conn) == (False, bad_id)


def test_recomputed_hash_with_right_key_still_breaks_next_link(conn):
    seed(conn)
    prev, dispute_id, event, actor, _, created_at = row(conn, 2)
    forged_payload = {"i": 1, "note": "forged"}
    forged = audit._compute_hash(TEST_KEY.encode(), prev, 2, dispute_id, event, actor, forged_payload, created_at)
    conn.execute("UPDATE audit_log SET payload = ?, hash = ? WHERE record_id = 2", (audit.canonical_json(forged_payload), forged))
    conn.commit()
    assert audit.verify_chain(conn) == (False, 3)


def _forge_append(conn, digest_fn):
    _, last_hash = audit.head(conn)
    body = {"record_id": 5, "dispute_id": "DSP-A", "event": "HUMAN_APPROVED", "actor": "shroddha",
            "payload": {}, "created_at": "2026-11-06T00:00:00+00:00"}
    message = (last_hash + audit.canonical_json(body)).encode()
    conn.execute(
        "INSERT INTO audit_log VALUES (5, 'DSP-A', 'HUMAN_APPROVED', 'shroddha', '{}', ?, ?, ?)",
        (body["created_at"], last_hash, digest_fn(message)),
    )
    conn.commit()


@pytest.mark.parametrize(
    "digest_fn",
    [
        lambda m: hashlib.sha256(m).hexdigest(),
        lambda m: hmac.new(WRONG_KEY.encode(), m, hashlib.sha256).hexdigest(),
    ],
    ids=["plain-sha256", "hmac-wrong-key"],
)
def test_keyless_attacker_append_detected(conn, digest_fn):
    seed(conn)
    _forge_append(conn, digest_fn)
    assert audit.verify_chain(conn) == (False, 5)


def test_keyless_attacker_rewrites_and_rechains_detected_at_rewritten_row(conn):
    seed(conn)
    conn.execute("UPDATE audit_log SET event = 'HUMAN_APPROVED' WHERE record_id = 2")
    prev = row(conn, 2)[0]
    for record_id in (2, 3, 4):
        _, dispute_id, event, actor, payload, created_at = row(conn, record_id)
        digest = audit._compute_hash(WRONG_KEY.encode(), prev, record_id, dispute_id, event, actor, json.loads(payload), created_at)
        conn.execute("UPDATE audit_log SET prev_hash = ?, hash = ? WHERE record_id = ?", (prev, digest, record_id))
        prev = digest
    conn.commit()
    assert audit.verify_chain(conn) == (False, 2)


def test_append_continues_chain_after_existing_rows(tmp_path):
    path = tmp_path / "a.db"
    c1 = sqlite3.connect(path)
    first = audit.append(c1, AuditRecord("DSP-A", AuditEvent.CE3_CHECKED, "a"))
    c1.close()
    c2 = sqlite3.connect(path)
    second = audit.append(c2, AuditRecord("DSP-A", AuditEvent.DISPOSITION, "a"))
    assert second.prev_hash == first.hash and audit.verify_chain(c2) == (True, None)
    c2.close()
