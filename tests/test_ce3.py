from datetime import date, timedelta

import pytest

from db.seed import insert_dispute, insert_transaction, seed
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
IP_DEVICE = {**NO_MATCH, "ip_address": IDENTITY["ip_address"], "device_id": IDENTITY["device_id"]}


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def _txn(txn_id, age, ident, merchant="M1", cred="C1", **flags):
    return Transaction(
        txn_id=txn_id, merchant_id=merchant, credential_id=cred, customer_id="CU1",
        amount_cents=1000, txn_date=TODAY - timedelta(days=age), **ident, **flags,
    )


def scenario(conn, priors):
    """priors: list of (age_days, identity_dict, extra_kwargs). Returns CE3Result for DSP-T."""
    insert_transaction(conn, _txn("D", 5, IDENTITY))
    for i, (age, ident, extra) in enumerate(priors):
        insert_transaction(conn, _txn(f"P{i}", age, ident, **extra))
    insert_dispute(conn, Dispute("DSP-T", "T", "D", "M1", "C1", "CU1", 1000, TODAY))
    return check_ce3(conn, "DSP-T")


@pytest.mark.parametrize(
    "dispute_id, qualified, recent",
    [("DSP-A", True, 0), ("DSP-B", False, 0), ("DSP-C", True, 0), ("DSP-D", False, 0), ("DSP-E", True, 2)],
)
def test_seeded_cases(conn, dispute_id, qualified, recent):
    seed(conn, TODAY)
    seed(conn, TODAY)  # idempotent
    assert check_ce3(conn, dispute_id).qualified is qualified
    assert load_dispute(conn, dispute_id).recent_dispute_count == recent


def test_seeded_b_reason_explains_recency(conn):
    seed(conn, TODAY)
    first = check_ce3(conn, "DSP-B").reasons[0]
    assert "45 and 90 days old" in first and "120" in first


def test_unknown_dispute_raises(conn):
    with pytest.raises(KeyError):
        load_dispute(conn, "NOPE")


@pytest.mark.parametrize(
    "ages, qualified",
    [((120, 365), True), ((119, 200), False), ((200, 366), False), ((119, 366), False)],
)
def test_date_window_boundaries(conn, ages, qualified):
    result = scenario(conn, [(a, IP_DEVICE, {}) for a in ages])
    assert result.qualified is qualified


@pytest.mark.parametrize(
    "bad_prior, reason_word",
    [
        ((200, IP_DEVICE, {"is_disputed": True}), "disputed"),
        ((200, IP_DEVICE, {"has_fraud_report": True}), "fraud"),
        ((200, IP_DEVICE, {"cred": "C2"}), "Only 1"),
        ((200, IP_DEVICE, {"merchant": "M2"}), "Only 1"),
    ],
    ids=["disputed", "fraud", "other-card", "other-merchant"],
)
def test_ineligible_prior_excluded(conn, bad_prior, reason_word):
    result = scenario(conn, [(150, IP_DEVICE, {}), bad_prior])
    assert not result.qualified
    assert result.matched_txns == ("P0",)
    assert reason_word in result.reasons[0]


def test_shipping_and_account_only_fails(conn):
    weak = {**NO_MATCH, "account_id": "ACCT-1 ", "shipping_address": " 12  main st,  SPRINGFIELD"}
    result = scenario(conn, [(150, weak, {}), (200, weak, {})])
    assert not result.qualified
    assert result.matched_fields["P0"] == ("account_id", "shipping_address")
    assert "IP address or device ID" in result.reasons[0]


def test_single_qualifying_prior_fails(conn):
    result = scenario(conn, [(150, IP_DEVICE, {})])
    assert not result.qualified
    assert "Only 1" in result.reasons[0]


def test_null_elements_never_match(conn):
    insert_transaction(conn, _txn("D", 5, {**IDENTITY, "account_id": None, "shipping_address": ""}))
    nulls = {"account_id": None, "ip_address": IDENTITY["ip_address"], "device_id": None, "shipping_address": ""}
    for i in range(2):
        insert_transaction(conn, _txn(f"P{i}", 150 + i, nulls))
    insert_dispute(conn, Dispute("DSP-T", "T", "D", "M1", "C1", "CU1", 1000, TODAY))
    result = check_ce3(conn, "DSP-T")
    assert not result.qualified
    assert result.matched_fields == {"P0": ("ip_address",), "P1": ("ip_address",)}


def test_success_reason_names_ages_and_elements(conn):
    result = scenario(conn, [(150, IP_DEVICE, {}), (210, IP_DEVICE, {})])
    assert result.qualified
    assert result.reasons[0] == (
        "2 qualifying prior transactions: P0 (150 days, IP address + device ID), P1 (210 days, IP address + device ID)."
    )


def test_no_history_reason(conn):
    result = scenario(conn, [])
    assert not result.qualified
    assert result.reasons[0].startswith("No prior transactions")


def test_non_ce3_reason_code_does_not_qualify(conn):
    insert_transaction(conn, _txn("D", 5, IDENTITY))
    for i, age in enumerate((150, 200)):
        insert_transaction(conn, _txn(f"P{i}", age, IP_DEVICE))
    insert_dispute(conn, Dispute("DSP-T", "T", "D", "M1", "C1", "CU1", 1000, TODAY, reason_code="13.1"))
    result = check_ce3(conn, "DSP-T")
    assert not result.qualified
    assert result.reasons[0] == "CE3.0 applies only to reason code 10.4 (this dispute is 13.1)."


def test_dispute_txn_mismatch_raises(conn):
    insert_transaction(conn, _txn("D", 5, IDENTITY))
    insert_dispute(conn, Dispute("DSP-T", "T", "D", "M1", "C9", "CU1", 1000, TODAY))
    with pytest.raises(ValueError):
        check_ce3(conn, "DSP-T")


def test_weak_reason_counts_strong_matches(conn):
    weak = {**NO_MATCH, "account_id": "acct-1", "shipping_address": "12 Main St, Springfield"}
    result = scenario(conn, [(150, IP_DEVICE, {}), (200, weak, {})])
    assert not result.qualified
    assert result.reasons[0] == "Only 1 of 2 prior transactions matches on IP address or device ID; CE3.0 needs at least 2."
