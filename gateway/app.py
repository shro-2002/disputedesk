"""Mock payment processor. Proves an agent cannot move money without a recorded approval.

Every money call must carry `X-Approval-Ref` = an audit record id that exists, belongs to the
path dispute, has an authorising event from an authorised source, has not been revoked, and has
never been used before. Each dispute can be settled (representment or accept) exactly once.
Dependencies are injected so this module does not import disputedesk.audit.

Input errors (malformed dispute id, oversized rebuttal) are 422 and checked before approval.
Authorisation failures are 403, replays/double settlement 409, audit backend failures 503.
"""

import logging
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Body, FastAPI, Header
from fastapi.responses import JSONResponse

from disputedesk.contracts import (
    ACCEPT_APPROVAL_EVENTS,
    REPRESENTMENT_APPROVAL_EVENTS,
    AuditEvent,
    AuditHistory,
    AuditLookup,
    ChainCheck,
)

log = logging.getLogger("disputedesk.gateway")

DISPUTE_ID_RE = re.compile(r"[A-Z0-9-]{1,40}")
APPROVAL_REF_RE = re.compile(r"[1-9][0-9]{0,17}")  # positive, fits SQLite INTEGER
MAX_REBUTTAL_CHARS = 20_000
AUTO_OUTCOMES = frozenset({"AUTO_EXECUTE", "PROCEED_FLAGGED"})

ACTION_REPRESENTMENT = "REPRESENTMENT"
ACTION_ACCEPT = "ACCEPT_LOSS"
ALLOWED_EVENTS = {
    ACTION_REPRESENTMENT: REPRESENTMENT_APPROVAL_EVENTS,
    ACTION_ACCEPT: ACCEPT_APPROVAL_EVENTS,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id   TEXT PRIMARY KEY,
    dispute_id   TEXT NOT NULL UNIQUE,
    action       TEXT NOT NULL,
    approval_ref INTEGER NOT NULL UNIQUE,
    submitted_at TEXT NOT NULL,
    status       TEXT NOT NULL,
    rebuttal     TEXT
)
"""
COLUMNS = ("receipt_id", "dispute_id", "action", "approval_ref", "submitted_at", "status", "rebuttal")


class Refusal(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _event_of(record) -> Optional[AuditEvent]:
    try:
        return AuditEvent(record.event)
    except ValueError:
        return None


def _audit_call(fn, *args):
    """Call an injected audit dependency; any failure becomes 503, never a stack trace."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - backend failure of any kind must refuse money
        log.warning("gateway audit dependency failed: %s", type(exc).__name__)
        raise Refusal(503, "AUDIT_UNAVAILABLE", "Audit log unavailable; submission refused") from exc


def _check_auto_payload(record, ref: int, auto_limit_cents: int) -> None:
    payload = record.payload if isinstance(record.payload, dict) else {}
    amount = payload.get("amount_cents")
    if (
        not isinstance(amount, int)
        or isinstance(amount, bool)
        or amount < 0
        or not isinstance(payload.get("outcome"), str)
        or payload.get("outcome") not in AUTO_OUTCOMES
    ):
        raise Refusal(403, "APPROVAL_INVALID", f"Audit record {ref} has a malformed auto-approval payload")
    if amount > auto_limit_cents:
        raise Refusal(403, "APPROVAL_OVER_LIMIT", f"Audit record {ref} exceeds the auto-approval limit")


def _validate_rebuttal(body: Optional[dict]) -> Optional[str]:
    rebuttal = (body or {}).get("rebuttal")
    if rebuttal is None:
        return None
    if not isinstance(rebuttal, str) or len(rebuttal) > MAX_REBUTTAL_CHARS:
        raise Refusal(422, "REBUTTAL_INVALID", f"rebuttal must be text of at most {MAX_REBUTTAL_CHARS} characters")
    return rebuttal


def _validate_dispute_id(dispute_id: str) -> None:
    if not DISPUTE_ID_RE.fullmatch(dispute_id):
        raise Refusal(422, "DISPUTE_ID_INVALID", "dispute_id must match [A-Z0-9-]{1,40}")


def create_app(
    audit_lookup: AuditLookup,
    chain_ok: ChainCheck,
    receipts_db: str,
    *,
    list_for: AuditHistory,
    finops_lead: str,
    auto_limit_cents: int,
):
    if not isinstance(finops_lead, str) or not finops_lead or finops_lead != finops_lead.strip():
        raise ValueError("finops_lead must be a non-empty string without surrounding whitespace")
    if not isinstance(auto_limit_cents, int) or isinstance(auto_limit_cents, bool) or auto_limit_cents < 0:
        raise ValueError("auto_limit_cents must be a non-negative integer")
    conn = sqlite3.connect(receipts_db, check_same_thread=False)
    conn.execute(SCHEMA)
    conn.commit()
    # ponytail: one global lock around the single connection; fine for a demo gateway.
    lock = threading.Lock()
    app = FastAPI(title="DisputeDesk mock gateway")

    def check_approval(dispute_id: str, action: str, raw_ref: Optional[str]) -> int:
        if raw_ref is None or not raw_ref.strip():
            raise Refusal(403, "APPROVAL_REQUIRED", "X-Approval-Ref header is required")
        raw_ref = raw_ref.strip()
        if not APPROVAL_REF_RE.fullmatch(raw_ref):
            raise Refusal(403, "APPROVAL_INVALID", "X-Approval-Ref must be a positive integer")
        ref = int(raw_ref)
        if not _audit_call(chain_ok):
            raise Refusal(403, "AUDIT_CHAIN_BROKEN", "Audit log failed verification; all submissions refused")
        record = _audit_call(audit_lookup, ref)
        if record is None:
            raise Refusal(403, "APPROVAL_NOT_FOUND", f"No audit record {ref}")
        if record.dispute_id != dispute_id:
            raise Refusal(403, "APPROVAL_WRONG_DISPUTE", f"Audit record {ref} is not for dispute {dispute_id}")
        event = _event_of(record)
        if event not in ALLOWED_EVENTS[action]:
            raise Refusal(403, "APPROVAL_WRONG_TYPE", f"Audit record {ref} does not authorise {action}")
        if event is AuditEvent.HUMAN_APPROVED and record.actor != finops_lead:
            raise Refusal(403, "APPROVAL_NOT_AUTHORISED", f"Audit record {ref} was not approved by the FinOps lead")
        if event is AuditEvent.AUTO_APPROVED:
            _check_auto_payload(record, ref, auto_limit_cents)
        # Any later HUMAN_REJECTED revokes; APPROVAL_DENIED (a failed attempt) does not.
        history = _audit_call(list_for, dispute_id)
        if any(
            _event_of(r) is AuditEvent.HUMAN_REJECTED and r.record_id is not None and r.record_id > ref
            for r in history
        ):
            raise Refusal(403, "APPROVAL_REVOKED", f"Approval {ref} was revoked by a later rejection")
        return ref

    def store(receipt: dict) -> None:
        with lock, conn:
            used = conn.execute(
                "SELECT approval_ref = ?, dispute_id = ? FROM receipts WHERE approval_ref = ? OR dispute_id = ?",
                (receipt["approval_ref"], receipt["dispute_id"], receipt["approval_ref"], receipt["dispute_id"]),
            ).fetchall()
            if any(same_ref for same_ref, _ in used):
                raise Refusal(409, "APPROVAL_ALREADY_USED", f"Approval {receipt['approval_ref']} has already been used")
            if used:
                raise Refusal(409, "DISPUTE_ALREADY_SETTLED", f"Dispute {receipt['dispute_id']} is already settled")
            try:
                conn.execute(
                    f"INSERT INTO receipts ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                    tuple(receipt[c] for c in COLUMNS),
                )
            except sqlite3.IntegrityError as exc:  # another process won the race
                raise Refusal(409, "DISPUTE_ALREADY_SETTLED", "Dispute or approval already used") from exc

    def submit(dispute_id: str, action: str, raw_ref: Optional[str], body: Optional[dict]) -> JSONResponse:
        try:
            _validate_dispute_id(dispute_id)
            rebuttal = _validate_rebuttal(body)
            ref = check_approval(dispute_id, action, raw_ref)
            receipt = {
                "receipt_id": f"RCPT-{uuid.uuid4().hex[:12].upper()}",
                "dispute_id": dispute_id,
                "action": action,
                "approval_ref": ref,
                "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "status": "RECEIVED",
                "rebuttal": rebuttal,
            }
            store(receipt)
        except Refusal as r:
            log.info("gateway refused action=%s code=%s", action, r.code)
            return _error(r.status, r.code, r.message)
        log.info("gateway accepted dispute=%s action=%s ref=%s receipt=%s", dispute_id, action, ref, receipt["receipt_id"])
        return JSONResponse(status_code=201, content=receipt)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/disputes/{dispute_id}/representment")
    def representment(
        dispute_id: str,
        x_approval_ref: Optional[str] = Header(default=None),
        body: Optional[dict] = Body(default=None),
    ):
        return submit(dispute_id, ACTION_REPRESENTMENT, x_approval_ref, body)

    @app.post("/disputes/{dispute_id}/accept")
    def accept(
        dispute_id: str,
        x_approval_ref: Optional[str] = Header(default=None),
        body: Optional[dict] = Body(default=None),
    ):
        return submit(dispute_id, ACTION_ACCEPT, x_approval_ref, body)

    @app.get("/disputes/{dispute_id}/receipts")
    def receipts(dispute_id: str):
        try:
            _validate_dispute_id(dispute_id)
        except Refusal as r:
            return _error(r.status, r.code, r.message)
        with lock:
            rows = conn.execute(
                f"SELECT {', '.join(COLUMNS)} FROM receipts WHERE dispute_id = ? ORDER BY rowid",
                (dispute_id,),
            ).fetchall()
        return {"receipts": [dict(zip(COLUMNS, row)) for row in rows]}

    return app
