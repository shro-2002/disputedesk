"""SAFR-style disposition policy. Owner: W2.

SAFR is the MAS industry reference framework (July 2026): a reference model we follow,
not regulation. decide() is pure and deterministic; no I/O except load_config().
"""

from pathlib import Path

import yaml

from disputedesk.contracts import CE3Result, Dispute, Disposition, Outcome

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
CENTS_PER_DOLLAR = 100
NOT_QUALIFIED_FALLBACK = "The dispute does not meet CE3.0 compelling-evidence conditions."
ACCEPT_LOSS = " Recommend accepting the loss."


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError(f"config must be a mapping, got {type(config).__name__}")
    limit = config.get("auto_limit_usd")
    if not _is_number(limit) or limit < 0:
        raise ValueError(f"config.auto_limit_usd must be a non-negative number, got {limit!r}")
    for key in ("risk_recent_dispute_count", "risk_window_days"):
        value = config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"config.{key} must be a positive integer, got {value!r}")
    lead = config.get("finops_lead")
    if lead is not None and (not isinstance(lead, str) or not lead.strip()):
        raise ValueError(f"config.finops_lead must be a non-empty string, got {lead!r}")
    return config


def load_config(path: str | Path | None = None) -> dict:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        data = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as err:
        raise ValueError(f"config file {config_path} is not valid YAML: {err}") from err
    return validate_config(data)


def format_usd(cents: int) -> str:
    return f"${cents // CENTS_PER_DOLLAR:,}.{cents % CENTS_PER_DOLLAR:02d}"


def _risk_flag(count: int, window_days: int) -> str:
    noun = "dispute" if count == 1 else "disputes"
    return f"customer has {count} other {noun} in the last {window_days} days"


def decide(ce3: CE3Result, dispute: Dispute, config: dict | None = None) -> Disposition:
    if dispute.dispute_id != ce3.dispute_id:
        raise ValueError(
            f"dispute_id mismatch: dispute {dispute.dispute_id!r} vs CE3 result {ce3.dispute_id!r}"
        )
    cfg = validate_config(config) if config is not None else load_config()

    if not ce3.qualified:
        why = ce3.reasons[0] if ce3.reasons else NOT_QUALIFIED_FALLBACK
        return Disposition(dispute.dispute_id, Outcome.REJECT, why + ACCEPT_LOSS)

    amount = format_usd(dispute.amount_cents)
    limit_cents = round(cfg["auto_limit_usd"] * CENTS_PER_DOLLAR)
    limit = format_usd(limit_cents)
    is_risky = dispute.recent_dispute_count >= cfg["risk_recent_dispute_count"]
    flags = (_risk_flag(dispute.recent_dispute_count, cfg["risk_window_days"]),) if is_risky else ()

    if dispute.amount_cents > limit_cents:
        reason = f"CE3.0 qualified, but {amount} exceeds the {limit} auto-execute limit; FinOps lead approval required."
        return Disposition(dispute.dispute_id, Outcome.HUMAN_REVIEW, reason, flags, requires_approval=True)
    if is_risky:
        reason = f"CE3.0 qualified and {amount} is within the {limit} auto-execute limit; proceeding but flagged: {flags[0]}."
        return Disposition(dispute.dispute_id, Outcome.PROCEED_FLAGGED, reason, flags)
    reason = f"CE3.0 qualified and {amount} is within the {limit} auto-execute limit; auto-executing representment."
    return Disposition(dispute.dispute_id, Outcome.AUTO_EXECUTE, reason)
