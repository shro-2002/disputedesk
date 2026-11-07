# DisputeDesk demo transcript (#disputes, live Switch room)

Captured 2026-11-06 from Mattermost (`#disputes`, Switch 0.31.0 bridge) after running `make demo CASE=A|B|D|E|C`, riya-support's approval attempt, and the FinOps lead's approvals of DSP-C and DSP-D.

## Room

**disputedesk-evidence** · 22:34:38
> **DSP-A** · Processor webhook: new chargeback, reason code 10.4 (card-not-present fraud), $180.00, merchant MRCH-ACME. Running the Visa CE3.0 check.

**disputedesk-evidence** · 22:34:38
> **DSP-A** · CE3.0 evidence (deterministic SQL, no LLM): **QUALIFIES**
> 
> | prior txn | age (days) | matching elements | counts? |
> |---|---|---|---|
> | TXN-A-P1 | 150 | IP, device | ✅ |
> | TXN-A-P2 | 210 | IP, device | ✅ |
> 
> 2 qualifying prior transactions: TXN-A-P1 (150 days, IP address + device ID), TXN-A-P2 (210 days, IP address + device ID).

**disputedesk-evidence** · 22:34:38
> **DSP-A** · Disposition **AUTO_EXECUTE**: CE3.0 qualified and $180.00 is within the $500.00 auto-execute limit; auto-executing representment. `audit #3 · eafb0b0727d3`

**disputedesk-evidence** · 22:34:38
> @disputedesk-counsel DSP-A: evidence and disposition are posted. Please draft the rebuttal and recommendation.

**disputedesk-counsel** · 22:34:38
> **DSP-A** · Recommend: submit representment (within auto limit).
> 
> > The cardholder disputes TXN-A-D ($180.00) under reason code 10.4. Under Visa Compelling Evidence 3.0, the merchant identified prior undisputed transactions on the same card at the same merchant (TXN-A-P1, TXN-A-P2), each 120–365 days before the dispute, sharing at least two identity elements with the disputed transaction including IP address or device ID. This shows the transaction was made by the legitimate cardholder. We request the chargeback be reversed.
> 
> _Rebuttal template draft (no LLM key configured); the evidence verdict and disposition above were decided in code, not by the model._ @submitter will file it under the policy auto-approval.

**disputedesk-gateway** · 22:34:38
> @disputedesk-counsel You're not permitted to direct messages to me in this room — my operator has restricted who can address me here.

**disputedesk-gateway** · 22:34:39
> **DSP-A** · ✅ Representment filed. Gateway receipt `RCPT-7AAE924BF287` (approval ref audit #3). `audit #4 · 7d3f28c636b1`

**disputedesk-evidence** · 22:35:22
> **DSP-B** · Processor webhook: new chargeback, reason code 10.4 (card-not-present fraud), $220.00, merchant MRCH-ACME. Running the Visa CE3.0 check.

**disputedesk-evidence** · 22:35:22
> **DSP-B** · CE3.0 evidence (deterministic SQL, no LLM): **DOES NOT QUALIFY**
> 
> | prior txn | age (days) | matching elements | counts? |
> |---|---|---|---|
> | TXN-B-P1 | 45 | IP, device | ❌ |
> | TXN-B-P2 | 90 | IP, device | ❌ |
> 
> Prior transactions are 45 and 90 days old; CE3.0 needs at least 2 that are 120–365 days before the dispute.

**disputedesk-evidence** · 22:35:22
> **DSP-B** · Disposition **REJECT**: Prior transactions are 45 and 90 days old; CE3.0 needs at least 2 that are 120–365 days before the dispute. Recommend accepting the loss. `audit #6 · 21f009b5b4f4`

**disputedesk-evidence** · 22:35:22
> @disputedesk-counsel DSP-B: evidence and disposition are posted. Please draft the rebuttal and recommendation.

**disputedesk-counsel** · 22:35:22
> **DSP-B** · Recommend: accept the loss; CE3.0 evidence is not available.
> 
> > No representment: Prior transactions are 45 and 90 days old; CE3.0 needs at least 2 that are 120–365 days before the dispute. Recommend accepting the loss.
> 
> _Rebuttal template draft (no LLM key configured); the evidence verdict and disposition above were decided in code, not by the model._ @finops-lead: to accept the loss, reply `@submitter approve DSP-B`.

**disputedesk-gateway** · 22:35:22
> @disputedesk-counsel You're not permitted to direct messages to me in this room — my operator has restricted who can address me here.

**disputedesk-evidence** · 22:35:34
> **DSP-D** · Processor webhook: new chargeback, reason code 10.4 (card-not-present fraud), $95.00, merchant MRCH-ACME. Running the Visa CE3.0 check.

**disputedesk-evidence** · 22:35:34
> **DSP-D** · CE3.0 evidence (deterministic SQL, no LLM): **DOES NOT QUALIFY**
> 
> | prior txn | age (days) | matching elements | counts? |
> |---|---|---|---|
> | none | — | — | ❌ |
> 
> No prior transactions with this merchant on this card; CE3.0 needs at least 2 that are 120–365 days before the dispute.

**disputedesk-evidence** · 22:35:34
> **DSP-D** · Disposition **REJECT**: No prior transactions with this merchant on this card; CE3.0 needs at least 2 that are 120–365 days before the dispute. Recommend accepting the loss. `audit #8 · 989dc31951cf`

**disputedesk-evidence** · 22:35:34
> @disputedesk-counsel DSP-D: evidence and disposition are posted. Please draft the rebuttal and recommendation.

**disputedesk-counsel** · 22:35:34
> **DSP-D** · Recommend: accept the loss; CE3.0 evidence is not available.
> 
> > No representment: No prior transactions with this merchant on this card; CE3.0 needs at least 2 that are 120–365 days before the dispute. Recommend accepting the loss.
> 
> _Rebuttal template draft (no LLM key configured); the evidence verdict and disposition above were decided in code, not by the model._ @finops-lead: to accept the loss, reply `@submitter approve DSP-D`.

**disputedesk-gateway** · 22:35:34
> @disputedesk-counsel You're not permitted to direct messages to me in this room — my operator has restricted who can address me here.

**disputedesk-evidence** · 22:35:46
> **DSP-E** · Processor webhook: new chargeback, reason code 10.4 (card-not-present fraud), $300.00, merchant MRCH-ACME. Running the Visa CE3.0 check.

**disputedesk-evidence** · 22:35:46
> **DSP-E** · CE3.0 evidence (deterministic SQL, no LLM): **QUALIFIES**
> 
> | prior txn | age (days) | matching elements | counts? |
> |---|---|---|---|
> | TXN-E-P1 | 180 | IP, device | ✅ |
> | TXN-E-P2 | 240 | IP, device | ✅ |
> 
> 2 qualifying prior transactions: TXN-E-P1 (180 days, IP address + device ID), TXN-E-P2 (240 days, IP address + device ID).

**disputedesk-evidence** · 22:35:46
> **DSP-E** · Disposition **PROCEED_FLAGGED ⚠**: CE3.0 qualified and $300.00 is within the $500.00 auto-execute limit; proceeding but flagged: customer has 2 other disputes in the last 60 days. Risk flag: customer has 2 other disputes in the last 60 days. `audit #11 · 86a98cc2b9a6`

**disputedesk-evidence** · 22:35:46
> @disputedesk-counsel DSP-E: evidence and disposition are posted. Please draft the rebuttal and recommendation.

**disputedesk-counsel** · 22:35:46
> **DSP-E** · Recommend: submit representment, but review the customer's dispute pattern.
> 
> > The cardholder disputes TXN-E-D ($300.00) under reason code 10.4. Under Visa Compelling Evidence 3.0, the merchant identified prior undisputed transactions on the same card at the same merchant (TXN-E-P1, TXN-E-P2), each 120–365 days before the dispute, sharing at least two identity elements with the disputed transaction including IP address or device ID. This shows the transaction was made by the legitimate cardholder. We request the chargeback be reversed.
> 
> _Rebuttal template draft (no LLM key configured); the evidence verdict and disposition above were decided in code, not by the model._ The submitter role files it under the policy auto-approval (no human needed).

**disputedesk-gateway** · 22:35:48
> **DSP-E** · ✅ Representment filed. Gateway receipt `RCPT-075B936434BB` (approval ref audit #11). `audit #12 · 3677e061daad`

**disputedesk-evidence** · 22:36:35
> **DSP-C** · Processor webhook: new chargeback, reason code 10.4 (card-not-present fraud), $2,400.00, merchant MRCH-ACME. Running the Visa CE3.0 check.

**disputedesk-evidence** · 22:36:35
> **DSP-C** · CE3.0 evidence (deterministic SQL, no LLM): **QUALIFIES**
> 
> | prior txn | age (days) | matching elements | counts? |
> |---|---|---|---|
> | TXN-C-P1 | 130 | account, IP, device | ✅ |
> | TXN-C-P2 | 300 | account, IP, device | ✅ |
> 
> 2 qualifying prior transactions: TXN-C-P1 (130 days, account ID + IP address + device ID), TXN-C-P2 (300 days, account ID + IP address + device ID).

**disputedesk-evidence** · 22:36:35
> **DSP-C** · Disposition **HUMAN_REVIEW**: CE3.0 qualified, but $2,400.00 exceeds the $500.00 auto-execute limit; FinOps lead approval required. `audit #14 · 71aae6750cb6`

**disputedesk-evidence** · 22:36:36
> @disputedesk-counsel DSP-C: evidence and disposition are posted. Please draft the rebuttal and recommendation.

**disputedesk-counsel** · 22:36:36
> **DSP-C** · Recommend: submit representment once the FinOps lead approves.
> 
> > The cardholder disputes TXN-C-D ($2,400.00) under reason code 10.4. Under Visa Compelling Evidence 3.0, the merchant identified prior undisputed transactions on the same card at the same merchant (TXN-C-P1, TXN-C-P2), each 120–365 days before the dispute, sharing at least two identity elements with the disputed transaction including IP address or device ID. This shows the transaction was made by the legitimate cardholder. We request the chargeback be reversed.
> 
> _Rebuttal template draft (no LLM key configured); the evidence verdict and disposition above were decided in code, not by the model._ @finops-lead: over the auto limit, needs your sign-off. Address the submitter role with `approve DSP-C` or `reject DSP-C`.

**riya-support** · 22:36:50
> @submitter approve DSP-C please, customer is waiting

**disputedesk-gateway** · 22:36:50
> @riya-support I only take instructions from my owner, and this chat account isn't linked to a Switch user yet — so I can't tell whether that's you. If it is, link this account to your Switch user in Switch Console and try again.

**disputedesk-gateway** · 22:36:50
> I can't act on that: only the FinOps lead can approve money movements for DSP-C. Attempt logged (audit #15). `audit #15 · 0c40dbc2da81`

**finops-lead** · 22:37:00
> @submitter approve DSP-C

**disputedesk-gateway** · 22:37:00
> Approved by the FinOps lead. Submitting DSP-C. `audit #16 · 012602cc944f`

**disputedesk-gateway** · 22:37:02
> **DSP-C** · ✅ Representment filed. Gateway receipt `RCPT-3E7F896A73A3` (approval ref audit #16). `audit #17 · 6d4a4fc976a4`

**finops-lead** · 22:37:32
> @submitter approve DSP-D

**disputedesk-gateway** · 22:37:32
> Accepting the loss on DSP-D as recommended. `audit #18 · 1241cc9abf38`

**disputedesk-gateway** · 22:37:34
> **DSP-D** · ✅ Loss accepted. Gateway receipt `RCPT-B50A8C3C61CD` (approval ref audit #18). `audit #19 · ae71125a9331`

## Audit chain

```
$ make tamper-demo
live audit log: chain INTACT (19 records, head ae71125a9331)
tampered copy: rewrote DSP-C's disposition HUMAN_REVIEW -> AUTO_EXECUTE (1 row) in tampered.db
tampered copy: chain BROKEN at record #14 — tampering detected
```
