"""Wire the mock gateway to the real audit log. Owner: lead.

Run: `make gateway`. The gateway is its own process and reads the audit DB itself, so an agent
cannot hand it a fake approval: it only trusts HMAC-verified audit rows.
"""

import os
import threading

from disputedesk import audit
from disputedesk.db import ROOT, connect
from disputedesk.policy import load_config
from disputedesk.settings import load_env, require
from gateway.app import create_app

load_env()
_key = require("AUDIT_HMAC_KEY")
_finops_lead = require("FINOPS_LEAD_SENDER_ID")
_config = load_config()
_conn = connect()
_lock = threading.Lock()  # one shared SQLite connection across gateway worker threads


def _locked(fn):
    def wrapper(*args):
        with _lock:
            return fn(*args)
    return wrapper


receipts_path = os.environ.get("GATEWAY_RECEIPTS_DB") or "data/receipts.db"
if not os.path.isabs(receipts_path):
    receipts_path = str(ROOT / receipts_path)  # persistent file: single-use refs survive restarts

app = create_app(
    audit_lookup=_locked(lambda record_id: audit.get(_conn, record_id)),
    chain_ok=_locked(lambda: audit.verify_chain(_conn, key=_key)[0]),
    receipts_db=receipts_path,
    list_for=_locked(lambda dispute_id: audit.list_for(_conn, dispute_id)),
    finops_lead=_finops_lead,
    auto_limit_cents=int(_config["auto_limit_usd"] * 100),
)
