"""Adversarial CE3.0 tests (verifier V1). Exercises edges the original suite did not."""

import ast
from datetime import date, timedelta
from pathlib import Path

import pytest

from db.seed import insert_dispute, insert_transaction, seed
from disputedesk import ce3 as ce3_module
from disputedesk.ce3 import check_ce3, load_dispute
from disputedesk.contracts import Dispute, Transaction
from disputedesk.db import connect

TODAY = date(2026, 6, 1)
IDENTITY = {
    "account_id": "acct-1",
    "ip_address": "203.0.113.7",
    "device_id": "dfp_abc",
    "shipping_address": "12 Main St, Springfield",
}
NO_MATCH = {"account_id": "x", "ip_address": "x", "device_id": "x", "shipping_address": "x"}


def only(*names):
    return {**NO_MATCH, **{n: IDENTITY[n] for n in names}}


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def _txn(txn_id, txn_date, ident, merchant="M1", cred="C1", customer="CU1", **flags):
    return Transaction(
        txn_id=txn_id, merchant_id=merchant, credential_id=cred, customer_id=customer,
        amount_cents=1000, txn_date=txn_date, **ident, **flags,
    )


def build(conn, priors, *, today=TODAY, disputed_ident=IDENTITY, reason_code="10.4"):
    """priors: list of (age_days, identity, extra). Disputed txn D is 5 days before `today`."""
    insert_transaction(conn, _txn("D", today - timedelta(days=5), disputed_ident))
    for i, (age, ident, extra) in enumerate(priors):
        insert_transaction(conn, _txn(f"P{i}", today - timedelta(days=age), ident, **extra))
    insert_dispute(conn, Dispute("DSP-T", "T", "D", "M1", "C1", "CU1", 1000, today, reason_code))
    return check_ce3(conn, "DSP-T")


# --- date window -----------------------------------------------------------

def test_prior_dated_after_dispute_is_ignored(conn):
    result = build(conn, [(150, IDENTITY, {}), (-10, IDENTITY, {})])
    assert not result.qualified
    assert "P1" not in result.matched_fields


def test_prior_on_dispute_date_is_not_eligible(conn):
    result = build(conn, [(150, IDENTITY, {}), (0, IDENTITY, {})])
    assert not result.qualified
    assert result.matched_txns == ("P0",)


def test_disputed_txn_never_counts_as_its_own_prior(conn):
    # Disputed txn itself sits inside the window and is the only other match.
    insert_transaction(conn, _txn("D", TODAY - timedelta(days=200), IDENTITY))
    insert_transaction(conn, _txn("P0", TODAY - timedelta(days=150), IDENTITY))
    insert_dispute(conn, Dispute("DSP-T", "T", "D", "M1", "C1", "CU1", 1000, TODAY))
    result = check_ce3(conn, "DSP-T")
    assert not result.qualified
    assert "D" not in result.matched_txns and "D" not in result.matched_fields


@pytest.mark.parametrize(
    "dispute_day, ages, qualified",
    [
        # Window spans Feb 29 2028: 120 / 365 calendar days back must be exact.
        (date(2028, 6, 28), (120, 365), True),
        (date(2028, 6, 28), (119, 365), False),
        (date(2028, 6, 28), (120, 366), False),
        (date(2028, 3, 1), (120, 365), True),
        (date(2029, 2, 28), (365, 364), True),
        (date(2029, 2, 28), (366, 200), False),
    ],
)
def test_leap_year_boundaries(conn, dispute_day, ages, qualified):
    result = build(conn, [(a, IDENTITY, {}) for a in ages], today=dispute_day)
    assert result.qualified is qualified


# --- element matching ------------------------------------------------------

def test_ip_plus_shipping_passes(conn):
    result = build(conn, [(150, only("ip_address", "shipping_address"), {})] * 1
                   + [(200, only("ip_address", "shipping_address"), {})])
    assert result.qualified
    assert result.matched_fields["P0"] == ("ip_address", "shipping_address")


def test_device_plus_account_passes(conn):
    ident = only("account_id", "device_id")
    assert build(conn, [(150, ident, {}), (200, ident, {})]).qualified


def test_account_plus_shipping_fails_with_strong_element_reason(conn):
    ident = only("account_id", "shipping_address")
    result = build(conn, [(150, ident, {}), (200, ident, {})])
    assert not result.qualified
    assert "IP address or device ID" in result.reasons[0]


def test_single_strong_element_alone_fails(conn):
    result = build(conn, [(150, only("ip_address"), {}), (200, only("device_id"), {})])
    assert not result.qualified


def test_different_element_pairs_per_prior_still_qualify(conn):
    result = build(conn, [(150, only("ip_address", "account_id"), {}),
                          (200, only("device_id", "shipping_address"), {})])
    assert result.qualified
    assert set(result.matched_txns) == {"P0", "P1"}


def test_ip_and_device_whitespace_and_case_variants_match(conn):
    variant = {**NO_MATCH, "ip_address": "  203.0.113.7 ", "device_id": "DFP_ABC\t"}
    assert build(conn, [(150, variant, {}), (200, variant, {})]).qualified


def test_empty_and_whitespace_elements_never_match(conn):
    blank = {"account_id": "", "ip_address": "   ", "device_id": "", "shipping_address": " "}
    result = build(conn, [(150, blank, {}), (200, blank, {})], disputed_ident=blank)
    assert not result.qualified
    assert result.matched_fields == {"P0": (), "P1": ()}


def test_ip_prefix_is_not_a_match(conn):
    near = {**NO_MATCH, "ip_address": "203.0.113.70", "device_id": "dfp_abcd"}
    assert not build(conn, [(150, near, {}), (200, near, {})]).qualified


def test_match_is_against_this_disputes_txn_not_another_customers(conn):
    # Another customer's disputed txn on a different card shares the priors' identity.
    other = {**NO_MATCH, "ip_address": "198.51.100.1", "device_id": "dfp_zzz"}
    insert_transaction(conn, _txn("OTHER-D", TODAY - timedelta(days=5), other, cred="C9", customer="CU9"))
    insert_dispute(conn, Dispute("DSP-O", "O", "OTHER-D", "M1", "C9", "CU9", 1000, TODAY))
    result = build(conn, [(150, other, {}), (200, other, {})])
    assert not result.qualified


# --- prior counting --------------------------------------------------------

def test_three_priors_two_qualify(conn):
    result = build(conn, [(150, IDENTITY, {}), (200, IDENTITY, {"has_fraud_report": True}),
                          (300, IDENTITY, {})])
    assert result.qualified
    assert set(result.matched_txns) == {"P0", "P2"}


def test_three_priors_only_one_qualifies(conn):
    result = build(conn, [(150, IDENTITY, {}), (200, IDENTITY, {"is_disputed": True}),
                          (60, IDENTITY, {})])
    assert not result.qualified
    assert result.matched_txns == ("P0",)


def test_prior_both_disputed_and_fraud_excluded(conn):
    flags = {"is_disputed": True, "has_fraud_report": True}
    result = build(conn, [(150, IDENTITY, flags), (200, IDENTITY, flags)])
    assert not result.qualified
    assert "disputed or fraud" in result.reasons[0]


def test_duplicate_txn_id_rejected_by_schema(conn):
    insert_transaction(conn, _txn("P0", TODAY - timedelta(days=150), IDENTITY))
    with pytest.raises(Exception):
        insert_transaction(conn, _txn("P0", TODAY - timedelta(days=200), IDENTITY))


# --- reason code -----------------------------------------------------------

def test_non_10_4_dispute_does_not_qualify(conn):
    """CE3.0 applies only to reason code 10.4; a 13.1 dispute must not be marked CE3-qualified."""
    result = build(conn, [(150, IDENTITY, {}), (200, IDENTITY, {})], reason_code="13.1")
    assert not result.qualified


# --- reasons wording -------------------------------------------------------

def test_too_old_reason_mentions_ages_and_window(conn):
    result = build(conn, [(400, IDENTITY, {}), (500, IDENTITY, {})])
    assert not result.qualified
    assert "400 and 500 days old" in result.reasons[0] and "365" in result.reasons[0]


def test_mixed_recent_and_old_reason_is_recency(conn):
    result = build(conn, [(30, IDENTITY, {}), (400, IDENTITY, {})])
    assert "30 and 400 days old" in result.reasons[0]


def test_exactly_one_reason_on_every_outcome(conn):
    result = build(conn, [(150, IDENTITY, {})])
    assert len(result.reasons) == 1


# --- input handling --------------------------------------------------------

@pytest.mark.parametrize("bad_id", ["NOPE", "' OR 1=1 --", "DSP-A'; DROP TABLE disputes; --", ""])
def test_unknown_or_injection_dispute_id_raises_keyerror(conn, bad_id):
    seed(conn, TODAY)
    with pytest.raises(KeyError):
        check_ce3(conn, bad_id)
    assert conn.execute("SELECT COUNT(*) FROM disputes").fetchone()[0] == 7


def test_dispute_pointing_to_missing_txn_raises(conn):
    conn.execute("PRAGMA foreign_keys = OFF")
    insert_dispute(conn, Dispute("DSP-X", "X", "GHOST", "M1", "C1", "CU1", 1000, TODAY))
    with pytest.raises(KeyError):
        check_ce3(conn, "DSP-X")


# --- seed --------------------------------------------------------------

@pytest.mark.parametrize(
    "dispute_id, amount, qualified, recent, prior_ages",
    [
        ("DSP-A", 18000, True, 0, (150, 210)),
        ("DSP-B", 22000, False, 0, ()),
        ("DSP-C", 240000, True, 0, None),
        ("DSP-D", 9500, False, 0, ()),
        ("DSP-E", 30000, True, 2, None),
    ],
)
@pytest.mark.parametrize("today", [date(2026, 11, 6), date(2028, 2, 29), date(2028, 3, 1)])
def test_seed_cases_any_today(conn, today, dispute_id, amount, qualified, recent, prior_ages):
    seed(conn, today)
    d = load_dispute(conn, dispute_id)
    r = check_ce3(conn, dispute_id)
    assert d.amount_cents == amount and d.dispute_date == today and d.reason_code == "10.4"
    assert r.qualified is qualified
    assert d.recent_dispute_count == recent
    if prior_ages is not None:
        ages = sorted((today - date.fromisoformat(
            conn.execute("SELECT txn_date FROM transactions WHERE txn_id=?", (t,)).fetchone()[0])).days
            for t in r.matched_txns)
        assert tuple(ages) == prior_ages


def test_seed_a_matches_exactly_ip_and_device(conn):
    seed(conn, TODAY)
    r = check_ce3(conn, "DSP-A")
    assert all(v == ("ip_address", "device_id") for v in r.matched_fields.values())


def test_seed_b_reason_plain_english_too_recent(conn):
    seed(conn, TODAY)
    reason = check_ce3(conn, "DSP-B").reasons[0]
    assert "45 and 90 days old" in reason and "120" in reason and "365" in reason


def test_seed_d_has_no_history(conn):
    seed(conn, TODAY)
    r = check_ce3(conn, "DSP-D")
    assert r.matched_fields == {} and r.reasons[0].startswith("No prior transactions")


def test_seed_is_idempotent_across_different_today(conn):
    seed(conn, TODAY)
    seed(conn, TODAY + timedelta(days=30))
    assert conn.execute("SELECT COUNT(*) FROM disputes").fetchone()[0] == 7
    assert check_ce3(conn, "DSP-A").qualified


# --- no LLM / network ----------------------------------------------------------

def test_ce3_module_imports_no_llm_or_network_libs():
    tree = ast.parse(Path(ce3_module.__file__).read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    banned = {"anthropic", "openai", "httpx", "requests", "urllib", "socket", "http", "aiohttp"}
    assert not (mods & banned), mods & banned


def test_check_ce3_works_with_sockets_disabled(conn, monkeypatch):
    import socket

    def boom(*a, **k):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    seed(conn, TODAY)
    assert check_ce3(conn, "DSP-A").qualified
