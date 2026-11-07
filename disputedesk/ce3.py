"""Visa CE3.0 (reason code 10.4) eligibility check. Owner: W1. Pure SQL + Python, no LLM."""

import re
from datetime import date

from disputedesk.contracts import (
    CE3_ELEMENTS,
    CE3_MAX_AGE_DAYS,
    CE3_MIN_AGE_DAYS,
    CE3_MIN_MATCHING_ELEMENTS,
    CE3_MIN_PRIOR_TXNS,
    CE3_STRONG_ELEMENTS,
    REASON_CODE_CE3,
    CE3Result,
    Dispute,
)

RECENT_DISPUTE_WINDOW_DAYS = 60
ELEMENT_LABELS = {
    "account_id": "account ID",
    "ip_address": "IP address",
    "device_id": "device ID",
    "shipping_address": "shipping address",
}
WINDOW_TEXT = f"{CE3_MIN_AGE_DAYS}–{CE3_MAX_AGE_DAYS} days before the dispute"
NEED_TEXT = f"CE3.0 needs at least {CE3_MIN_PRIOR_TXNS} that are {WINDOW_TEXT}."

_DISPUTE_COLS = (
    "dispute_id, case_label, txn_id, merchant_id, credential_id, customer_id,"
    " amount_cents, dispute_date, reason_code"
)
_ELEMENT_COLS = ", ".join(CE3_ELEMENTS)
# Every same-merchant + same-card transaction other than the disputed one, on or before the dispute.
_CANDIDATES_SQL = f"""
    SELECT txn_id, {_ELEMENT_COLS},
           CAST(julianday(:dispute_date) - julianday(txn_date) AS INTEGER) AS age_days
    FROM transactions
    WHERE merchant_id = :merchant_id AND credential_id = :credential_id
      AND txn_id != :txn_id AND txn_date <= :dispute_date
    ORDER BY txn_date DESC
"""
# Same as above plus the CE3.0 date window and clean-history filters.
_ELIGIBLE_SQL = f"""
    SELECT txn_id
    FROM transactions
    WHERE merchant_id = :merchant_id AND credential_id = :credential_id
      AND txn_id != :txn_id
      AND txn_date BETWEEN date(:dispute_date, '-{CE3_MAX_AGE_DAYS} days')
                       AND date(:dispute_date, '-{CE3_MIN_AGE_DAYS} days')
      AND is_disputed = 0 AND has_fraud_report = 0
"""


def load_dispute(conn, dispute_id: str) -> Dispute:
    row = conn.execute(
        f"SELECT {_DISPUTE_COLS} FROM disputes WHERE dispute_id = ?", (dispute_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown dispute: {dispute_id}")
    recent = conn.execute(
        f"""SELECT COUNT(*) FROM disputes
            WHERE customer_id = ? AND dispute_id != ?
              AND dispute_date BETWEEN date(?, '-{RECENT_DISPUTE_WINDOW_DAYS} days') AND ?""",
        (row["customer_id"], dispute_id, row["dispute_date"], row["dispute_date"]),
    ).fetchone()[0]
    fields = dict(row)
    fields["dispute_date"] = date.fromisoformat(fields["dispute_date"])
    return Dispute(**fields, recent_dispute_count=recent)


def _normalize(element: str, value):
    if value is None:
        return None
    text = value.strip().lower()
    if element == "shipping_address":
        text = re.sub(r"\s+", " ", text)
    return text or None


def _matched_elements(disputed, prior) -> tuple[str, ...]:
    return tuple(
        e
        for e in CE3_ELEMENTS
        if _normalize(e, disputed[e]) is not None
        and _normalize(e, disputed[e]) == _normalize(e, prior[e])
    )


def _passes_elements(matched: tuple[str, ...]) -> bool:
    has_strong = any(e in CE3_STRONG_ELEMENTS for e in matched)
    return len(matched) >= CE3_MIN_MATCHING_ELEMENTS and has_strong


def _join(items) -> str:
    items = [str(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _ages_text(ages) -> str:
    ages = sorted(ages)
    if len(ages) == 1:
        return f"Prior transaction is {ages[0]} days old"
    return f"Prior transactions are {_join(ages)} days old"


def check_ce3(conn, dispute_id: str) -> CE3Result:
    dispute = load_dispute(conn, dispute_id)
    if dispute.reason_code != REASON_CODE_CE3:
        return CE3Result(
            dispute_id=dispute_id,
            qualified=False,
            matched_txns=(),
            reasons=(
                f"CE3.0 applies only to reason code {REASON_CODE_CE3}"
                f" (this dispute is {dispute.reason_code}).",
            ),
        )
    disputed = conn.execute(
        f"SELECT merchant_id, credential_id, {_ELEMENT_COLS} FROM transactions WHERE txn_id = ?",
        (dispute.txn_id,),
    ).fetchone()
    if disputed is None:
        raise KeyError(f"Disputed transaction not found: {dispute.txn_id}")
    if (disputed["merchant_id"], disputed["credential_id"]) != (dispute.merchant_id, dispute.credential_id):
        raise ValueError(
            f"Dispute {dispute_id} says merchant/card {dispute.merchant_id}/{dispute.credential_id}"
            f" but transaction {dispute.txn_id} is {disputed['merchant_id']}/{disputed['credential_id']}"
        )
    params = {
        "merchant_id": disputed["merchant_id"],
        "credential_id": disputed["credential_id"],
        "txn_id": dispute.txn_id,
        "dispute_date": dispute.dispute_date.isoformat(),
    }
    candidates = conn.execute(_CANDIDATES_SQL, params).fetchall()
    eligible_ids = {r["txn_id"] for r in conn.execute(_ELIGIBLE_SQL, params)}

    matched_fields = {c["txn_id"]: _matched_elements(disputed, c) for c in candidates}
    ages = {c["txn_id"]: c["age_days"] for c in candidates}
    matched_txns = tuple(
        t for t in matched_fields if t in eligible_ids and _passes_elements(matched_fields[t])
    )
    qualified = len(matched_txns) >= CE3_MIN_PRIOR_TXNS
    reasons = _reasons(qualified, matched_txns, eligible_ids, matched_fields, ages)
    return CE3Result(
        dispute_id=dispute_id,
        qualified=qualified,
        matched_txns=matched_txns,
        matched_fields=matched_fields,
        reasons=reasons,
    )


def _reasons(qualified, matched_txns, eligible_ids, matched_fields, ages) -> tuple[str, ...]:
    if qualified:
        details = ", ".join(
            f"{t} ({ages[t]} days, {' + '.join(ELEMENT_LABELS[e] for e in matched_fields[t])})"
            for t in sorted(matched_txns, key=ages.get)
        )
        return (f"{len(matched_txns)} qualifying prior transactions: {details}.",)
    if not ages:
        return (f"No prior transactions with this merchant on this card; {NEED_TEXT}",)
    in_window = [t for t, a in ages.items() if CE3_MIN_AGE_DAYS <= a <= CE3_MAX_AGE_DAYS]
    # Recency is decisive only when enough history exists but falls outside the window.
    if len(in_window) < CE3_MIN_PRIOR_TXNS <= len(ages):
        return (f"{_ages_text(ages.values())}; {NEED_TEXT}",)
    excluded = [t for t in in_window if t not in eligible_ids]
    if excluded and len(in_window) - len(excluded) < CE3_MIN_PRIOR_TXNS:
        return (
            f"{len(excluded)} prior transaction(s) in the {CE3_MIN_AGE_DAYS}–{CE3_MAX_AGE_DAYS} day"
            f" window were excluded because they were disputed or fraud-reported; {NEED_TEXT}",
        )
    strong = [t for t in eligible_ids if any(e in CE3_STRONG_ELEMENTS for e in matched_fields[t])]
    if len(eligible_ids) >= CE3_MIN_PRIOR_TXNS > len(strong):
        verb = "matches" if len(strong) == 1 else "match"
        return (
            f"Only {len(strong)} of {len(eligible_ids)} prior transactions {verb} on IP address"
            f" or device ID; CE3.0 needs at least {CE3_MIN_PRIOR_TXNS}.",
        )
    return (
        f"Only {len(matched_txns)} qualifying prior transaction(s) match on at least"
        f" {CE3_MIN_MATCHING_ELEMENTS} identity elements including IP address or device ID;"
        f" CE3.0 needs at least {CE3_MIN_PRIOR_TXNS}.",
    )
