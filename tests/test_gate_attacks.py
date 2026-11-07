"""V2 gate-integrity attack tests (round 2). Gateway wired to the REAL keyed audit module.

Plain failing tests = real defects. `test_trust_*` pass and document an accepted trust
boundary (the HMAC key holder, the receipts store).
"""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from disputedesk import audit
from disputedesk.contracts import AuditEvent, AuditRecord
from gateway.app import create_app

E = AuditEvent
KEY = "test-hmac-key-0123456789abcdef0123456789"
ATTACKER_KEY = "attacker-guess-0123456789abcdef01234567"
LEAD = "shroddha"
LIMIT = 50_000  # $500.00
AUTO_OK = {"amount_cents": 18_000, "outcome": "AUTO_EXECUTE"}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    yield c
    c.close()


def make_app(conn, receipts_db=":memory:", **overrides):
    lock = threading.Lock()  # shared sqlite conn across the gateway threadpool

    def locked(fn):
        def call(*a):
            with lock:
                return fn(*a)
        return call

    kwargs = dict(
        list_for=locked(lambda d: audit.list_for(conn, d)),
        finops_lead=LEAD,
        auto_limit_cents=LIMIT,
    )
    kwargs.update(overrides)
    return create_app(
        locked(lambda i: audit.get(conn, i)),
        locked(lambda: audit.verify_chain(conn, key=KEY)[0]),
        receipts_db,
        **kwargs,
    )


@pytest.fixture
def client(conn):
    return TestClient(make_app(conn), raise_server_exceptions=False)


def add(conn, dispute, event, actor="policy", payload=None):
    if payload is None and event is E.AUTO_APPROVED:
        payload = AUTO_OK
    return audit.append(conn, AuditRecord(dispute, event, actor, payload or {}), key=KEY).record_id


def post(client, action, ref, dispute="DSP-A"):
    h = {} if ref is None else {"X-Approval-Ref": str(ref)}
    return client.post(f"/disputes/{dispute}/{action}", headers=h)


def code(r):
    return r.json()["error"]["code"]


def n_receipts(client, dispute="DSP-A"):
    return len(client.get(f"/disputes/{dispute}/receipts").json()["receipts"])


def recompute_from(conn, start_id, key):
    """Re-hash every row from start_id forward with `key`."""
    prev = conn.execute("SELECT hash FROM audit_log WHERE record_id = ?", (start_id - 1,)).fetchone()
    prev_hash = prev[0] if prev else audit.GENESIS_HASH
    rows = conn.execute(
        "SELECT record_id, dispute_id, event, actor, payload, created_at FROM audit_log"
        " WHERE record_id >= ? ORDER BY record_id",
        (start_id,),
    ).fetchall()
    k = key.encode()
    for rid, d, ev, actor, payload, created in rows:
        h = audit._compute_hash(k, prev_hash, rid, d, ev, actor, json.loads(payload), created)
        conn.execute("UPDATE audit_log SET prev_hash = ?, hash = ? WHERE record_id = ?", (prev_hash, h, rid))
        prev_hash = h
    conn.commit()


def forge_tail(conn, key, dispute, event, actor, payload):
    last_id, last_hash = conn.execute("SELECT record_id, hash FROM audit_log ORDER BY record_id DESC").fetchone()
    rid, created = last_id + 1, "2026-11-06T00:00:00+00:00"
    h = audit._compute_hash(key.encode(), last_hash, rid, dispute, event, actor, payload, created)
    conn.execute(
        "INSERT INTO audit_log (record_id, dispute_id, event, actor, payload, created_at, prev_hash, hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, dispute, event, actor, json.dumps(payload), created, last_hash, h),
    )
    conn.commit()
    return rid


# --- header parsing --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw",
    [b"+1", b"01", b"0x1", b"1e0", b"1_0", b"1 1", b"1,1", b"9" * 19, b"\xb9", "١".encode(), b"-0"],
)
def test_malformed_refs_never_reach_lookup(conn, raw):
    add(conn, "DSP-A", E.AUTO_APPROVED)
    calls = []
    app = create_app(lambda i: calls.append(i) or audit.get(conn, i), lambda: True, ":memory:",
                     list_for=lambda d: [], finops_lead=LEAD, auto_limit_cents=LIMIT)
    c = TestClient(app)
    r = c.post("/disputes/DSP-A/representment", headers=[(b"x-approval-ref", raw)])
    assert r.status_code == 403 and code(r) in {"APPROVAL_INVALID", "APPROVAL_REQUIRED"}
    assert calls == [] and n_receipts(c) == 0


def test_surrounding_whitespace_is_same_ref_not_a_bypass(client, conn):
    ref = add(conn, "DSP-A", E.AUTO_APPROVED)
    assert post(client, "representment", f"  {ref}\t").status_code == 201
    assert post(client, "representment", str(ref)).status_code == 409


@pytest.mark.parametrize("order", [("bad", "good"), ("good", "bad")])
def test_duplicate_headers_judged_on_picked_value(client, conn, order):
    good = add(conn, "DSP-A", E.AUTO_APPROVED)
    bad = add(conn, "DSP-A", E.APPROVAL_DENIED, "mallory")
    vals = {"bad": str(bad).encode(), "good": str(good).encode()}
    r = client.post("/disputes/DSP-A/representment", headers=[(b"x-approval-ref", vals[o]) for o in order])
    if r.status_code == 201:
        assert r.json()["approval_ref"] == good
    else:
        assert code(r) == "APPROVAL_WRONG_TYPE"


# --- binding to dispute / action / single use -------------------------------------------------

def test_approval_for_A_cannot_move_money_on_C(client, conn):
    ref = add(conn, "DSP-A", E.HUMAN_APPROVED, LEAD)
    r = post(client, "representment", ref, dispute="DSP-C")
    assert r.status_code == 403 and code(r) == "APPROVAL_WRONG_DISPUTE"
    assert n_receipts(client, "DSP-C") == 0


@pytest.mark.parametrize("event", [E.HUMAN_REJECTED, E.APPROVAL_DENIED, E.DISPOSITION, E.SUBMITTED, E.CE3_CHECKED])
@pytest.mark.parametrize("action", ["representment", "accept"])
def test_non_authorising_events_refused(client, conn, event, action):
    ref = add(conn, "DSP-A", event, LEAD, AUTO_OK)
    r = post(client, action, ref)
    assert r.status_code == 403 and code(r) == "APPROVAL_WRONG_TYPE"


def test_ref_cannot_cross_actions(client, conn):
    rep = add(conn, "DSP-A", E.AUTO_APPROVED)
    acc = add(conn, "DSP-A", E.ACCEPT_LOSS_APPROVED)
    assert post(client, "accept", rep).status_code == 403
    assert post(client, "representment", acc).status_code == 403
    assert post(client, "representment", rep).status_code == 201
    assert n_receipts(client) == 1


def test_replay_after_success_is_409(client, conn):
    ref = add(conn, "DSP-A", E.AUTO_APPROVED)
    assert post(client, "representment", ref).status_code == 201
    for _ in range(3):
        r = post(client, "representment", ref)
        assert r.status_code == 409 and code(r) == "APPROVAL_ALREADY_USED"
    assert n_receipts(client) == 1


def test_concurrent_replay_exactly_one_success(conn):
    ref = add(conn, "DSP-A", E.HUMAN_APPROVED, LEAD)
    with TestClient(make_app(conn)) as c, ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(lambda _: post(c, "representment", ref).status_code, range(32)))
        assert codes.count(201) == 1 and codes.count(409) == 31
        assert n_receipts(c) == 1


def test_concurrent_different_refs_same_dispute_exactly_one_settles(conn):
    refs = [add(conn, "DSP-A", E.AUTO_APPROVED) for _ in range(8)]
    refs += [add(conn, "DSP-A", E.ACCEPT_LOSS_APPROVED) for _ in range(8)]
    with TestClient(make_app(conn)) as c, ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(
            lambda r: post(c, "representment" if r <= 8 else "accept", r).status_code, refs))
        assert codes.count(201) == 1 and codes.count(409) == 15
        assert n_receipts(c) == 1


def test_second_money_move_on_same_dispute_refused(client, conn):
    a = add(conn, "DSP-A", E.AUTO_APPROVED)
    b = add(conn, "DSP-A", E.ACCEPT_LOSS_APPROVED)
    c2 = add(conn, "DSP-A", E.HUMAN_APPROVED, LEAD)
    assert post(client, "representment", a).status_code == 201
    for action, ref in (("accept", b), ("representment", c2)):
        r = post(client, action, ref)
        assert r.status_code == 409 and code(r) == "DISPUTE_ALREADY_SETTLED"
    assert n_receipts(client) == 1


def test_trust_receipts_in_memory_means_restart_allows_replay(conn):
    # Single-use + settle-once live ONLY in receipts_db. serve.py must pass a persistent file.
    ref = add(conn, "DSP-A", E.AUTO_APPROVED)
    assert post(TestClient(make_app(conn, ":memory:")), "representment", ref).status_code == 201
    assert post(TestClient(make_app(conn, ":memory:")), "representment", ref).status_code == 201


def test_file_backed_receipts_survive_restart(conn, tmp_path):
    db = str(tmp_path / "receipts.db")
    ref = add(conn, "DSP-A", E.AUTO_APPROVED)
    assert post(TestClient(make_app(conn, db)), "representment", ref).status_code == 201
    assert post(TestClient(make_app(conn, db)), "representment", ref).status_code == 409


# --- HUMAN_APPROVED actor / revocation --------------------------------------------------------

@pytest.mark.parametrize("actor", ["mallory", "Shroddha", "shroddha ", " shroddha", "shroddha​", ""])
def test_human_approved_by_non_lead_refused(client, conn, actor):
    ref = add(conn, "DSP-C", E.HUMAN_APPROVED, actor)
    r = post(client, "representment", ref, dispute="DSP-C")
    assert r.status_code == 403 and code(r) == "APPROVAL_NOT_AUTHORISED"


def test_revoked_approval_refused(client, conn):
    ref = add(conn, "DSP-C", E.HUMAN_APPROVED, LEAD)
    add(conn, "DSP-C", E.HUMAN_REJECTED, LEAD)
    r = post(client, "representment", ref, dispute="DSP-C")
    assert r.status_code == 403 and code(r) == "APPROVAL_REVOKED"


def test_reject_then_reapprove_uses_newer_approval_only(client, conn):
    old = add(conn, "DSP-C", E.HUMAN_APPROVED, LEAD)
    add(conn, "DSP-C", E.HUMAN_REJECTED, LEAD)
    new = add(conn, "DSP-C", E.HUMAN_APPROVED, LEAD)
    assert code(post(client, "representment", old, dispute="DSP-C")) == "APPROVAL_REVOKED"
    assert post(client, "representment", new, dispute="DSP-C").status_code == 201


def test_rejection_on_other_dispute_does_not_revoke(client, conn):
    ref = add(conn, "DSP-C", E.HUMAN_APPROVED, LEAD)
    add(conn, "DSP-A", E.HUMAN_REJECTED, LEAD)
    assert post(client, "representment", ref, dispute="DSP-C").status_code == 201


def test_rejection_revokes_auto_approval_too(client, conn):
    ref = add(conn, "DSP-A", E.AUTO_APPROVED)
    add(conn, "DSP-A", E.HUMAN_REJECTED, LEAD)
    assert code(post(client, "representment", ref)) == "APPROVAL_REVOKED"


def test_denied_attempt_does_not_revoke(client, conn):
    ref = add(conn, "DSP-C", E.HUMAN_APPROVED, LEAD)
    add(conn, "DSP-C", E.APPROVAL_DENIED, "mallory")
    assert post(client, "representment", ref, dispute="DSP-C").status_code == 201


def test_non_lead_rejection_revokes_fail_closed(client, conn):
    """Accepted: a HUMAN_REJECTED by anyone revokes. It can only block money, never move it."""
    ref = add(conn, "DSP-C", E.HUMAN_APPROVED, LEAD)
    add(conn, "DSP-C", E.HUMAN_REJECTED, "mallory")
    assert code(post(client, "representment", ref, dispute="DSP-C")) == "APPROVAL_REVOKED"


def test_settle_then_reject_keeps_single_receipt(client, conn):
    ref = add(conn, "DSP-C", E.HUMAN_APPROVED, LEAD)
    assert post(client, "representment", ref, dispute="DSP-C").status_code == 201
    add(conn, "DSP-C", E.HUMAN_REJECTED, LEAD)
    assert post(client, "representment", ref, dispute="DSP-C").status_code in {403, 409}
    assert n_receipts(client, "DSP-C") == 1


# --- AUTO_APPROVED payload --------------------------------------------------------------------

def test_auto_approved_over_limit_refused(client, conn):
    ref = add(conn, "DSP-C", E.AUTO_APPROVED, "policy", {"amount_cents": 240_000, "outcome": "AUTO_EXECUTE"})
    r = post(client, "representment", ref, dispute="DSP-C")
    assert r.status_code == 403 and code(r) == "APPROVAL_OVER_LIMIT"


def test_auto_approved_exactly_at_limit_accepted(client, conn):
    ref = add(conn, "DSP-A", E.AUTO_APPROVED, "policy", {"amount_cents": LIMIT, "outcome": "PROCEED_FLAGGED"})
    assert post(client, "representment", ref).status_code == 201


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"outcome": "AUTO_EXECUTE"},
        {"amount_cents": 100},
        {"amount_cents": 100.0, "outcome": "AUTO_EXECUTE"},
        {"amount_cents": 1e20, "outcome": "AUTO_EXECUTE"},
        {"amount_cents": "100", "outcome": "AUTO_EXECUTE"},
        {"amount_cents": True, "outcome": "AUTO_EXECUTE"},
        {"amount_cents": False, "outcome": "AUTO_EXECUTE"},
        {"amount_cents": None, "outcome": "AUTO_EXECUTE"},
        {"amount_cents": -1, "outcome": "AUTO_EXECUTE"},
        {"amount_cents": [100], "outcome": "AUTO_EXECUTE"},
        {"amount_cents": 100, "outcome": "auto_execute"},
        {"amount_cents": 100, "outcome": "AUTO_EXECUTE "},
        {"amount_cents": 100, "outcome": "HUMAN_REVIEW"},
        {"amount_cents": 100, "outcome": "REJECT"},
        {"amount_cents": 100, "outcome": None},
        {"amount_cents": 100, "outcome": ["AUTO_EXECUTE"]},
        {"amount_cents": 100, "outcome": {"x": 1}},
    ],
)
def test_malformed_auto_payload_refused_with_403(client, conn, payload):
    ref = add(conn, "DSP-A", E.AUTO_APPROVED, "policy", payload)
    r = post(client, "representment", ref)
    assert r.status_code == 403 and code(r) == "APPROVAL_INVALID"
    assert n_receipts(client) == 0


def test_huge_int_amount_is_over_limit(client, conn):
    ref = add(conn, "DSP-A", E.AUTO_APPROVED, "policy", {"amount_cents": 10**30, "outcome": "AUTO_EXECUTE"})
    assert code(post(client, "representment", ref)) == "APPROVAL_OVER_LIMIT"


# --- path / method tricks ---------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/disputes/dsp-a/representment", "/disputes/DSP-A%2Frepresentment",
                                  "/disputes/DSP-A/representment/", "/disputes/%20DSP-A/representment",
                                  "/disputes/DSP-A%00/representment", "/disputes/../disputes/DSP-A/representment"])
def test_path_tricks_never_2xx_without_valid_ref(client, conn, path):
    add(conn, "dsp-a", E.AUTO_APPROVED)
    r = client.post(path, headers={"X-Approval-Ref": "999"})
    assert not 200 <= r.status_code < 300


def test_trailing_newline_dispute_id_is_rejected_as_invalid(client, conn):
    ref = add(conn, "DSP-A", E.AUTO_APPROVED)
    r = client.post("/disputes/DSP-A%0A/representment", headers={"X-Approval-Ref": str(ref)})
    assert r.status_code == 422 and code(r) == "DISPUTE_ID_INVALID"


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("action", ["representment", "accept"])
def test_method_confusion_refused(client, conn, method, action):
    ref = add(conn, "DSP-A", E.AUTO_APPROVED if action == "representment" else E.ACCEPT_LOSS_APPROVED)
    r = client.request(method, f"/disputes/DSP-A/{action}", headers={"X-Approval-Ref": str(ref)})
    assert r.status_code == 405 and n_receipts(client) == 0


# --- audit failures: 503, no leak -------------------------------------------------------------

SECRET_MSG = "secret-path /var/db/disputedesk.db " + KEY


def _boom(*_):
    raise sqlite3.OperationalError(SECRET_MSG)


@pytest.mark.parametrize("which", ["lookup", "chain", "list_for"])
def test_dependency_failure_is_503_without_leak(conn, which):
    ref = add(conn, "DSP-A", E.AUTO_APPROVED)
    args = {
        "lookup": lambda i: audit.get(conn, i),
        "chain": lambda: audit.verify_chain(conn, key=KEY)[0],
        "list_for": lambda d: audit.list_for(conn, d),
    }
    args[which] = _boom
    app = create_app(args["lookup"], args["chain"], ":memory:", list_for=args["list_for"],
                     finops_lead=LEAD, auto_limit_cents=LIMIT)
    c = TestClient(app, raise_server_exceptions=False)
    r = post(c, "representment", ref)
    assert r.status_code == 503 and code(r) == "AUDIT_UNAVAILABLE"
    assert "secret" not in r.text and KEY not in r.text and "OperationalError" not in r.text
    assert n_receipts(c) == 0


def test_gateway_missing_key_fails_closed(conn, monkeypatch):
    ref = add(conn, "DSP-A", E.AUTO_APPROVED)
    monkeypatch.delenv(audit.KEY_ENV, raising=False)
    app = create_app(lambda i: audit.get(conn, i), lambda: audit.verify_chain(conn)[0], ":memory:",
                     list_for=lambda d: audit.list_for(conn, d), finops_lead=LEAD, auto_limit_cents=LIMIT)
    r = post(TestClient(app, raise_server_exceptions=False), "representment", ref)
    assert r.status_code == 503 and "AUDIT_HMAC_KEY" not in r.text


def test_unknown_event_string_in_db_is_not_2xx(client, conn):
    ref = add(conn, "DSP-A", E.AUTO_APPROVED)
    conn.execute("UPDATE audit_log SET event = 'HUMAN_APPROVED ' WHERE record_id = ?", (ref,))
    recompute_from(conn, ref, KEY)
    assert not 200 <= post(client, "representment", ref).status_code < 300


# --- tampering without / with the key ---------------------------------------------------------

def test_naive_edit_rejected_to_approved_is_caught(client, conn):
    add(conn, "DSP-C", E.DISPOSITION)
    ref = add(conn, "DSP-C", E.HUMAN_REJECTED, LEAD)
    add(conn, "DSP-C", E.CE3_CHECKED)
    conn.execute("UPDATE audit_log SET event = 'HUMAN_APPROVED' WHERE record_id = ?", (ref,))
    conn.commit()
    assert audit.verify_chain(conn, key=KEY) == (False, ref)
    r = post(client, "representment", ref, dispute="DSP-C")
    assert r.status_code == 403 and code(r) == "AUDIT_CHAIN_BROKEN"


def test_edit_redirecting_approval_to_other_dispute_is_caught(client, conn):
    ref = add(conn, "DSP-A", E.HUMAN_APPROVED, LEAD)
    add(conn, "DSP-A", E.SUBMITTED)
    conn.execute("UPDATE audit_log SET dispute_id = 'DSP-C' WHERE record_id = ?", (ref,))
    conn.commit()
    assert code(post(client, "representment", ref, dispute="DSP-C")) == "AUDIT_CHAIN_BROKEN"


@pytest.mark.parametrize("attacker_key", [ATTACKER_KEY, None])
def test_forged_tail_row_without_key_refused(client, conn, attacker_key):
    add(conn, "DSP-C", E.DISPOSITION)
    if attacker_key is None:  # old unkeyed SHA-256 construction
        import hashlib
        last_id, last_hash = conn.execute("SELECT record_id, hash FROM audit_log ORDER BY record_id DESC").fetchone()
        rid, created = last_id + 1, "2026-11-06T00:00:00+00:00"
        body = {"record_id": rid, "dispute_id": "DSP-C", "event": "HUMAN_APPROVED", "actor": LEAD,
                "payload": {}, "created_at": created}
        h = hashlib.sha256((last_hash + audit.canonical_json(body)).encode()).hexdigest()
        conn.execute("INSERT INTO audit_log VALUES (?, 'DSP-C', 'HUMAN_APPROVED', ?, '{}', ?, ?, ?)",
                     (rid, LEAD, created, last_hash, h))
        conn.commit()
    else:
        rid = forge_tail(conn, attacker_key, "DSP-C", "HUMAN_APPROVED", LEAD, {})
    assert audit.verify_chain(conn, key=KEY) == (False, rid)
    r = post(client, "representment", rid, dispute="DSP-C")
    assert r.status_code == 403 and code(r) == "AUDIT_CHAIN_BROKEN"


def test_mid_chain_rewrite_with_rehash_without_key_refused(client, conn):
    add(conn, "DSP-C", E.DISPOSITION)
    ref = add(conn, "DSP-C", E.HUMAN_REJECTED, LEAD)
    for _ in range(3):
        add(conn, "DSP-A", E.CE3_CHECKED)
    conn.execute("UPDATE audit_log SET event = 'HUMAN_APPROVED' WHERE record_id = ?", (ref,))
    recompute_from(conn, ref, ATTACKER_KEY)
    assert audit.verify_chain(conn, key=KEY)[0] is False
    assert code(post(client, "representment", ref, dispute="DSP-C")) == "AUDIT_CHAIN_BROKEN"


def test_deleting_revocation_row_breaks_chain(client, conn):
    ref = add(conn, "DSP-C", E.HUMAN_APPROVED, LEAD)
    rej = add(conn, "DSP-C", E.HUMAN_REJECTED, LEAD)
    add(conn, "DSP-A", E.CE3_CHECKED)
    conn.execute("DELETE FROM audit_log WHERE record_id = ?", (rej,))
    conn.commit()
    assert code(post(client, "representment", ref, dispute="DSP-C")) == "AUDIT_CHAIN_BROKEN"


def test_trust_tail_truncation_of_revocation_is_undetectable(client, conn):
    """Known ceiling: deleting the newest row (a revocation) passes verify_chain; only the
    room-posted head() anchor reveals it, and the gateway does not compare against it."""
    ref = add(conn, "DSP-C", E.HUMAN_APPROVED, LEAD)
    rej = add(conn, "DSP-C", E.HUMAN_REJECTED, LEAD)
    anchored = audit.head(conn)
    conn.execute("DELETE FROM audit_log WHERE record_id = ?", (rej,))
    conn.commit()
    assert audit.head(conn) != anchored
    assert post(client, "representment", ref, dispute="DSP-C").status_code == 201


def test_trust_attacker_with_key_can_mint_any_approval(client, conn):
    """Accepted trust boundary: the key holder IS the approver. Also note the gateway trusts
    the self-declared payload amount; DSP-C is $2,400 but the forged payload says $1."""
    add(conn, "DSP-C", E.DISPOSITION)
    rid = forge_tail(conn, KEY, "DSP-C", "AUTO_APPROVED", "llm-agent",
                     {"amount_cents": 100, "outcome": "AUTO_EXECUTE"})
    assert audit.verify_chain(conn, key=KEY) == (True, None)
    assert post(client, "representment", rid, dispute="DSP-C").status_code == 201


# --- configuration --------------------------------------------------------------------------

@pytest.mark.parametrize("overrides", [{"finops_lead": ""}, {"finops_lead": None}, {"auto_limit_cents": None},
                                       {"auto_limit_cents": -1}, {"auto_limit_cents": "50000"}])
def test_bad_gateway_config_refused_at_startup(conn, overrides):
    """Misconfiguration must fail at create_app, not per request (finops_lead='' would accept an
    actor='' HUMAN_APPROVED; auto_limit_cents=None turns every auto approval into a 500)."""
    with pytest.raises((ValueError, TypeError)):
        make_app(conn, **overrides)
