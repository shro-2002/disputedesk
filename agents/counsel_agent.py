"""@counsel: drafts the rebuttal (the only LLM-written content) and posts the recommendation. Owner: lead."""

import logging
import threading
import time

from agents import text
from agents.common import AGENTS, db, make_client, post, run_forever
from disputedesk import counsel
from disputedesk import orchestrator as orch
from disputedesk.ce3 import check_ce3, load_dispute
from disputedesk.contracts import Disposition, Outcome
from disputedesk.settings import require

log = logging.getLogger("agents.counsel")


def _disposition(conn, dispute_id: str) -> Disposition:
    """Read the decided disposition from the audit log (never recompute or let the LLM pick it)."""
    from disputedesk import audit
    from disputedesk.contracts import AuditEvent

    record = next(r for r in reversed(audit.list_for(conn, dispute_id)) if r.event == AuditEvent.DISPOSITION)
    return Disposition(dispute_id, Outcome(record.payload["outcome"]), record.payload["reason"],
                       tuple(record.payload.get("risk_flags", ())))


class Counsel:
    def __init__(self) -> None:
        self.client = make_client("COUNSEL")
        self.lead = require("FINOPS_LEAD")
        self.done: set[str] = set()

    def handle(self, event: dict) -> None:
        payload = event.get("payload", {})
        if event.get("type") != "message" or not payload.get("addressed"):
            return
        if payload.get("sender_name") != AGENTS["EVIDENCE"]:
            return  # counsel drafts on @evidence's hand-off only
        match = orch.DISPUTE_ID_RE.search(payload.get("body", ""))
        if match is None or match.group(0) in self.done:
            return
        self.draft(match.group(0))

    def draft(self, dispute_id: str) -> None:
        conn = db()
        dispute, ce3 = load_dispute(conn, dispute_id), check_ce3(conn, dispute_id)
        disposition = _disposition(conn, dispute_id)
        if disposition.outcome == Outcome.REJECT:
            body, by_llm = f"No representment: {disposition.reason}", False
        else:
            started = time.monotonic()
            body, by_llm = counsel.draft_rebuttal(dispute, ce3)
            log.info("draft %s source=%s latency=%.1fs", dispute_id, "claude" if by_llm else "template",
                     time.monotonic() - started)
        orch.save_rebuttal(conn, dispute_id, body, by_llm)
        self.done.add(dispute_id)
        post(self.client, text.counsel_post(dispute_id, counsel.recommendation(disposition), body, by_llm,
                                            text.next_step(disposition.outcome, dispute_id, self.lead)))


def main(stop: threading.Event) -> None:
    agent = Counsel()
    run_forever(agent.client, agent.handle, stop)
