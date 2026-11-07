import pytest
from fastapi.testclient import TestClient

from disputedesk.contracts import AuditEvent, AuditRecord
from gateway.app import create_app

E = AuditEvent
LEAD = "finops-lead"
LIMIT = 10_000


def rec(rid, dispute, event, actor="policy", payload=None):
    return AuditRecord(dispute, event, actor, payload=payload or {}, record_id=rid)


def auto(rid, dispute="DSP-A", amount=5_000, outcome="AUTO_EXECUTE"):
    return rec(rid, dispute, E.AUTO_APPROVED, payload={"amount_cents": amount, "outcome": outcome})


RECORDS = {
    r.record_id: r
    for r in [
        auto(1),
        rec(2, "DSP-A", E.HUMAN_APPROVED, LEAD),
        rec(3, "DSP-A", E.ACCEPT_LOSS_APPROVED),
        rec(5, "DSP-A", E.APPROVAL_DENIED, "riya"),
        rec(6, "DSP-A", E.CE3_CHECKED, "ce3"),
        auto(7, dispute="DSP-B"),
        rec(8, "DSP-A", E.HUMAN_APPROVED, "riya"),
        auto(9, amount=LIMIT + 1),
        rec(10, "DSP-A", E.AUTO_APPROVED, payload={}),
        auto(11, outcome="HUMAN_REVIEW"),
        auto(12, amount="5000"),
        auto(13, amount=True),
        auto(14, amount=LIMIT, outcome="PROCEED_FLAGGED"),
        auto(15, amount=-1),
        # DSP-R: approval, then rejection (revokes 20), then a fresh approval (22 stays valid)
        rec(20, "DSP-R", E.HUMAN_APPROVED, LEAD),
        rec(21, "DSP-R", E.HUMAN_REJECTED, LEAD),
        rec(22, "DSP-R", E.HUMAN_APPROVED, LEAD),
        # DSP-D: lead approves, then riya's failed attempt; approval must survive
        rec(30, "DSP-D", E.HUMAN_APPROVED, LEAD),
        rec(31, "DSP-D", E.APPROVAL_DENIED, "riya"),
    ]
}


def list_for(dispute_id):
    return [r for _, r in sorted(RECORDS.items()) if r.dispute_id == dispute_id]


def boom(*_):
    raise RuntimeError("db down")


@pytest.fixture
def deps():
    return {"lookup": RECORDS.get, "chain": lambda: True, "list_for": list_for}


@pytest.fixture
def client(deps):
    app = create_app(
        lambda ref: deps["lookup"](ref),
        lambda: deps["chain"](),
        ":memory:",
        list_for=lambda d: deps["list_for"](d),
        finops_lead=LEAD,
        auto_limit_cents=LIMIT,
    )
    return TestClient(app)


def post(client, action, ref, dispute="DSP-A", body=None):
    headers = {} if ref is None else {"X-Approval-Ref": ref}
    return client.post(f"/disputes/{dispute}/{action}", headers=headers, json=body)


def receipts(client, dispute="DSP-A"):
    return client.get(f"/disputes/{dispute}/receipts").json()["receipts"]


def assert_error(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "action,ref,dispute,code",
    [
        ("representment", None, "DSP-A", "APPROVAL_REQUIRED"),
        ("representment", "   ", "DSP-A", "APPROVAL_REQUIRED"),
        ("accept", None, "DSP-A", "APPROVAL_REQUIRED"),
        ("representment", "abc", "DSP-A", "APPROVAL_INVALID"),
        ("representment", "0", "DSP-A", "APPROVAL_INVALID"),
        ("representment", "-1", "DSP-A", "APPROVAL_INVALID"),
        ("representment", "1.5", "DSP-A", "APPROVAL_INVALID"),
        ("representment", "99", "DSP-A", "APPROVAL_NOT_FOUND"),
        ("representment", "7", "DSP-A", "APPROVAL_WRONG_DISPUTE"),
        ("accept", "7", "DSP-A", "APPROVAL_WRONG_DISPUTE"),
        ("representment", "3", "DSP-A", "APPROVAL_WRONG_TYPE"),  # accept approval cannot submit
        ("representment", "21", "DSP-R", "APPROVAL_WRONG_TYPE"),  # HUMAN_REJECTED
        ("representment", "5", "DSP-A", "APPROVAL_WRONG_TYPE"),  # APPROVAL_DENIED
        ("representment", "6", "DSP-A", "APPROVAL_WRONG_TYPE"),  # CE3_CHECKED
        ("accept", "1", "DSP-A", "APPROVAL_WRONG_TYPE"),  # AUTO_APPROVED cannot accept loss
        ("accept", "2", "DSP-A", "APPROVAL_WRONG_TYPE"),  # HUMAN_APPROVED cannot accept loss
        ("accept", "21", "DSP-R", "APPROVAL_WRONG_TYPE"),
        ("accept", "5", "DSP-A", "APPROVAL_WRONG_TYPE"),
        ("accept", "6", "DSP-A", "APPROVAL_WRONG_TYPE"),
        ("representment", "8", "DSP-A", "APPROVAL_NOT_AUTHORISED"),  # HUMAN_APPROVED by non-lead
        ("representment", "9", "DSP-A", "APPROVAL_OVER_LIMIT"),
        ("representment", "10", "DSP-A", "APPROVAL_INVALID"),  # empty payload
        ("representment", "11", "DSP-A", "APPROVAL_INVALID"),  # wrong outcome
        ("representment", "12", "DSP-A", "APPROVAL_INVALID"),  # amount as string
        ("representment", "13", "DSP-A", "APPROVAL_INVALID"),  # amount as bool
        ("representment", "15", "DSP-A", "APPROVAL_INVALID"),  # negative amount
        ("representment", "20", "DSP-R", "APPROVAL_REVOKED"),  # later HUMAN_REJECTED
    ],
)
def test_refused_with_403_and_no_receipt(client, action, ref, dispute, code):
    assert_error(post(client, action, ref, dispute), 403, code)
    assert receipts(client, dispute) == []


@pytest.mark.parametrize("action,ref", [("representment", "1"), ("accept", "3")])
def test_broken_chain_refuses_everything(client, deps, action, ref):
    deps["chain"] = lambda: False
    assert_error(post(client, action, ref), 403, "AUDIT_CHAIN_BROKEN")
    assert receipts(client) == []


@pytest.mark.parametrize("failing", ["lookup", "chain", "list_for"])
def test_audit_failure_is_503_and_no_receipt(client, deps, failing):
    deps[failing] = boom
    r = post(client, "representment", "1")
    assert_error(r, 503, "AUDIT_UNAVAILABLE")
    assert "db down" not in r.text
    assert receipts(client) == []


@pytest.mark.parametrize(
    "action,ref,dispute,expected_action",
    [
        ("representment", "1", "DSP-A", "REPRESENTMENT"),  # AUTO_APPROVED under limit
        ("representment", "14", "DSP-A", "REPRESENTMENT"),  # AUTO_APPROVED exactly at limit, flagged
        ("representment", "2", "DSP-A", "REPRESENTMENT"),  # HUMAN_APPROVED by lead
        ("representment", "22", "DSP-R", "REPRESENTMENT"),  # approval after rejection
        ("representment", "30", "DSP-D", "REPRESENTMENT"),  # APPROVAL_DENIED does not revoke
        ("accept", "3", "DSP-A", "ACCEPT_LOSS"),
    ],
)
def test_authorised_call_returns_receipt(client, action, ref, dispute, expected_action):
    r = post(client, action, ref, dispute, body={"rebuttal": "CE3.0 match on device and IP"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"receipt_id", "dispute_id", "action", "approval_ref", "submitted_at", "status", "rebuttal"}
    assert body["receipt_id"].startswith("RCPT-")
    assert body["dispute_id"] == dispute
    assert body["action"] == expected_action
    assert body["approval_ref"] == int(ref)
    assert body["status"] == "RECEIVED"
    assert body["rebuttal"] == "CE3.0 match on device and IP"
    assert body["submitted_at"].endswith("+00:00")
    assert receipts(client, dispute) == [body]


def test_body_is_optional(client):
    r = post(client, "representment", "1")
    assert r.status_code == 201
    assert r.json()["rebuttal"] is None


@pytest.mark.parametrize("rebuttal", ["x" * 20_001, 123, ["a"]])
def test_bad_rebuttal_is_422_and_no_receipt(client, rebuttal):
    assert_error(post(client, "representment", "1", body={"rebuttal": rebuttal}), 422, "REBUTTAL_INVALID")
    assert receipts(client) == []


def test_rebuttal_at_limit_is_accepted(client):
    assert post(client, "representment", "1", body={"rebuttal": "x" * 20_000}).status_code == 201


def test_replay_is_409_and_stores_once(client):
    assert post(client, "representment", "1").status_code == 201
    assert_error(post(client, "representment", "1"), 409, "APPROVAL_ALREADY_USED")
    assert len(receipts(client)) == 1


@pytest.mark.parametrize(
    "first,second",
    [
        (("representment", "1"), ("representment", "2")),
        (("representment", "1"), ("accept", "3")),
        (("accept", "3"), ("representment", "2")),
    ],
)
def test_second_money_move_is_409_settled(client, first, second):
    assert post(client, *first).status_code == 201
    assert_error(post(client, *second), 409, "DISPUTE_ALREADY_SETTLED")
    assert len(receipts(client)) == 1


def test_receipts_listing_is_per_dispute(client):
    post(client, "representment", "1")
    post(client, "representment", "7", dispute="DSP-B")
    assert [r["approval_ref"] for r in receipts(client)] == [1]
    assert [r["approval_ref"] for r in receipts(client, "DSP-B")] == [7]
    assert receipts(client, "DSP-Z") == []


@pytest.mark.parametrize("dispute", ["dsp-a", "DSP_A", "A" * 41, "DSP-A%0A"])
def test_bad_dispute_id_is_422(client, dispute):
    assert_error(post(client, "representment", "1", dispute=dispute), 422, "DISPUTE_ID_INVALID")


def test_trailing_newline_ref_is_invalid(client):
    # httpx refuses raw newlines in headers, so exercise the regex directly.
    from gateway.app import APPROVAL_REF_RE, DISPUTE_ID_RE

    assert APPROVAL_REF_RE.fullmatch("1\n") is None
    assert DISPUTE_ID_RE.fullmatch("DSP-A\n") is None


@pytest.mark.parametrize("outcome", [["AUTO_EXECUTE"], {"AUTO_EXECUTE": 1}, None, 5])
def test_non_string_outcome_is_invalid(deps, client, outcome):
    bad = rec(40, "DSP-A", E.AUTO_APPROVED, payload={"amount_cents": 100, "outcome": outcome})
    deps["lookup"] = lambda ref: bad if ref == 40 else RECORDS.get(ref)
    assert_error(post(client, "representment", "40"), 403, "APPROVAL_INVALID")
    assert receipts(client) == []


@pytest.mark.parametrize(
    "lead,limit",
    [("", LIMIT), ("  ", LIMIT), (" lead", LIMIT), (None, LIMIT), (LEAD, -1), (LEAD, True), (LEAD, 1.5), (LEAD, "100")],
)
def test_bad_config_refused_at_startup(lead, limit):
    with pytest.raises(ValueError):
        create_app(RECORDS.get, lambda: True, ":memory:", list_for=list_for, finops_lead=lead, auto_limit_cents=limit)
