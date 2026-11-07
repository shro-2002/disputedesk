"""@submitter (disputedesk-gateway): the only agent that calls the payment gateway. Owner: lead.

- Holds the exclusive Switch role `submitter`, re-assumed on every (re)connect (the lease is per connection).
- Submits only disputes the audit log has approved (policy auto-approval or the FinOps lead), polling the DB:
  no chat message can make it move money.
- Handles approvals: sender must be FINOPS_LEAD_SENDER_ID, message must address @submitter and name the case ID.
  Switch's owner_only addressing refuses everyone else first; this is the second, independent check.
"""

import logging
import re
import threading

import httpx

from agents import text
from agents.common import AGENTS, anchor, audit_key, db, make_client, post, run_forever
from disputedesk import orchestrator as orch
from disputedesk.counsel import classify_intent
from disputedesk.settings import require

log = logging.getLogger("agents.submitter")
POLL_SECONDS = 2.0
SUBMITTER_MENTION = re.compile(r"@submitter\b", re.I)


class Submitter:
    def __init__(self) -> None:
        self.client = make_client("GATEWAY", on_connect=self._take_role)
        self.lead_id = require("FINOPS_LEAD_SENDER_ID")
        self.gateway = httpx.Client(base_url=require("GATEWAY_URL"), timeout=httpx.Timeout(15.0, connect=5.0))
        self.lock = threading.Lock()

    def _take_role(self) -> None:
        try:
            self.client.call_op("assume_role", {"role": "submitter"})
            log.info("holding exclusive role 'submitter'")
        except Exception as err:
            log.warning("assume_role failed (%s)", str(err)[:160])

    def _gateway_post(self, path: str, headers: dict, body: dict) -> tuple[int, dict]:
        resp = self.gateway.post(path, headers=headers, json=body)
        return resp.status_code, resp.json()

    def handle(self, event: dict) -> None:
        payload = event.get("payload", {})
        if event.get("type") != "message":
            return
        body = payload.get("body", "")
        is_agent = payload.get("sender_kind") == "agent" or payload.get("sender_name") in AGENTS.values()
        if is_agent or not (payload.get("addressed") or SUBMITTER_MENTION.search(body)):
            return
        with self.lock:
            conn = db()
            result = orch.handle_approval(
                conn, text=body, sender_id=payload.get("sender", ""), sender_name=payload.get("sender_name", ""),
                addressed_submitter=bool(SUBMITTER_MENTION.search(body)), intent=classify_intent(body),
                finops_lead_id=self.lead_id, key=audit_key())
            post(self.client, f"{result.message} {anchor(conn)}")

    def submit_ready(self) -> None:
        with self.lock:
            conn = db()
            for dispute_id in orch.ready_to_submit(conn):
                rebuttal = orch.get_rebuttal(conn, dispute_id)
                if rebuttal is None and orch.latest_approval(conn, dispute_id).event.value != "ACCEPT_LOSS_APPROVED":
                    continue  # wait for @counsel's draft before filing a representment
                try:
                    sub = orch.submit(conn, dispute_id, post=self._gateway_post, rebuttal=rebuttal or "", key=audit_key())
                    post(self.client, text.receipt(dispute_id, sub.receipt, anchor(conn)))
                except PermissionError as err:
                    log.warning("%s", err)
                    post(self.client, f"**{dispute_id}** · ⛔ {err} {anchor(conn)}")


def main(stop: threading.Event) -> None:
    agent = Submitter()

    def poll() -> None:
        while not stop.wait(POLL_SECONDS):
            try:
                agent.submit_ready()
            except Exception:
                log.exception("submit loop failed")

    threading.Thread(target=poll, daemon=True).start()
    run_forever(agent.client, agent.handle, stop)
