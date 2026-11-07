"""Deterministic dispute pipeline. Owner: lead.

dispute -> check_ce3 -> decide -> audit -> (auto-approve | wait for FinOps lead | recommend accept)
-> submit through the gateway with an audit approval ref.

Rules (from verifier V2): LLM output never enters check_ce3/decide/audit.append or the approval ref.
Only this module writes approval events, and only from deterministic inputs.
"""

import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from disputedesk import audit
from disputedesk.ce3 import check_ce3, load_dispute
from disputedesk.contracts import (
    ACCEPT_APPROVAL_EVENTS,
    REPRESENTMENT_APPROVAL_EVENTS,
    AuditEvent,
    AuditRecord,
    CE3Result,
    Dispute,
    Disposition,
    Outcome,
)
from disputedesk.policy import decide

log = logging.getLogger(__name__)

DISPUTE_ID_RE = re.compile(r"\bDSP-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
AUTO_OUTCOMES = (Outcome.AUTO_EXECUTE, Outcome.PROCEED_FLAGGED)

STATUS_OPEN = "OPEN"
STATUS_APPROVED = "APPROVED"
STATUS_AWAITING = "AWAITING_APPROVAL"
STATUS_RECOMMEND_ACCEPT = "RECOMMEND_ACCEPT"
STATUS_MANUAL_REVIEW = "MANUAL_REVIEW"
STATUS_SUBMITTED = "SUBMITTED"
STATUS_ACCEPTED_LOSS = "ACCEPTED_LOSS"

Intent = Literal["approve", "reject", "unclear"]
SYSTEM_ACTOR = "disputedesk-policy"


@dataclass(frozen=True)
class CaseResult:
    dispute: Dispute
    ce3: CE3Result
    disposition: Disposition
    status: str
    approval_ref: Optional[int]


@dataclass(frozen=True)
class ApprovalResult:
    accepted: bool
    status: str  # new dispute status, or unchanged
    message: str  # plain English for the room
    record_id: Optional[int] = None


@dataclass(frozen=True)
class Submission:
    receipt: dict
    record_id: int


def _set_status(conn: sqlite3.Connection, dispute_id: str, status: str) -> None:
    conn.execute("UPDATE disputes SET status = ? WHERE dispute_id = ?", (status, dispute_id))
    conn.commit()


def get_status(conn: sqlite3.Connection, dispute_id: str) -> str:
    row = conn.execute("SELECT status FROM disputes WHERE dispute_id = ?", (dispute_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown dispute {dispute_id}")
    return row[0]


def _append(conn, dispute_id: str, event: AuditEvent, actor: str, payload: dict, key) -> AuditRecord:
    record = audit.append(conn, AuditRecord(dispute_id=dispute_id, event=event, actor=actor, payload=payload), key=key)
    conn.commit()
    return record


def process_dispute(conn: sqlite3.Connection, dispute_id: str, *, config: dict, key=None) -> CaseResult:
    """Run CE3 + policy for an OPEN dispute and record every decision in the audit chain."""
    if get_status(conn, dispute_id) != STATUS_OPEN:
        raise ValueError(f"{dispute_id} was already processed (status {get_status(conn, dispute_id)})")
    dispute = load_dispute(conn, dispute_id)
    ce3 = check_ce3(conn, dispute_id)
    _append(conn, dispute_id, AuditEvent.CE3_CHECKED, "disputedesk-evidence", {
        "qualified": ce3.qualified, "matched_txns": list(ce3.matched_txns), "reasons": list(ce3.reasons)}, key)
    disposition = decide(ce3, dispute, config)
    _append(conn, dispute_id, AuditEvent.DISPOSITION, SYSTEM_ACTOR, {
        "outcome": disposition.outcome.value, "reason": disposition.reason,
        "risk_flags": list(disposition.risk_flags), "amount_cents": dispute.amount_cents}, key)

    approval_ref = None
    if disposition.outcome in AUTO_OUTCOMES:
        record = _append(conn, dispute_id, AuditEvent.AUTO_APPROVED, SYSTEM_ACTOR, {
            "amount_cents": dispute.amount_cents, "outcome": disposition.outcome.value}, key)
        approval_ref, status = record.record_id, STATUS_APPROVED
    elif disposition.outcome == Outcome.HUMAN_REVIEW:
        status = STATUS_AWAITING
    else:
        status = STATUS_RECOMMEND_ACCEPT
    _set_status(conn, dispute_id, status)
    return CaseResult(dispute, ce3, disposition, status, approval_ref)


def handle_approval(conn: sqlite3.Connection, *, text: str, sender_id: str, sender_name: str,
                    addressed_submitter: bool, intent: Intent, finops_lead_id: str, key=None) -> ApprovalResult:
    """Apply a human approval/rejection. Three deterministic checks gate it; `intent` (possibly
    LLM-classified) only chooses approve vs reject AFTER all three pass."""
    match = DISPUTE_ID_RE.search(text or "")
    if not addressed_submitter or match is None:
        return ApprovalResult(False, "", "Approvals must address @submitter and name the case ID (e.g. DSP-C).")
    dispute_id = match.group(0)
    status = get_status(conn, dispute_id)
    if sender_id != finops_lead_id:
        record = _append(conn, dispute_id, AuditEvent.APPROVAL_DENIED, sender_name or sender_id,
                         {"sender_id": sender_id, "attempted_intent": intent}, key)
        return ApprovalResult(False, status, f"I can't act on that: only the FinOps lead can approve money "
                              f"movements for {dispute_id}. Attempt logged (audit #{record.record_id}).",
                              record.record_id)
    if status == STATUS_RECOMMEND_ACCEPT and intent == "approve":
        record = _append(conn, dispute_id, AuditEvent.ACCEPT_LOSS_APPROVED, finops_lead_id, {"via": "room"}, key)
        _set_status(conn, dispute_id, STATUS_APPROVED)
        return ApprovalResult(True, STATUS_APPROVED, f"Accepting the loss on {dispute_id} as recommended.",
                              record.record_id)
    if status != STATUS_AWAITING:
        return ApprovalResult(False, status, f"{dispute_id} is not awaiting approval (status {status}).")
    if intent == "approve":
        record = _append(conn, dispute_id, AuditEvent.HUMAN_APPROVED, finops_lead_id, {"via": "room"}, key)
        _set_status(conn, dispute_id, STATUS_APPROVED)
        return ApprovalResult(True, STATUS_APPROVED, f"Approved by the FinOps lead. Submitting {dispute_id}.",
                              record.record_id)
    if intent == "reject":
        record = _append(conn, dispute_id, AuditEvent.HUMAN_REJECTED, finops_lead_id, {"via": "room"}, key)
        _set_status(conn, dispute_id, STATUS_MANUAL_REVIEW)
        return ApprovalResult(True, STATUS_MANUAL_REVIEW, f"Rejected. {dispute_id} moved to MANUAL_REVIEW.",
                              record.record_id)
    return ApprovalResult(False, status, f"Do you want me to approve or reject {dispute_id}? Reply "
                          f"`@submitter approve {dispute_id}` or `@submitter reject {dispute_id}`.")


def latest_approval(conn: sqlite3.Connection, dispute_id: str) -> Optional[AuditRecord]:
    """Newest record authorising a gateway call, read from the audit log (never from chat)."""
    authorising = REPRESENTMENT_APPROVAL_EVENTS | ACCEPT_APPROVAL_EVENTS
    records = [r for r in audit.list_for(conn, dispute_id) if r.event in authorising]
    return records[-1] if records else None


def submit(conn: sqlite3.Connection, dispute_id: str, *, post: Callable[[str, dict, dict], tuple[int, dict]],
           rebuttal: str = "", key=None) -> Submission:
    """Call the gateway with the audit approval ref. `post(path, headers, json) -> (status, body)`."""
    approval = latest_approval(conn, dispute_id)
    if approval is None:
        raise PermissionError(f"No approval record for {dispute_id}; refusing to call the gateway.")
    is_accept = approval.event in ACCEPT_APPROVAL_EVENTS
    path = f"/disputes/{dispute_id}/{'accept' if is_accept else 'representment'}"
    body = {} if is_accept else {"rebuttal": rebuttal}
    status_code, response = post(path, {"X-Approval-Ref": str(approval.record_id)}, body)
    if status_code != 201:
        code = response.get("error", {}).get("code", "UNKNOWN") if isinstance(response, dict) else "UNKNOWN"
        raise PermissionError(f"Gateway refused {dispute_id}: HTTP {status_code} {code}")
    record = _append(conn, dispute_id, AuditEvent.SUBMITTED, "disputedesk-gateway", {
        "receipt_id": response["receipt_id"], "approval_ref": approval.record_id, "action": response["action"]}, key)
    _set_status(conn, dispute_id, STATUS_ACCEPTED_LOSS if is_accept else STATUS_SUBMITTED)
    return Submission(response, record.record_id)


def save_rebuttal(conn: sqlite3.Connection, dispute_id: str, text: str, by_llm: bool) -> None:
    """Counsel's draft is prose only; it never feeds back into qualification or approval."""
    conn.execute("CREATE TABLE IF NOT EXISTS rebuttals (dispute_id TEXT PRIMARY KEY, text TEXT NOT NULL, "
                 "by_llm INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("INSERT OR REPLACE INTO rebuttals (dispute_id, text, by_llm) VALUES (?, ?, ?)",
                 (dispute_id, text, int(by_llm)))
    conn.commit()


def get_rebuttal(conn: sqlite3.Connection, dispute_id: str) -> Optional[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS rebuttals (dispute_id TEXT PRIMARY KEY, text TEXT NOT NULL, "
                 "by_llm INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    row = conn.execute("SELECT text FROM rebuttals WHERE dispute_id = ?", (dispute_id,)).fetchone()
    return row[0] if row else None


def ready_to_submit(conn: sqlite3.Connection) -> list[str]:
    """Approved disputes not yet submitted (the submitter polls this; no chat instruction needed)."""
    return [r[0] for r in conn.execute(
        "SELECT dispute_id FROM disputes WHERE status = ? ORDER BY dispute_id", (STATUS_APPROVED,))]
