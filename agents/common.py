"""Shared agent plumbing: settings, Switch client per agent, audit-anchored posting. Owner: lead."""

import logging
import os
import sqlite3
import threading

from disputedesk import audit
from disputedesk.db import connect
from disputedesk.settings import load_env, require
from disputedesk.switch_client import SwitchClient

load_env()
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")

logging.getLogger("httpx").setLevel(logging.WARNING)  # heartbeats every 2 s are noise

AGENTS = {"EVIDENCE": "disputedesk-evidence", "COUNSEL": "disputedesk-counsel", "GATEWAY": "disputedesk-gateway"}


def room_id() -> str:
    return require("SWITCH_ROOM_ID")


def audit_key() -> str:
    return require("AUDIT_HMAC_KEY")


def make_client(prefix: str, **kwargs) -> SwitchClient:
    return SwitchClient(require("SWITCH_BASE_URL"), require(f"{prefix}_AGENT_ID"), require(f"{prefix}_API_KEY"),
                        rooms=[room_id()], event_filter="all", agent_name=AGENTS[prefix], **kwargs)


def db() -> sqlite3.Connection:
    return connect()


def anchor(conn: sqlite3.Connection) -> str:
    """Audit head posted into the room after every write: the shared room log is the external anchor
    that makes tail truncation of the hash chain detectable."""
    record_id, digest = audit.head(conn)
    return f"`audit #{record_id} · {digest[:12]}`" if record_id else "`audit empty`"


def post(client: SwitchClient, text: str, *, thread_id: str | None = None) -> None:
    try:
        client.post_message(room_id(), text, thread_id=thread_id)
    except Exception:  # posting must never kill an agent loop; the audit log is the source of truth
        logging.getLogger("agents.post").exception("post failed")


def run_forever(client: SwitchClient, handle, stop: threading.Event) -> None:
    log = logging.getLogger(f"agents.{client.agent_name}")
    for event in client.events(stop=stop):
        try:
            handle(event)
        except Exception:
            log.exception("handler failed for event %s", event.get("type"))
