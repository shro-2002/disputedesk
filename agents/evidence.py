"""@evidence: receives the processor webhook, runs CE3.0 + policy deterministically, posts the evidence
table, then hands the case to @counsel with a targeted message. Owner: lead."""

import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agents import text
from agents.common import anchor, audit_key, db, make_client, post, run_forever
from disputedesk import orchestrator as orch
from disputedesk.ce3 import load_dispute
from disputedesk.policy import load_config

log = logging.getLogger("agents.evidence")
WEBHOOK_PORT = 8098
DISPUTE_ID_RE = re.compile(r"^DSP-[A-Z0-9]+$")
RULEBOOK = """Visa Compelling Evidence 3.0 (reason code 10.4, card-not-present fraud). A dispute qualifies only if:
1. at least two prior transactions exist from the same merchant and the same payment credential;
2. each prior transaction is at least 120 and at most 365 days before the dispute date;
3. each prior transaction is not disputed and has no fraud report;
4. at least two of {customer account/login ID, IP address, device ID/fingerprint, shipping address} match between
   each prior transaction and the disputed one, and at least one matching element is IP address or device ID.
DisputeDesk decides this in SQL/Python only. The LLM never decides qualification."""


class Evidence:
    def __init__(self) -> None:
        self.client = make_client("EVIDENCE", on_connect=self._on_connect)
        self.lock = threading.Lock()  # one dispute at a time keeps the audit chain linear

    def _on_connect(self) -> None:
        try:
            self.client.call_op("create_room_document", {
                "name": "CE3.0 rulebook", "description": "The Visa CE3.0 rule DisputeDesk enforces",
                "instructions": "Consult before discussing whether a 10.4 dispute qualifies. Do not re-decide it.",
                "content": RULEBOOK})
            log.info("room document 'CE3.0 rulebook' created")
        except Exception as err:  # already exists on reconnect: documented as a failed request
            log.info("rulebook document not created (%s)", str(err)[:120])

    def process(self, dispute_id: str) -> None:
        with self.lock:
            conn = db()
            post(self.client, text.intake(load_dispute(conn, dispute_id)))
            result = orch.process_dispute(conn, dispute_id, config=load_config(), key=audit_key())
            post(self.client, text.evidence_table(conn, result.dispute, result.ce3))
            post(self.client, text.disposition_line(result.disposition, anchor(conn)))
            self.client.call_op("send_targeted_message", {
                "body": f"{dispute_id}: evidence and disposition are posted. Please draft the rebuttal and recommendation.",
                "target_names": ["disputedesk-counsel"]})

    def handle(self, event: dict) -> None:
        return None  # @evidence acts on webhooks only; room chatter is context


MAX_WEBHOOK_BYTES = 4096


def webhook_server(agent: Evidence) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            if self.path != "/webhook/disputes":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
                if not 0 < length <= MAX_WEBHOOK_BYTES:  # read(-1) would read to EOF, unbounded
                    raise ValueError(f"Content-Length must be 1..{MAX_WEBHOOK_BYTES}")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
                dispute_id = str(body.get("dispute_id", ""))
                if not DISPUTE_ID_RE.fullmatch(dispute_id):
                    raise ValueError("dispute_id must look like DSP-A")
            except (ValueError, json.JSONDecodeError) as err:
                self.send_error(400, str(err))
                return
            threading.Thread(target=self._run, args=(dispute_id,), daemon=True).start()
            self.send_response(202)
            self.end_headers()

        def _run(self, dispute_id: str) -> None:
            try:
                agent.process(dispute_id)
            except Exception:
                log.exception("processing %s failed", dispute_id)
                post(agent.client, f"**{dispute_id}** · ⚠ processing failed; see agent logs.")

        def log_message(self, fmt: str, *args) -> None:
            log.info("webhook " + fmt, *args)

    return ThreadingHTTPServer(("127.0.0.1", WEBHOOK_PORT), Handler)


def main(stop: threading.Event) -> None:
    agent = Evidence()
    server = webhook_server(agent)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("webhook listening on 127.0.0.1:%s", WEBHOOK_PORT)
    run_forever(agent.client, agent.handle, stop)
    server.shutdown()
