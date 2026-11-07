"""End-to-end pipeline: seeded cases A–E through CE3, policy, audit and the real gateway app."""

import shutil
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from db.seed import seed
from disputedesk import audit
from disputedesk import orchestrator as orch
from disputedesk.contracts import AuditEvent, Outcome
from disputedesk.db import connect
from disputedesk.policy import load_config
from gateway.app import create_app

KEY = "k" * 64
LEAD = "@switch-user-lead:localhost"
RIYA = "@switch-user-riya:localhost"
TODAY = date(2026, 11, 7)


@pytest.fixture
def env(tmp_path):
    conn = connect(tmp_path / "dd.db")
    seed(conn, TODAY)
    config = load_config()
    app = create_app(
        audit_lookup=lambda i: audit.get(conn, i),
        chain_ok=lambda: audit.verify_chain(conn, key=KEY)[0],
        receipts_db=str(tmp_path / "receipts.db"),
        list_for=lambda d: audit.list_for(conn, d),
        finops_lead=LEAD,
        auto_limit_cents=int(config["auto_limit_usd"] * 100),
    )
    client = TestClient(app)

    def post(path, headers, body):
        resp = client.post(path, headers=headers, json=body)
        return resp.status_code, resp.json()

    return conn, config, post, tmp_path


def _approve(conn, text, sender, intent="approve"):
    return orch.handle_approval(conn, text=text, sender_id=sender, sender_name="x", addressed_submitter=True,
                                intent=intent, finops_lead_id=LEAD, key=KEY)


@pytest.mark.parametrize("dispute_id, outcome, status", [
    ("DSP-A", Outcome.AUTO_EXECUTE, orch.STATUS_APPROVED),
    ("DSP-B", Outcome.REJECT, orch.STATUS_RECOMMEND_ACCEPT),
    ("DSP-C", Outcome.HUMAN_REVIEW, orch.STATUS_AWAITING),
    ("DSP-D", Outcome.REJECT, orch.STATUS_RECOMMEND_ACCEPT),
    ("DSP-E", Outcome.PROCEED_FLAGGED, orch.STATUS_APPROVED),
])
def test_cases_reach_expected_disposition(env, dispute_id, outcome, status):
    conn, config, _, _ = env
    result = orch.process_dispute(conn, dispute_id, config=config, key=KEY)
    assert result.disposition.outcome == outcome
    assert result.status == status
    assert (result.approval_ref is not None) == (status == orch.STATUS_APPROVED)


def test_b_rejection_explains_120_day_window(env):
    conn, config, _, _ = env
    reason = orch.process_dispute(conn, "DSP-B", config=config, key=KEY).disposition.reason
    assert "120" in reason and "accepting the loss" in reason.lower()


def test_e_carries_risk_flag(env):
    conn, config, _, _ = env
    assert orch.process_dispute(conn, "DSP-E", config=config, key=KEY).disposition.risk_flags


def test_auto_cases_submit_and_get_receipts(env):
    conn, config, post, _ = env
    for dispute_id in ("DSP-A", "DSP-E"):
        orch.process_dispute(conn, dispute_id, config=config, key=KEY)
    assert orch.ready_to_submit(conn) == ["DSP-A", "DSP-E"]
    for dispute_id in orch.ready_to_submit(conn):
        sub = orch.submit(conn, dispute_id, post=post, rebuttal="draft", key=KEY)
        assert sub.receipt["status"] == "RECEIVED"
        assert orch.get_status(conn, dispute_id) == orch.STATUS_SUBMITTED
    assert audit.verify_chain(conn, key=KEY) == (True, None)


def test_c_waits_riya_refused_lead_approves(env):
    conn, config, post, _ = env
    orch.process_dispute(conn, "DSP-C", config=config, key=KEY)
    with pytest.raises(PermissionError):
        orch.submit(conn, "DSP-C", post=post, key=KEY)  # no approval yet: never calls gateway

    denied = _approve(conn, "@submitter approve DSP-C", RIYA)
    assert not denied.accepted and "only the FinOps lead" in denied.message
    assert orch.get_status(conn, "DSP-C") == orch.STATUS_AWAITING
    assert orch.ready_to_submit(conn) == []

    approved = _approve(conn, "@submitter approve DSP-C", LEAD)
    assert approved.accepted and approved.status == orch.STATUS_APPROVED
    sub = orch.submit(conn, "DSP-C", post=post, rebuttal="draft", key=KEY)
    assert sub.receipt["action"] == "REPRESENTMENT"
    events = [r.event for r in audit.list_for(conn, "DSP-C")]
    assert AuditEvent.APPROVAL_DENIED in events and events[-1] == AuditEvent.SUBMITTED


def test_lead_rejection_moves_to_manual_review(env):
    conn, config, post, _ = env
    orch.process_dispute(conn, "DSP-C", config=config, key=KEY)
    result = _approve(conn, "@submitter reject DSP-C", LEAD, intent="reject")
    assert result.status == orch.STATUS_MANUAL_REVIEW
    assert orch.get_status(conn, "DSP-C") == orch.STATUS_MANUAL_REVIEW
    with pytest.raises(PermissionError):
        orch.submit(conn, "DSP-C", post=post, key=KEY)


@pytest.mark.parametrize("text, addressed", [("approve DSP-C", False), ("@submitter approve it", True)])
def test_approval_needs_submitter_and_case_id(env, text, addressed):
    conn, config, _, _ = env
    orch.process_dispute(conn, "DSP-C", config=config, key=KEY)
    result = orch.handle_approval(conn, text=text, sender_id=LEAD, sender_name="lead",
                                  addressed_submitter=addressed, intent="approve", finops_lead_id=LEAD, key=KEY)
    assert not result.accepted
    assert orch.get_status(conn, "DSP-C") == orch.STATUS_AWAITING


def test_unclear_intent_asks_and_changes_nothing(env):
    conn, config, _, _ = env
    orch.process_dispute(conn, "DSP-C", config=config, key=KEY)
    result = _approve(conn, "@submitter hmm DSP-C?", LEAD, intent="unclear")
    assert not result.accepted and orch.get_status(conn, "DSP-C") == orch.STATUS_AWAITING


def test_d_accept_loss_after_lead_confirms(env):
    conn, config, post, _ = env
    orch.process_dispute(conn, "DSP-D", config=config, key=KEY)
    assert _approve(conn, "@submitter approve DSP-D", LEAD).accepted
    sub = orch.submit(conn, "DSP-D", post=post, key=KEY)
    assert sub.receipt["action"] == "ACCEPT_LOSS"
    assert orch.get_status(conn, "DSP-D") == orch.STATUS_ACCEPTED_LOSS


def test_reprocessing_is_refused(env):
    conn, config, _, _ = env
    orch.process_dispute(conn, "DSP-A", config=config, key=KEY)
    with pytest.raises(ValueError):
        orch.process_dispute(conn, "DSP-A", config=config, key=KEY)


def test_tampered_copy_fails_chain(env):
    conn, config, post, tmp_path = env
    orch.process_dispute(conn, "DSP-C", config=config, key=KEY)
    conn.commit()
    copy_path = tmp_path / "tampered.db"
    shutil.copy(tmp_path / "dd.db", copy_path)
    tampered = sqlite3.connect(copy_path)
    tampered.execute("UPDATE audit_log SET payload = replace(payload, 'HUMAN_REVIEW', 'AUTO_EXECUTE')")
    tampered.commit()
    ok, bad = audit.verify_chain(tampered, key=KEY)
    assert not ok and bad is not None
    assert audit.verify_chain(conn, key=KEY) == (True, None)
