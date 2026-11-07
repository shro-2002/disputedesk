"""@counsel's LLM work: rebuttal drafting and approve/reject intent classification. Owner: lead.

The LLM writes prose only. It never sees or changes qualification, disposition or approval
records (V2 rule). Without ANTHROPIC_API_KEY (or on any API error) we fall back to a
deterministic template / keyword classifier so the pipeline still runs.
"""

import logging
import os
import re

from disputedesk.contracts import CE3Result, Dispute, Disposition, Outcome
from disputedesk.policy import format_usd

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"
TIMEOUT_SECONDS = 30.0
DRAFT_MAX_TOKENS = 2000
INTENT_MAX_TOKENS = 256
FALLBACK_BETA = "server-side-fallback-2026-07-01"

DRAFT_SYSTEM = (
    "You are a chargeback analyst writing a Visa reason code 10.4 (card-not-present fraud) "
    "representment rebuttal under Compelling Evidence 3.0. Use ONLY the facts given. Do not invent "
    "transactions, dates, or identifiers. Do not restate or second-guess the qualification decision; "
    "it was made deterministically. Write 4-6 sentences, plain and factual, addressed to the issuer."
)
INTENT_SYSTEM = (
    "Classify the FinOps lead's message about a chargeback case as exactly one word: "
    "approve, reject, or unclear. Answer with the single word only."
)

_APPROVE = re.compile(r"\b(approve[sd]?|go ahead|ship it|submit it|lgtm|yes)\b", re.I)
_REJECT = re.compile(r"\b(reject(ed)?|deny|denied|decline[sd]?|don'?t|do not|no|hold|stop)\b", re.I)


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic  # imported lazily so tests and the deterministic path need no SDK config

    return anthropic.Anthropic(timeout=TIMEOUT_SECONDS, max_retries=2)


def _ask(system: str, prompt: str, max_tokens: int, effort: str) -> str | None:
    """One Claude call; returns text or None on any failure (caller falls back)."""
    client = _client()
    if client is None:
        return None
    import anthropic

    try:
        response = client.beta.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",  # server-side fallback model if the request is declined
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.RateLimitError:
        log.warning("Claude rate-limited; using deterministic fallback")
        return None
    except anthropic.APIStatusError as err:
        log.warning("Claude API error %s; using deterministic fallback", err.status_code)
        return None
    except anthropic.APIConnectionError:
        log.warning("Claude unreachable; using deterministic fallback")
        return None
    if response.stop_reason == "refusal":
        log.warning("Claude declined the request; using deterministic fallback")
        return None
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return text or None


def evidence_facts(dispute: Dispute, ce3: CE3Result) -> str:
    lines = [f"Dispute {dispute.dispute_id}: {format_usd(dispute.amount_cents)}, reason code "
             f"{dispute.reason_code}, merchant {dispute.merchant_id}, disputed txn {dispute.txn_id}."]
    for txn_id in ce3.matched_txns:
        fields = ", ".join(ce3.matched_fields.get(txn_id, ()))
        lines.append(f"Prior undisputed txn {txn_id} matches on: {fields}.")
    lines.extend(ce3.reasons)
    return "\n".join(lines)


def template_rebuttal(dispute: Dispute, ce3: CE3Result) -> str:
    priors = ", ".join(ce3.matched_txns) or "none"
    return (f"The cardholder disputes {dispute.txn_id} ({format_usd(dispute.amount_cents)}) under reason code "
            f"10.4. Under Visa Compelling Evidence 3.0, the merchant identified prior undisputed transactions "
            f"on the same card at the same merchant ({priors}), each 120–365 days before the dispute, sharing "
            f"at least two identity elements with the disputed transaction including IP address or device ID. "
            f"This shows the transaction was made by the legitimate cardholder. We request the chargeback be reversed.")


_SUBMITTER_MENTION = re.compile(r"@submitter\b", re.I)


def scrub_mentions(text: str) -> str:
    """An agent post containing "@submitter" reads as an agent instructing the gateway, which Switch refuses
    in the room. Model output is never trusted to avoid it, so rewrite deterministically."""
    return _SUBMITTER_MENTION.sub("the submitter role", text)


def draft_rebuttal(dispute: Dispute, ce3: CE3Result) -> tuple[str, bool]:
    """Returns (text, written_by_llm)."""
    text = _ask(DRAFT_SYSTEM, evidence_facts(dispute, ce3), DRAFT_MAX_TOKENS, "medium")
    return (scrub_mentions(text), True) if text else (template_rebuttal(dispute, ce3), False)


def recommendation(disposition: Disposition) -> str:
    """Deterministic recommendation line derived from the policy outcome (never from the LLM)."""
    return {
        Outcome.AUTO_EXECUTE: "Recommend: submit representment (within auto limit).",
        Outcome.PROCEED_FLAGGED: "Recommend: submit representment, but review the customer's dispute pattern.",
        Outcome.HUMAN_REVIEW: "Recommend: submit representment once the FinOps lead approves.",
        Outcome.REJECT: "Recommend: accept the loss; CE3.0 evidence is not available.",
    }[disposition.outcome]


def keyword_intent(text: str) -> str:
    has_reject, has_approve = bool(_REJECT.search(text)), bool(_APPROVE.search(text))
    if has_reject and not has_approve:
        return "reject"
    if has_approve and not has_reject:
        return "approve"
    return "unclear"


def classify_intent(text: str) -> str:
    """Keywords first (deterministic); the LLM only breaks ties. Output limited to 3 labels."""
    intent = keyword_intent(text)
    if intent != "unclear":
        return intent
    answer = (_ask(INTENT_SYSTEM, text, INTENT_MAX_TOKENS, "low") or "").strip().lower()
    return answer if answer in ("approve", "reject") else "unclear"
