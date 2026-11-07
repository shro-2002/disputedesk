# DisputeDesk — build state (owner: lead)

Deadline: Sat Nov 7 2026, 23:59 IST. Checkpoints: N0–M by 10:00 · N6 by 17:00 · publish by 21:00.

## Task graph

```
N0 contract ──┬─ W1 data+CE3 engine ──┐
              ├─ W2 policy+audit log ─┤
              ├─ W3 mock gateway ─────┼─→ V1/V2/V3 ─→ M merge ─→ H1 ─→ N5 ─→ N6 ─→ N7 ─→ H2 ─→ N8 ─→ H3
              └─ W4 Switch client ────┘
(README outline runs in lead context in parallel with W1–W4: fake edge removed)
```

## File ownership (one writer per file)

| Owner | Files |
|---|---|
| W1 | `db/schema.sql`, `db/seed.py`, `disputedesk/ce3.py`, `tests/test_ce3.py` |
| W2 | `disputedesk/policy.py`, `disputedesk/audit.py`, `tests/test_policy.py`, `tests/test_audit.py` |
| W3 | `gateway/app.py`, `tests/test_gateway.py` |
| W4 | `disputedesk/switch_client.py`, `tests/test_switch_client.py` |
| Lead | everything else: `disputedesk/contracts.py`, `disputedesk/db.py`, `disputedesk/orchestrator.py`, `gateway/serve.py`, `agents/`, `switch/`, `scripts/`, `README.md`, `docs/`, `Makefile`, `STATE.md`, `config.yaml`, `.env.example`, `.gitignore`, `requirements.txt`, `pytest.ini` |

Verifiers (V1–V3) write nothing except new test files they are told to create; fixes go back to the owning worker.

## decided
- Python 3.13, SQLite, FastAPI, stdlib dataclasses (no pydantic). Deps pinned in requirements.txt.
- Money = integer cents. Contract in `disputedesk/contracts.py`.
- Risk signal (≥2 other disputes in 60 days) is computed by `ce3.load_dispute` into `Dispute.recent_dispute_count`, so `policy.decide` stays pure.
- `audit.py` owns its own `audit_log` table (W2 independent of W1 schema).
- Gateway gets audit lookup + chain check **injected** (`create_app(audit_lookup, chain_ok, receipts_db)`), so W3 does not depend on W2's code. Lead wires real ones in `gateway/serve.py` at M.
- Gateway rules: approval ref = audit record id; record must exist, match the path dispute, have an authorising event (representment: AUTO_APPROVED/HUMAN_APPROVED; accept: ACCEPT_LOSS_APPROVED); each ref single-use; broken audit chain ⇒ refuse.
- Switch protocol: docs (switch-docs MCP, queried over HTTP) say current protocol is **WebSocket in / HTTP out**; SSE (`GET /agents/{id}/events`) still supported but deprecated. We keep SSE per ground truth; flagged in README limitations.
- switch-docs MCP not registered in this Claude session; queried via its HTTP endpoint with a helper script.

- Switch setup steps (instructions, roles, documents) are done by "asking an agent in the room" per the payments-room guide; aliases via `!set-alias`; who-may-instruct in agent settings (owner only). Feeds the H1 checklist.
- Orchestrator rules (from V2): LLM output never enters check_ce3/decide/audit.append/X-Approval-Ref; only deterministic code writes AUTO_APPROVED/ACCEPT_LOSS_APPROVED/HUMAN_APPROVED; HUMAN_APPROVED only if sender id == FINOPS_LEAD_SENDER_ID and message names the dispute id and addresses @submitter; submitter reads approval ref from audit log; SUBMITTED event after receipt; persistent receipts DB; chain head posted to room after writes.
- Audit chain is HMAC-SHA256 keyed by AUDIT_HMAC_KEY (deterministic code + gateway only).

## built
- N0: repo skeleton, venv, contracts.py, db.py, config.yaml, Makefile, .gitignore (.env ignored), .env.example.
- README outline (lead, parallel with workers).
- W1–W4 done.
- M (in progress, lead): orchestrator.py (process_dispute / handle_approval / submit / ready_to_submit) + tests/test_orchestrator.py 16 passed; gateway/serve.py (real HMAC audit, persistent receipts, lock); counsel.py (Claude sonnet-5-5 draft + intent, server-side fallback, deterministic template/keyword fallback) + tests 12 passed; settings.py .env loader.

- W3 done: gateway/app.py + tests, 30 passed (self-reported; re-run at M). Notes for M: serve.py must wrap lookup/chain exceptions into the 403 envelope; submitter writes SUBMITTED audit event after a receipt; bad dispute_id path → 422.

- W2 done: policy.py + audit.py + tests, 44 passed (self-reported). Known: tail-deletion undetectable by any hash chain alone → M posts chain head hash into the room after each audit write (room log = external anchor). append inside open txn requires caller commit.

- W1 done: schema, seed (A–E), ce3.py + tests, 20 passed. 60-day window inclusive of day −60.
- V1 (CE3 correctness) and V2 (gate integrity) launched; V3 waits on W4.
- W4 done: switch_client.py + tests, 13 passed. SSE frames parse WS-envelope or bare. Approval auth must use `payload.sender` (stable id), NOT sender_name (display name, spoofable) → config needs FINOPS_LEAD_SENDER_ID, captured at H1. Act only when `addressed` true. OPEN: (1) SSE data shape undocumented; (2) no documented thread reply field on POST /agents/{id}/message → likely cut #3 (post at root), confirm via list_operations() at H1; (3) agent own sender id discovered at H1.
- V3 launched.
- V1 round 1: 1 MEDIUM (reason code not checked) + 3 LOW (merchant/card source, reason wording) → W1 fixed; suite 162 passed. V1 CLOSED.
- Docs: external agents register via `POST /agents` (Bearer = Gateway registration key) → `{id, api_key}` once. Room setup is scriptable via ops `POST /agents/{id}/ops/{op}` (define_role, assume_role, create_room_document, update_agent_detail{addressing}, invite_agent_to_room, list_participants, read_context). H1 shrinks to: start Console+server, create room, create registration key, link Mattermost account, create riya-support. scripts/ register + configure the rest.
- V3 round 1 (clean copy, `make test` 158/159, only failure = V1 bug since fixed): lead fixed README quickstart visible, Makefile `.venv` dep + `-include .env`/export + `key` target generating AUDIT_HMAC_KEY, settings.load_env, DB path resolved vs repo root, constraints.txt lock, data/.gitkeep. V3 CLOSED pending final run at M.
- V2 round 1: HIGH unkeyed hash (forgeable chain) → W2 HMAC; HIGH AUTO_APPROVED any amount → W3 payload amount check; MEDIUM actor/single-settlement/revocation/fullmatch → W3. Orchestrator rules from V2 adopted (see decided).

## verified
- N5 DONE: agents/ (evidence: webhook :8098 → CE3/policy/audit → evidence table → targeted hand-off; counsel: draft+recommendation; submitter: assume_role on every connect, DB-polled submits, approval checks), CE3.0 rulebook room document created via op, scripts/new_dispute.py, scripts/verify_audit.py.
- N6 DONE (22:45 Fri, ahead of Sat 17:00): live room run of A–E. A AUTO_EXECUTE → RCPT-7AAE924BF287; B REJECT with "45 and 90 days old; CE3.0 needs … 120–365 days"; C HUMAN_REVIEW → riya refused twice (Switch owner_only reply + DisputeDesk APPROVAL_DENIED audit #15) → lead approves → RCPT-3E7F896A73A3; D REJECT → lead accepts loss → RCPT-B50A8C3C61CD; E PROCEED_FLAGGED ⚠ → RCPT-075B936434BB. `make audit` intact (19 records); `make tamper-demo` copy broken at #14. Transcript: docs/demo-transcript.md. Tests 343 passed.
- Known noise fixed: agent text containing "@submitter" triggers Switch's refusal reply (agents can't address an owner_only agent) → counsel now says "address the submitter role with `approve DSP-C`".
- No ANTHROPIC_API_KEY in .env yet → counsel used deterministic template; add key for Claude drafts.
- H1 DONE (22:35 Fri). Server = official compose stack ghcr.io/sandbox-quantum/standalone-compose:0.31.0 (profiles collab+gateway) via `make server-init server-up`, NOT Console's native server (no Gateway web there → no registration keys). Runs on colima profile `switch` (vz + Rosetta; docker context colima-switch) because mattermost-team-edition:11.9.0 is amd64-only and SIGSEGVs under QEMU. Seeder linked finops-lead@switch.local ↔ Mattermost finops-lead. User created registration key in Gateway (localhost:3000).
- Agents registered (POST /agents): evidence, counsel, gateway; keys in .env. Gateway addressing set by sibling agent via update_agent_detail (owner-only, "never yourself") → verified policy {owner:true, owner_agents:false, users:[]}.
- Room #disputes created via create_room op (one call: instructions, aliases evidence/counsel, exclusive role submitter, agents, users finops-lead + riya-support). Verified via get_room_detail + Mattermost channel members. SWITCH_ROOM_ID in .env.
- Live ops schemas in switch/ops-schemas.json (53 ops). post_message op supports thread_id → in-thread replies possible (no cut #3 needed). assume_role binds to live connection; exclusive lease auto-releases on disconnect → gateway process assumes role at every connect. create_room_document needs connected room → N5.
- H1 terminal part (21:45 Fri): colima running (docker 29.5.2, 4 cpu, 8 GB); Switch Console dmg downloaded with user approval, verified notarized (Developer ID Sb Technology, Inc 2SX74VJ95S), installed, launched with DOCKER_HOST=colima socket. scripts/h1_setup.py (`make h1`) written: discover, register, ops dump, riya; room/invite/alias/role phases finalized after live ops schemas are read.
- V2 round 2 (2 LOW: unhashable outcome 500, create_app config validation) → W3 fixed. V2 CLOSED after 2 rounds. Accepted trust boundaries: key holder = approver; tail truncation needs room-anchored head; HUMAN_REJECTED from anyone revokes (blocks only).
- M DONE (21:28 Fri, ahead of Sat 10:00 checkpoint): `make test` 337 passed in repo; clean copy (git-shippable files only) `make setup` exit 0 + `make test` 337 passed + AUDIT_HMAC_KEY generated. Python<3.10 now fails fast with a clear message. V3 CLOSED.
- Lead re-ran W1–W3 suites: 94 passed (21:16 IST).

## blocked
- (none)

## remaining
- N7 → H2 → N8 → H3
- Note: Switch refuses an alias that collides with a role name, so `@submitter` is addressed via the exclusive ROLE `submitter` (held by disputedesk-gateway), not an alias. Aliases: @evidence, @counsel.

## cuts taken
- (none)
