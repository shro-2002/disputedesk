"""Room message formatting (pure functions, deterministic). Owner: lead."""

import sqlite3

from disputedesk.contracts import CE3_STRONG_ELEMENTS, CE3Result, Dispute, Disposition, Outcome
from disputedesk.policy import format_usd

LABELS = {"account_id": "account", "ip_address": "IP", "device_id": "device", "shipping_address": "shipping"}
BADGE = {Outcome.AUTO_EXECUTE: "AUTO_EXECUTE", Outcome.HUMAN_REVIEW: "HUMAN_REVIEW",
         Outcome.REJECT: "REJECT", Outcome.PROCEED_FLAGGED: "PROCEED_FLAGGED ⚠"}


def intake(dispute: Dispute) -> str:
    return (f"**{dispute.dispute_id}** · Processor webhook: new chargeback, reason code {dispute.reason_code} "
            f"(card-not-present fraud), {format_usd(dispute.amount_cents)}, merchant {dispute.merchant_id}. "
            "Running the Visa CE3.0 check.")


def evidence_table(conn: sqlite3.Connection, dispute: Dispute, ce3: CE3Result) -> str:
    rows = ["| prior txn | age (days) | matching elements | counts? |", "|---|---|---|---|"]
    for txn_id, fields in sorted(ce3.matched_fields.items()):
        row = conn.execute("SELECT julianday(?) - julianday(txn_date) FROM transactions WHERE txn_id = ?",
                           (dispute.dispute_date.isoformat(), txn_id)).fetchone()
        age = int(row[0]) if row else "?"
        names = ", ".join(LABELS[f] for f in fields) or "none"
        strong = any(f in CE3_STRONG_ELEMENTS for f in fields)
        rows.append(f"| {txn_id} | {age} | {names}{'' if strong or not fields else ' (no IP/device)'} | "
                    f"{'✅' if txn_id in ce3.matched_txns else '❌'} |")
    if len(rows) == 2:
        rows.append("| none | — | — | ❌ |")
    verdict = "QUALIFIES" if ce3.qualified else "DOES NOT QUALIFY"
    return (f"**{dispute.dispute_id}** · CE3.0 evidence (deterministic SQL, no LLM): **{verdict}**\n\n"
            + "\n".join(rows) + f"\n\n{ce3.reasons[0] if ce3.reasons else ''}")


def disposition_line(disposition: Disposition, anchor: str) -> str:
    unstated = [f for f in disposition.risk_flags if f not in disposition.reason]
    flags = f" Risk flag: {'; '.join(unstated)}." if unstated else ""
    return f"**{disposition.dispute_id}** · Disposition **{BADGE[disposition.outcome]}**: {disposition.reason}{flags} {anchor}"


def counsel_post(dispute_id: str, recommendation: str, rebuttal: str, by_llm: bool, next_step: str, *,
                 rejected: bool = False) -> str:
    if rejected:
        note = "Policy decision — rejected cases skip the model; no rebuttal needed."
    else:
        source = "drafted by Claude" if by_llm else "template draft (no LLM key configured)"
        note = (f"Rebuttal {source}; the evidence verdict and disposition above were decided in code, "
                f"not by the model.")
    return f"**{dispute_id}** · {recommendation}\n\n> {rebuttal}\n\n_{note}_ {next_step}"


def next_step(outcome: Outcome, dispute_id: str, lead: str) -> str:
    if outcome in (Outcome.AUTO_EXECUTE, Outcome.PROCEED_FLAGGED):
        return "The submitter role files it under the policy auto-approval (no human needed)."
    if outcome == Outcome.HUMAN_REVIEW:
        return f"@{lead}: over the auto limit, needs your sign-off. Address the submitter role with `approve {dispute_id}` or `reject {dispute_id}`."
    return f"@{lead}: to accept the loss, address the submitter role with `approve {dispute_id}`."


def receipt(dispute_id: str, receipt_body: dict, anchor: str) -> str:
    action = "Representment filed" if receipt_body.get("action") == "REPRESENTMENT" else "Loss accepted"
    return (f"**{dispute_id}** · ✅ {action}. Gateway receipt `{receipt_body.get('receipt_id')}` "
            f"(approval ref audit #{receipt_body.get('approval_ref')}). {anchor}")
