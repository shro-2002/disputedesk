"""Demo seed data for DisputeDesk cases A-E. Owner: W1. Dates are relative to `today`."""

import sys
from dataclasses import astuple, fields
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from disputedesk.contracts import Dispute, Transaction  # noqa: E402

MERCHANT = "MRCH-ACME"
DISPUTED_TXN_AGE_DAYS = 5
_TXN_COLS = [f.name for f in fields(Transaction)]
_DISPUTE_COLS = [f.name for f in fields(Dispute) if f.name != "recent_dispute_count"]


def insert_transaction(conn, txn: Transaction) -> None:
    values = [v.isoformat() if isinstance(v, date) else v for v in astuple(txn)]
    values = [int(v) if isinstance(v, bool) else v for v in values]
    conn.execute(
        f"INSERT INTO transactions ({', '.join(_TXN_COLS)}) VALUES ({', '.join('?' * len(_TXN_COLS))})",
        values,
    )


def insert_dispute(conn, dispute: Dispute, status: str = "OPEN") -> None:
    values = [getattr(dispute, c) for c in _DISPUTE_COLS]
    values = [v.isoformat() if isinstance(v, date) else v for v in values]
    cols = _DISPUTE_COLS + ["status"]
    conn.execute(
        f"INSERT INTO disputes ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        values + [status],
    )


def _identity(n: int, account: str, street: str) -> dict:
    return {
        "account_id": account,
        "ip_address": f"203.0.113.{n}",
        "device_id": f"dfp_{n:02d}a7c3e91b4f0d",
        "shipping_address": street,
    }


def _case(today, label, amount_cents, prior_ages, prior_identity=None):
    """Disputed txn (today-5) + priors at given ages. prior_identity overrides priors' elements."""
    n = ord(label) - ord("A") + 10
    cust, cred = f"CUST-{label}", f"CRED-{label}"
    ident = _identity(n, f"acct-{label.lower()}-4471", f"{n} Market Street, Springfield, IL 62701")
    base = {"merchant_id": MERCHANT, "credential_id": cred, "customer_id": cust}
    disputed = Transaction(
        txn_id=f"TXN-{label}-D", amount_cents=amount_cents,
        txn_date=today - timedelta(days=DISPUTED_TXN_AGE_DAYS), is_disputed=True, **base, **ident,
    )
    priors = [
        Transaction(
            txn_id=f"TXN-{label}-P{i}", amount_cents=amount_cents // 2,
            txn_date=today - timedelta(days=age), **base, **{**ident, **(prior_identity or {})},
        )
        for i, age in enumerate(prior_ages, 1)
    ]
    dispute = Dispute(
        dispute_id=f"DSP-{label}", case_label=label, txn_id=disputed.txn_id,
        amount_cents=amount_cents, dispute_date=today, **base,
    )
    return [disputed, *priors], dispute


def _rows(today):
    other_acct = {"account_id": "acct-other-0000", "shipping_address": "1 Elsewhere Rd, Austin, TX 78701"}
    cases = [
        _case(today, "A", 18000, [150, 210], other_acct),  # IP + device
        _case(today, "B", 22000, [45, 90], other_acct),  # IP + device, too recent
        _case(today, "C", 240000, [130, 300], {"shipping_address": "9 Other Ave, Reno, NV 89501"}),  # IP+device+account
        _case(today, "D", 9500, []),  # no history
        _case(today, "E", 30000, [180, 240], other_acct),  # IP + device
    ]
    txns = [t for ts, _ in cases for t in ts]
    disputes = [(d, "OPEN") for _, d in cases]
    # Two closed disputes for CUST-E on a different card: not CE3 priors for DSP-E.
    for i, age in enumerate((20, 45), 1):
        txn = Transaction(
            txn_id=f"TXN-E-H{i}", merchant_id=MERCHANT, credential_id="CRED-E-ALT", customer_id="CUST-E",
            amount_cents=7500, txn_date=today - timedelta(days=age + 10), account_id=None,
            ip_address=f"198.51.100.{i}", device_id=f"dfp_alt{i}9e2", shipping_address=None, is_disputed=True,
        )
        txns.append(txn)
        disputes.append((Dispute(
            dispute_id=f"DSP-E-H{i}", case_label="E", txn_id=txn.txn_id, merchant_id=MERCHANT,
            credential_id=txn.credential_id, customer_id="CUST-E", amount_cents=txn.amount_cents,
            dispute_date=today - timedelta(days=age),
        ), "CLOSED"))
    return txns, disputes


def seed(conn, today: date) -> None:
    txns, disputes = _rows(today)
    with conn:
        conn.executemany("DELETE FROM disputes WHERE dispute_id = ?", [(d.dispute_id,) for d, _ in disputes])
        conn.executemany("DELETE FROM transactions WHERE txn_id = ?", [(t.txn_id,) for t in txns])
        for t in txns:
            insert_transaction(conn, t)
        for d, status in disputes:
            insert_dispute(conn, d, status)


if __name__ == "__main__":
    from disputedesk.db import connect

    seed(connect(), date.today())
    print("Seeded demo cases A-E.")
