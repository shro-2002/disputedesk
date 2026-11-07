# README claim audit

This file maps each important claim in the root `README.md` to the source code, tests or recorded demo evidence that supports it. It was checked on 2026-11-07 against commit `a61c5df`, plus the uncommitted demo-video assets.

**Status key:**
- **Verified:** confirmed by reading the code, running the command, or the recorded live run.
- **Qualified:** true, with the stated limit written into the README.
- **Removed:** the claim was dropped because it couldn't be supported.

## Product and scope

| Claim | Evidence | Status |
|---|---|---|
| The gateway is a mock; there is no Visa or processor connection | `gateway/app.py` docstring ("Mock payment processor"); no external HTTP calls besides Switch and Anthropic | Verified |
| All dispute data is synthetic | `db/seed.py` seeds cases A–E with dates relative to today | Verified |
| Tested with Switch 0.31.0 | `Makefile` `SWITCH_VERSION_PIN := 0.31.0`; `docs/h1-checklist.md` | Verified |
| The agents use the deprecated SSE transport | `disputedesk/switch_client.py` module docstring | Verified |
| Claude model `claude-sonnet-5-5` | `disputedesk/counsel.py` `DEFAULT_MODEL` | Verified |

## Workflow and agents

| Claim | Evidence | Status |
|---|---|---|
| The webhook listens on 127.0.0.1, takes a body ≤ 4 KB and requires a strict ID format | `agents/evidence.py` `webhook_server`, `MAX_WEBHOOK_BYTES = 4096`, `DISPUTE_ID_RE` | Verified |
| @evidence posts intake, runs CE3.0, writes CE3_CHECKED and DISPOSITION (plus AUTO_APPROVED if small), posts the table and disposition with the audit head, then hands off with `send_targeted_message` | `agents/evidence.py` `process`; `disputedesk/orchestrator.py` `process_dispute` | Verified |
| @evidence ignores room chatter | `agents/evidence.py` `handle` returns None | Verified |
| @evidence creates the "CE3.0 rulebook" room document when it connects | `agents/evidence.py` `_on_connect` | Verified |
| @counsel skips the model for rejected cases | `agents/counsel_agent.py` (the `Outcome.REJECT` branch); `docs/demo-transcript.md` cases B and D | Verified |
| @counsel's draft is prose only and is never read by the approval logic | `orchestrator.save_rebuttal` and the `rebuttals` table; `handle_approval` and `check_approval` don't read it | Verified |
| `@submitter` mentions in model output are rewritten | `counsel.scrub_mentions`; `tests/test_counsel.py::test_llm_draft_never_mentions_submitter` | Verified |
| Prompts name only the matched elements | `counsel.evidence_facts`, `DRAFT_SYSTEM`; `test_facts_name_only_matched_elements` | Verified |
| The LLM is used for intent only as a tie-break, after the identity checks | `counsel.classify_intent` (keywords first; output limited to approve, reject or unclear); `orchestrator.handle_approval` checks sender ID before acting on intent | Verified. Note: `classify_intent` runs in `submitter.handle` before `handle_approval`, but its result is only *used* after the sender, @submitter and case-ID checks pass. The README says "runs only after the identity checks" in the security table; the accurate wording is "is acted on only after". Corrected in the README. |
| @submitter ignores agent senders | `agents/submitter.py` `handle` (`is_agent` check) | Verified |
| @submitter re-assumes the exclusive role on every reconnect | `agents/submitter.py` `_take_role` (on_connect); `switch_client` on_connect runs on every open (`test_on_connect_runs_on_every_open...`) | Verified |
| @submitter reads the approval from the audit log, not the chat | `orchestrator.latest_approval`, `submit` | Verified |
| @submitter waits for a draft before filing a representment | `agents/submitter.py` `submit_ready` | Verified |

## Policy and CE3.0

| Claim | Evidence | Status |
|---|---|---|
| The four outcomes and their conditions | `disputedesk/policy.py` `decide`; `config.yaml`; `tests/test_policy.py` | Verified |
| The $500 limit and the 2-in-60-days rule are demo settings, not Visa rules | `config.yaml` | Verified (stated as configuration) |
| SAFR is a reference model, not regulation | `config.yaml` comment; `switch/room-instructions.md` | Qualified (worded as a design pattern, no compliance claimed) |
| CE3.0: reason code 10.4; ≥ 2 priors; same merchant and credential; 120–365 days inclusive; undisputed, no fraud report; ≥ 2 of 4 elements, with IP or device | `disputedesk/ce3.py` (reason-code gate, SQL filter `is_disputed = 0 AND has_fraud_report = 0`, `_matched_elements`) | Verified against the code |
| This is not a complete Visa representment implementation | Scope of `ce3.py` and `gateway/app.py` | Qualified (stated in the README) |

## Security and trust

| Claim | Evidence | Status |
|---|---|---|
| Approvals are checked against stable sender ID, not display name | `orchestrator.handle_approval` (`sender_id != finops_lead_id`); `FINOPS_LEAD_SENDER_ID` | Verified |
| Denied attempts are audited | `AuditEvent.APPROVAL_DENIED`; transcript audit #9 | Verified |
| The gateway checks: ref present and well-formed, chain intact, record exists, right dispute, right event type for the action, lead-signed human approval, auto approval within limit, not revoked | `gateway/app.py` `check_approval`; `tests/test_gate_attacks.py` | Verified |
| Single use and one settlement per dispute are enforced by the gateway (UNIQUE constraints and a lock), not by the role | `gateway/app.py` `SCHEMA`, `store`; `test_concurrent_replay_exactly_one_success`, `test_second_money_move_on_same_dispute_refused` | Verified |
| The gateway verifies the whole chain on every money call | `gateway/serve.py` `chain_ok`; `check_approval` | Verified |
| Audit backend failure gives 503 with no leak | `_audit_call`; `test_dependency_failure_is_503_without_leak` | Verified |
| Hash formula `HMAC-SHA256(key, prev_hash + canonical_json(body))` | `disputedesk/audit.py` `_compute_hash` | Verified |
| Key holders can forge; tail truncation isn't caught by the chain alone | `test_trust_attacker_with_key_can_mint_any_approval`, `test_trust_tail_truncation_of_revocation_is_undetectable` | Qualified (stated as a limitation) |
| Comparing against room-posted heads is manual | No code compares DB heads with room posts | Qualified (stated) |
| Tamper demo output: chain BROKEN at record #8 | `scripts/verify_audit.py`; demo video at 2:01; `docs/images/tamper-detected.jpg` | Verified (real terminal output; the chain graphic is labelled as an illustration) |
| "Not blockchain, not immutable" | — | Stated as a limitation |

## Switch behaviour

| Claim | Evidence | Status |
|---|---|---|
| `owner_only` addressing set on `disputedesk-gateway`; policy `{owner: true, owner_agents: false, users: []}` | `scripts/h1_setup.py` `addressing`; `docs/h1-checklist.md` | Verified (recorded at H1) |
| Exclusive `submitter` role and aliases created by `create_room` | `scripts/h1_setup.py` (`roles … exclusive: True`, `aliases`) | Verified |
| Switch refused Riya **because her chat account isn't linked to a Switch user** | `docs/demo-transcript.md` / live room: "this chat account isn't linked to a Switch user yet" | Verified. **Qualified:** the README states plainly that the demo did not show a verified non-owner Switch user being refused |
| DisputeDesk would also deny a *linked* non-owner | `handle_approval` compares sender IDs whatever Switch decided; `test_c_waits_riya_refused_lead_approves` | Verified in tests, not demonstrated live |
| Switch does not do CE3.0 or gateway checks | Those live in `ce3.py` and `gateway/app.py` | Verified (README table "What Switch enforces, and what DisputeDesk enforces") |

## Numbers

| Claim | Evidence | Status |
|---|---|---|
| 349 tests pass in about 1 s | `make test` run on 2026-11-07: `349 passed in 1.04s` | Verified |
| 75 CE3.0 tests | `pytest --collect-only`: `test_ce3.py` 23 + `test_ce3_adversarial.py` 52 | Verified |
| Per-file counts in the README's test table | `pytest --collect-only` per file | Verified |
| Case A receipt about 5 s after the webhook | Transcript: intake 08:33:46, receipt 08:33:51 | Verified |
| Case C receipt about 1 s after approval | Transcript: lead 08:34:12, receipt 08:34:13 | Verified |
| Claude letters 2.6–3.2 s (A, C, E) | Counsel agent `latency=` log lines during the recorded run (see `docs/BRIEFING.md`) | Verified from logs at the time; not re-measured |
| Video length 2:43; segment timestamps; hosted at https://youtu.be/A_EF8L1ivME | `FINAL_DisputeDesk_Demo.srt` cue start times (local `demo-video/`, not committed); `ffprobe` duration 162.7 s | Verified |
| No coverage percentage | Coverage isn't measured | Not claimed |

## Removed or rewritten during the audit

- **"Switch proves a non-owner was denied."** Rewritten: Switch's refusal was for an unlinked account.
- **"An exclusive role prevents double filing."** Rewritten: the role limits *who* holds the job; double filing is prevented by the gateway's UNIQUE constraints.
- **"The model runs only after the identity checks."** Rewritten to "acted on only after", because the classifier is called before `handle_approval`.
- **No ROI, merchant counts, dispute-volume statistics, customers or CI badges.** None exist, so none are claimed. The test badge says "(local)".
