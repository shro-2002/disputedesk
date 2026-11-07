# DisputeDesk: 3-minute demo storyboard

Setup before recording:
- `make reset`;
- `make gateway` and `make agents` running in two terminals;
- Mattermost `#disputes` open as `finops-lead`, with a private window logged in as `riya-support`.

| Time | Screen | Say |
|---|---|---|
| **0:00–0:20** The problem | Title card, then `#disputes` | "Card-not-present fraud chargebacks are winnable under Visa's Compelling Evidence 3.0, but the rule is fiddly, so merchants give up. DisputeDesk is a Switch room where agents build the defense and a human signs off on the money." |
| **0:20–0:45** Case A | Terminal: `make demo CASE=A`. Room: intake, evidence table (150 and 210 days, IP + device ✅), `AUTO_EXECUTE`, counsel draft, receipt | "@evidence runs CE3.0 as plain SQL; no model decides this. $180 is under the auto limit, so the policy approves it and @submitter files it. Every step leaves an audit hash in the room." |
| **0:45–1:10** Case B | `make demo CASE=B`. Room: `DOES NOT QUALIFY`, REJECT "45 and 90 days old; CE3.0 needs … 120–365 days" | "Same IP, same device, but too recent. The agent knows the real rule, so it recommends accepting the loss and says exactly why." |
| **1:10–1:40** Case C waits | `make demo CASE=C`. Room: `HUMAN_REVIEW`, $2,400, counsel asks the FinOps lead | "Over $500, so it stops and waits for a human." |
| **1:40–2:10** riya refused, lead approves | Riya's window: `@submitter approve DSP-C`. Show both refusals (Switch's owner-only reply, then DisputeDesk's "Attempt logged (audit #…)"). Lead's window: `@submitter approve DSP-C`. Receipt arrives | "Support can't approve money. Switch refuses her first, because @submitter answers only its owner, and our own check refuses her again and logs it. When the FinOps lead approves, it files." |
| **2:10–2:40** Tamper test | Terminal: `make tamper-demo`: live chain INTACT, then copy with DSP-C rewritten to AUTO_EXECUTE: chain BROKEN at #14 | "Every decision is HMAC-chained. Someone quietly rewrites the $2,400 case to auto-approve, and the chain breaks. The gateway refuses anything while the chain is broken." |
| **2:40–3:00** Why Switch | README "Why Switch?" table | "The exclusive role means exactly one agent can move money. Who-may-instruct means only the FinOps lead drives it. Room instructions and the rulebook document keep every agent on one rule. And the shared room log anchors the audit chain." |
