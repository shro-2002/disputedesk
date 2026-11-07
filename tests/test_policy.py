from dataclasses import asdict, replace
from datetime import date

import pytest

from disputedesk.contracts import CE3Result, Dispute, Outcome
from disputedesk.policy import decide, format_usd, load_config

CONFIG = {"auto_limit_usd": 500, "risk_recent_dispute_count": 2, "risk_window_days": 60, "finops_lead": "lead"}


def make(amount_cents=10_000, recent=0, qualified=True, reasons=("ok",)):
    dispute = Dispute("DSP-X", "X", "T1", "M1", "C1", "CU1", amount_cents, date(2026, 11, 1), recent_dispute_count=recent)
    ce3 = CE3Result("DSP-X", qualified, ("T0",) if qualified else (), reasons=reasons)
    return ce3, dispute


@pytest.mark.parametrize(
    "amount,recent,expected",
    [
        (10_000, 0, Outcome.AUTO_EXECUTE),
        (50_000, 0, Outcome.AUTO_EXECUTE),
        (50_001, 0, Outcome.HUMAN_REVIEW),
        (240_000, 0, Outcome.HUMAN_REVIEW),
        (10_000, 1, Outcome.AUTO_EXECUTE),
        (10_000, 2, Outcome.PROCEED_FLAGGED),
        (50_000, 3, Outcome.PROCEED_FLAGGED),
        (240_000, 2, Outcome.HUMAN_REVIEW),
    ],
)
def test_outcomes_and_boundaries(amount, recent, expected):
    d = decide(*make(amount, recent), CONFIG)
    assert d.outcome is expected
    assert d.requires_approval is (expected is Outcome.HUMAN_REVIEW)
    assert bool(d.risk_flags) is (recent >= 2)


def test_reject_reason_carries_ce3_reason_and_accept_loss():
    d = decide(*make(qualified=False, reasons=("Prior transactions are under 120 days old.", "x")), CONFIG)
    assert d.outcome is Outcome.REJECT and not d.requires_approval
    assert d.reason == "Prior transactions are under 120 days old. Recommend accepting the loss."


def test_reject_with_no_reasons_uses_fallback():
    d = decide(*make(qualified=False, reasons=()), CONFIG)
    assert d.reason.endswith("Recommend accepting the loss.") and len(d.reason) > 40


def test_human_review_keeps_risk_flag_and_formats_amount():
    d = decide(*make(240_000, 2), CONFIG)
    assert d.risk_flags == ("customer has 2 other disputes in the last 60 days",)
    assert "$2,400.00" in d.reason and "$500.00" in d.reason


def test_mismatched_dispute_ids_raise():
    ce3, dispute = make()
    with pytest.raises(ValueError, match="mismatch"):
        decide(replace(ce3, dispute_id="DSP-Y"), dispute, CONFIG)


@pytest.mark.parametrize(
    "overrides,amount,recent,expected",
    [
        ({"auto_limit_usd": 100}, 10_001, 0, Outcome.HUMAN_REVIEW),
        ({"auto_limit_usd": 5000}, 240_000, 0, Outcome.AUTO_EXECUTE),
        ({"risk_recent_dispute_count": 1}, 10_000, 1, Outcome.PROCEED_FLAGGED),
        ({"risk_recent_dispute_count": 5}, 10_000, 4, Outcome.AUTO_EXECUTE),
    ],
)
def test_config_thresholds_change_outcome(overrides, amount, recent, expected):
    assert decide(*make(amount, recent), {**CONFIG, **overrides}).outcome is expected


@pytest.mark.parametrize(
    "bad",
    [{"auto_limit_usd": "500"}, {"auto_limit_usd": -1}, {"risk_recent_dispute_count": 0}, {"risk_window_days": True}],
)
def test_bad_config_raises(bad):
    with pytest.raises(ValueError):
        decide(*make(), {**CONFIG, **bad})


def test_load_config_reads_repo_file_and_rejects_bad_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # must not depend on CWD
    cfg = load_config()
    assert cfg["auto_limit_usd"] == 500 and cfg["risk_recent_dispute_count"] == 2
    bad = tmp_path / "bad.yaml"
    bad.write_text("auto_limit_usd: lots\nrisk_recent_dispute_count: 2\nrisk_window_days: 60\n")
    with pytest.raises(ValueError, match="auto_limit_usd"):
        load_config(bad)


def test_default_config_used_when_none():
    assert decide(*make(50_001), None).outcome is Outcome.HUMAN_REVIEW


@pytest.mark.parametrize("cents,text", [(0, "$0.00"), (5, "$0.05"), (50_000, "$500.00"), (240_000, "$2,400.00")])
def test_format_usd(cents, text):
    assert format_usd(cents) == text


@pytest.mark.parametrize(
    "args", [(10_000, 0, True), (240_000, 2, True), (10_000, 2, True), (10_000, 0, False)]
)
def test_no_string_says_mandate(args):
    amount, recent, qualified = args
    d = decide(*make(amount, recent, qualified), CONFIG)
    assert "mandate" not in str(asdict(d)).lower()
    import disputedesk.policy as policy_module
    from pathlib import Path
    assert "mandate" not in Path(policy_module.__file__).read_text().lower()
