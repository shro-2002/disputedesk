"""Room post formatting (agents/text.py)."""

from agents import text
from disputedesk.contracts import Disposition, Outcome


def test_rejected_case_label_says_model_was_skipped_on_purpose():
    post = text.counsel_post("DSP-B", "Recommend: accept the loss.", "No representment.", False, "next",
                             rejected=True)
    assert "Policy decision — rejected cases skip the model; no rebuttal needed" in post
    assert "no LLM key" not in post


def test_risk_flag_not_repeated_when_reason_already_states_it():
    flag = "customer has 2 other disputes in the last 60 days"
    disposition = Disposition("DSP-E", Outcome.PROCEED_FLAGGED, f"proceeding but flagged: {flag}.", (flag,))
    assert text.disposition_line(disposition, "`audit #1`").count(flag) == 1


def test_risk_flag_shown_when_reason_omits_it():
    flag = "customer has 2 other disputes in the last 60 days"
    disposition = Disposition("DSP-C", Outcome.HUMAN_REVIEW, "over the limit.", (flag,))
    assert text.disposition_line(disposition, "`audit #1`").count(flag) == 1
