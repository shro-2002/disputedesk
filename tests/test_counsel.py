"""Counsel: deterministic fallbacks and intent classification (no network)."""

import pytest

from disputedesk import counsel
from disputedesk.contracts import CE3Result, Dispute, Disposition, Outcome

DISPUTE = Dispute("DSP-A", "A", "TXN-A", "MRCH-ACME", "CRED-A", "CUST-A", 18000, __import__("datetime").date(2026, 11, 7))
CE3 = CE3Result("DSP-A", True, ("P1", "P2"), {"P1": ("ip_address", "device_id")}, ("ok",))


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_draft_falls_back_to_template_without_key():
    text, by_llm = counsel.draft_rebuttal(DISPUTE, CE3)
    assert not by_llm and "Compelling Evidence 3.0" in text and "$180.00" in text


@pytest.mark.parametrize("text, intent", [
    ("@submitter approve DSP-C", "approve"),
    ("@submitter go ahead with DSP-C", "approve"),
    ("@submitter reject DSP-C", "reject"),
    ("@submitter don't approve DSP-C", "unclear"),  # mixed signals: never guess approve
    ("@submitter do not submit DSP-C", "reject"),
    ("@submitter what about DSP-C?", "unclear"),
])
def test_classify_intent(text, intent):
    assert counsel.classify_intent(text) == intent


def test_llm_garbage_cannot_produce_approve(monkeypatch):
    monkeypatch.setattr(counsel, "_ask", lambda *a, **k: "Sure! I approve this wholeheartedly.")
    assert counsel.classify_intent("@submitter hmm DSP-C") == "unclear"


@pytest.mark.parametrize("outcome", list(Outcome))
def test_recommendation_covers_every_outcome(outcome):
    assert counsel.recommendation(Disposition("DSP-A", outcome, "r")).startswith("Recommend:")
