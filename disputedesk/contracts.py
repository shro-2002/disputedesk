"""Shared contract for every DisputeDesk module. Owner: lead (N0). Workers read, never edit.

Money is integer cents everywhere. Dates are datetime.date. All records are frozen.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Callable, Optional, Protocol

REASON_CODE_CE3 = "10.4"

# The four CE3.0 identity elements, plus the subset where at least one must match.
CE3_ELEMENTS = ("account_id", "ip_address", "device_id", "shipping_address")
CE3_STRONG_ELEMENTS = ("ip_address", "device_id")
CE3_MIN_PRIOR_TXNS = 2
CE3_MIN_MATCHING_ELEMENTS = 2
CE3_MIN_AGE_DAYS = 120  # inclusive
CE3_MAX_AGE_DAYS = 365  # inclusive


@dataclass(frozen=True)
class Transaction:
    txn_id: str
    merchant_id: str
    credential_id: str  # tokenised payment credential (card)
    customer_id: str
    amount_cents: int
    txn_date: date
    account_id: Optional[str]
    ip_address: Optional[str]
    device_id: Optional[str]
    shipping_address: Optional[str]
    is_disputed: bool = False
    has_fraud_report: bool = False


@dataclass(frozen=True)
class Dispute:
    dispute_id: str  # e.g. "DSP-A"
    case_label: str  # "A".."E" for seeded demo cases
    txn_id: str  # the disputed transaction
    merchant_id: str
    credential_id: str
    customer_id: str
    amount_cents: int
    dispute_date: date
    reason_code: str = REASON_CODE_CE3
    # Number of OTHER disputes by the same customer within the last 60 days.
    # Populated by ce3.load_dispute so policy.decide stays pure.
    recent_dispute_count: int = 0


@dataclass(frozen=True)
class CE3Result:
    dispute_id: str
    qualified: bool
    matched_txns: tuple[str, ...]  # txn_ids that satisfied every CE3.0 condition
    # txn_id -> matched element names, for every candidate prior txn evaluated
    matched_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Plain-English reasons. On failure, the first reason explains why (e.g. 120-day window).
    reasons: tuple[str, ...] = ()


class Outcome(str, Enum):
    AUTO_EXECUTE = "AUTO_EXECUTE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    REJECT = "REJECT"
    PROCEED_FLAGGED = "PROCEED_FLAGGED"


@dataclass(frozen=True)
class Disposition:
    dispute_id: str
    outcome: Outcome
    reason: str  # plain-English, shown in the room
    risk_flags: tuple[str, ...] = ()
    requires_approval: bool = False  # True only for HUMAN_REVIEW


class AuditEvent(str, Enum):
    CE3_CHECKED = "CE3_CHECKED"
    DISPOSITION = "DISPOSITION"
    AUTO_APPROVED = "AUTO_APPROVED"  # policy approved representment (AUTO_EXECUTE / PROCEED_FLAGGED)
    HUMAN_APPROVED = "HUMAN_APPROVED"  # FinOps lead approved representment
    HUMAN_REJECTED = "HUMAN_REJECTED"  # FinOps lead rejected -> MANUAL_REVIEW
    APPROVAL_DENIED = "APPROVAL_DENIED"  # unauthorised sender tried to approve
    ACCEPT_LOSS_APPROVED = "ACCEPT_LOSS_APPROVED"  # REJECT outcome: accept the loss
    SUBMITTED = "SUBMITTED"  # gateway receipt recorded


# Which audit events authorise which gateway call.
REPRESENTMENT_APPROVAL_EVENTS = frozenset({AuditEvent.AUTO_APPROVED, AuditEvent.HUMAN_APPROVED})
ACCEPT_APPROVAL_EVENTS = frozenset({AuditEvent.ACCEPT_LOSS_APPROVED})


@dataclass(frozen=True)
class AuditRecord:
    dispute_id: str
    event: AuditEvent
    actor: str  # agent name or Mattermost username
    payload: dict = field(default_factory=dict)  # JSON-serialisable only
    created_at: str = ""  # ISO-8601 UTC; audit.append fills it if empty
    record_id: Optional[int] = None  # assigned by audit.append
    prev_hash: str = ""  # assigned by audit.append
    hash: str = ""  # assigned by audit.append


# ---------------------------------------------------------------------------
# Signatures each worker implements (documentation; the real code lives in the owner's file).
#
# W1 disputedesk/ce3.py
#   def load_dispute(conn, dispute_id: str) -> Dispute          # raises KeyError if unknown
#   def check_ce3(conn, dispute_id: str) -> CE3Result           # pure SQL + Python, no LLM
# W1 db/seed.py
#   def seed(conn, today: date) -> None                         # idempotent; dates relative to today
#
# W2 disputedesk/policy.py
#   def load_config(path: str | None = None) -> dict             # None -> repo-root config.yaml
#   def decide(ce3: CE3Result, dispute: Dispute, config: dict | None = None) -> Disposition
# W2 disputedesk/audit.py   (owns its own `audit_log` table, CREATE IF NOT EXISTS)
#   def append(conn, record: AuditRecord) -> AuditRecord        # returns record with id + hashes
#   Hash = HMAC-SHA256(key, prev_hash + canonical_json(record)); key from env AUDIT_HMAC_KEY
#   (or explicit key= kwarg). Only deterministic code + the gateway hold the key; never agents/LLM.
#   def get(conn, record_id: int) -> AuditRecord | None
#   def list_for(conn, dispute_id: str) -> list[AuditRecord]
#   def verify_chain(conn) -> tuple[bool, int | None]           # (ok, first_bad_record_id)
#
# W3 gateway/app.py
#   def create_app(audit_lookup: AuditLookup, chain_ok: ChainCheck, receipts_db: str, *,
#                  list_for: AuditHistory, finops_lead: str, auto_limit_cents: int) -> FastAPI
#   AUTO_APPROVED payload MUST carry {"amount_cents": int, "outcome": "AUTO_EXECUTE"|"PROCEED_FLAGGED"}.
#   HUMAN_APPROVED/HUMAN_REJECTED actor = finops_lead identity (Switch sender id from config).
#
# W4 disputedesk/switch_client.py
#   class SwitchClient: see W4 brief in STATE.md
# ---------------------------------------------------------------------------


class AuditLookup(Protocol):
    def __call__(self, record_id: int) -> Optional[AuditRecord]: ...


ChainCheck = Callable[[], bool]
AuditHistory = Callable[[str], list[AuditRecord]]  # dispute_id -> records ordered by record_id
