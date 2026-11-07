"""Verify the HMAC audit chain, optionally proving tamper detection on a COPY of the database.

    make audit              # verify the live chain
    make tamper-demo        # copy the DB, flip DSP-C's disposition in the copy, show verification fail
"""

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from disputedesk import audit
from disputedesk.db import DEFAULT_DB, connect
from disputedesk.settings import load_env, require


def report(conn: sqlite3.Connection, label: str, key: str) -> bool:
    ok, bad = audit.verify_chain(conn, key=key)
    head_id, digest = audit.head(conn)
    if ok:
        print(f"{label}: chain INTACT ({head_id} records, head {digest[:12]})")
    else:
        print(f"{label}: chain BROKEN at record #{bad} — tampering detected")
    return ok


def main() -> int:
    load_env()
    key = require("AUDIT_HMAC_KEY")
    live_ok = report(connect(), "live audit log", key)
    if "--tamper-copy" not in sys.argv:
        return 0 if live_ok else 1
    copy = Path(tempfile.mkdtemp()) / "tampered.db"
    shutil.copy(DEFAULT_DB, copy)
    tampered = sqlite3.connect(copy)
    changed = tampered.execute(
        "UPDATE audit_log SET payload = replace(payload, 'HUMAN_REVIEW', 'AUTO_EXECUTE') "
        "WHERE dispute_id = 'DSP-C' AND event = 'DISPOSITION'").rowcount
    tampered.commit()
    print(f"tampered copy: rewrote DSP-C's disposition HUMAN_REVIEW -> AUTO_EXECUTE ({changed} row) in {copy.name}")
    copy_ok = report(tampered, "tampered copy", key)
    return 0 if live_ok and not copy_ok else 1


if __name__ == "__main__":
    sys.exit(main())
