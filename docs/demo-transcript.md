# DisputeDesk demo transcript (#disputes, live Switch room)

Captured 2026-11-07 from Mattermost (`#disputes`, Switch 0.31.0 bridge) after running `make demo CASE=A|B|C|D|E`, riya-support's approval attempt, and the FinOps lead's approvals of DSP-C and DSP-D. Rebuttals for DSP-A, DSP-C and DSP-E were drafted live by Claude (`claude-sonnet-5-5`, 2.6–3.2 s each); DSP-B and DSP-D are policy rejects, which skip the model by design.

## Room

**disputedesk-evidence** · 08:33:46
> **DSP-A** · Processor webhook: new chargeback, reason code 10.4 (card-not-present fraud), $180.00, merchant MRCH-ACME. Running the Visa CE3.0 check.

**disputedesk-evidence** · 08:33:46
> **DSP-A** · CE3.0 evidence (deterministic SQL, no LLM): **QUALIFIES**
>
> | prior txn | age (days) | matching elements | counts? |
> |---|---|---|---|
> | TXN-A-P1 | 150 | IP, device | ✅ |
> | TXN-A-P2 | 210 | IP, device | ✅ |
>
> 2 qualifying prior transactions: TXN-A-P1 (150 days, IP address + device ID), TXN-A-P2 (210 days, IP address + device ID).

**disputedesk-evidence** · 08:33:46
> **DSP-A** · Disposition **AUTO_EXECUTE**: CE3.0 qualified and $180.00 is within the $500.00 auto-execute limit; auto-executing representment. `audit #3 · 1c29a4c76f6b`

**disputedesk-evidence** · 08:33:46
> @disputedesk-counsel DSP-A: evidence and disposition are posted. Please draft the rebuttal and recommendation.

**disputedesk-counsel** · 08:33:49
> **DSP-A** · Recommend: submit representment (within auto limit).
>
> > To the Issuer, regarding Dispute DSP-A ($180.00, reason code 10.4, merchant MRCH-ACME, disputed transaction TXN-A-D):
>
> The merchant submits this representment under Visa Compelling Evidence 3.0 and presents two prior undisputed transactions that were made with the same card at the same merchant. The first, TXN-A-P1, was processed 150 days before the disputed transaction and matches it on IP address and device ID. The second, TXN-A-P2, was processed 210 days before the disputed transaction and also matches it on IP address and device ID. Both prior transactions were undisputed, and the IP address and device ID used in the disputed transaction are the same as those used in each of them. The merchant asks that the dispute be reversed and the $180.00 be returned to the merchant on the basis of this evidence.
>
> _Rebuttal drafted by Claude; the evidence verdict and disposition above were decided in code, not by the model._ The submitter role files it under the policy auto-approval (no human needed).

**disputedesk-gateway** · 08:33:51
> **DSP-A** · ✅ Representment filed. Gateway receipt `RCPT-A495473EF387` (approval ref audit #3). `audit #4 · 2bc607beb65c`

**disputedesk-evidence** · 08:33:55
> **DSP-B** · Processor webhook: new chargeback, reason code 10.4 (card-not-present fraud), $220.00, merchant MRCH-ACME. Running the Visa CE3.0 check.

**disputedesk-evidence** · 08:33:55
> **DSP-B** · CE3.0 evidence (deterministic SQL, no LLM): **DOES NOT QUALIFY**
>
> | prior txn | age (days) | matching elements | counts? |
> |---|---|---|---|
> | TXN-B-P1 | 45 | IP, device | ❌ |
> | TXN-B-P2 | 90 | IP, device | ❌ |
>
> Prior transactions are 45 and 90 days old; CE3.0 needs at least 2 that are 120–365 days before the dispute.

**disputedesk-evidence** · 08:33:55
> **DSP-B** · Disposition **REJECT**: Prior transactions are 45 and 90 days old; CE3.0 needs at least 2 that are 120–365 days before the dispute. Recommend accepting the loss. `audit #6 · d0c0b524f382`

**disputedesk-evidence** · 08:33:55
> @disputedesk-counsel DSP-B: evidence and disposition are posted. Please draft the rebuttal and recommendation.

**disputedesk-counsel** · 08:33:55
> **DSP-B** · Recommend: accept the loss; CE3.0 evidence is not available.
>
> > No representment: Prior transactions are 45 and 90 days old; CE3.0 needs at least 2 that are 120–365 days before the dispute. Recommend accepting the loss.
>
> _Policy decision — rejected cases skip the model; no rebuttal needed._ @finops-lead: to accept the loss, address the submitter role with `approve DSP-B`.

**disputedesk-evidence** · 08:34:00
> **DSP-C** · Processor webhook: new chargeback, reason code 10.4 (card-not-present fraud), $2,400.00, merchant MRCH-ACME. Running the Visa CE3.0 check.

**disputedesk-evidence** · 08:34:00
> **DSP-C** · CE3.0 evidence (deterministic SQL, no LLM): **QUALIFIES**
>
> | prior txn | age (days) | matching elements | counts? |
> |---|---|---|---|
> | TXN-C-P1 | 130 | account, IP, device | ✅ |
> | TXN-C-P2 | 300 | account, IP, device | ✅ |
>
> 2 qualifying prior transactions: TXN-C-P1 (130 days, account ID + IP address + device ID), TXN-C-P2 (300 days, account ID + IP address + device ID).

**disputedesk-evidence** · 08:34:00
> **DSP-C** · Disposition **HUMAN_REVIEW**: CE3.0 qualified, but $2,400.00 exceeds the $500.00 auto-execute limit; FinOps lead approval required. `audit #8 · 60783ee689b8`

**disputedesk-evidence** · 08:34:00
> @disputedesk-counsel DSP-C: evidence and disposition are posted. Please draft the rebuttal and recommendation.

**disputedesk-counsel** · 08:34:03
> **DSP-C** · Recommend: submit representment once the FinOps lead approves.
>
> > To the Issuer, re: Dispute DSP-C, Visa reason code 10.4, $2,400.00, merchant MRCH-ACME, disputed transaction TXN-C-D.
>
> We submit this representment under Compelling Evidence 3.0 and provide two prior undisputed transactions, each made with the same card at the same merchant as the disputed transaction. The first, TXN-C-P1, occurred 130 days before the disputed transaction and matches it on account ID, IP address, and device ID. The second, TXN-C-P2, occurred 300 days before the disputed transaction and also matches it on account ID, IP address, and device ID. Neither prior transaction was disputed. We ask that you review this evidence and reverse the chargeback for TXN-C-D.
>
> _Rebuttal drafted by Claude; the evidence verdict and disposition above were decided in code, not by the model._ @finops-lead: over the auto limit, needs your sign-off. Address the submitter role with `approve DSP-C` or `reject DSP-C`.

**riya-support** · 08:34:06
> @submitter approve DSP-C

**disputedesk-gateway** · 08:34:07
> @riya-support I only take instructions from my owner, and this chat account isn't linked to a Switch user yet — so I can't tell whether that's you. If it is, link this account to your Switch user in Switch Console and try again.

**disputedesk-gateway** · 08:34:07
> I can't act on that: only the FinOps lead can approve money movements for DSP-C. Attempt logged (audit #9). `audit #9 · 0025b1a14477`

**finops-lead** · 08:34:12
> @submitter approve DSP-C

**disputedesk-gateway** · 08:34:13
> Approved by the FinOps lead. Submitting DSP-C. `audit #10 · cb6a3989221f`

**disputedesk-gateway** · 08:34:13
> **DSP-C** · ✅ Representment filed. Gateway receipt `RCPT-3A60D3399BB2` (approval ref audit #10). `audit #11 · 42de30e545ad`

**disputedesk-evidence** · 08:34:17
> **DSP-D** · Processor webhook: new chargeback, reason code 10.4 (card-not-present fraud), $95.00, merchant MRCH-ACME. Running the Visa CE3.0 check.

**disputedesk-evidence** · 08:34:17
> **DSP-D** · CE3.0 evidence (deterministic SQL, no LLM): **DOES NOT QUALIFY**
>
> | prior txn | age (days) | matching elements | counts? |
> |---|---|---|---|
> | none | — | — | ❌ |
>
> No prior transactions with this merchant on this card; CE3.0 needs at least 2 that are 120–365 days before the dispute.

**disputedesk-evidence** · 08:34:17
> **DSP-D** · Disposition **REJECT**: No prior transactions with this merchant on this card; CE3.0 needs at least 2 that are 120–365 days before the dispute. Recommend accepting the loss. `audit #13 · fcf590bf6d15`

**disputedesk-evidence** · 08:34:17
> @disputedesk-counsel DSP-D: evidence and disposition are posted. Please draft the rebuttal and recommendation.

**disputedesk-counsel** · 08:34:17
> **DSP-D** · Recommend: accept the loss; CE3.0 evidence is not available.
>
> > No representment: No prior transactions with this merchant on this card; CE3.0 needs at least 2 that are 120–365 days before the dispute. Recommend accepting the loss.
>
> _Policy decision — rejected cases skip the model; no rebuttal needed._ @finops-lead: to accept the loss, address the submitter role with `approve DSP-D`.

**finops-lead** · 08:34:24
> @submitter approve DSP-D

**disputedesk-gateway** · 08:34:24
> Accepting the loss on DSP-D as recommended. `audit #14 · a76a1622291a`

**disputedesk-gateway** · 08:34:25
> **DSP-D** · ✅ Loss accepted. Gateway receipt `RCPT-54081938FCEB` (approval ref audit #14). `audit #15 · 08248ca78e76`

**disputedesk-evidence** · 08:34:29
> **DSP-E** · Processor webhook: new chargeback, reason code 10.4 (card-not-present fraud), $300.00, merchant MRCH-ACME. Running the Visa CE3.0 check.

**disputedesk-evidence** · 08:34:29
> **DSP-E** · CE3.0 evidence (deterministic SQL, no LLM): **QUALIFIES**
>
> | prior txn | age (days) | matching elements | counts? |
> |---|---|---|---|
> | TXN-E-P1 | 180 | IP, device | ✅ |
> | TXN-E-P2 | 240 | IP, device | ✅ |
>
> 2 qualifying prior transactions: TXN-E-P1 (180 days, IP address + device ID), TXN-E-P2 (240 days, IP address + device ID).

**disputedesk-evidence** · 08:34:29
> **DSP-E** · Disposition **PROCEED_FLAGGED ⚠**: CE3.0 qualified and $300.00 is within the $500.00 auto-execute limit; proceeding but flagged: customer has 2 other disputes in the last 60 days. `audit #18 · 7010c41dff89`

**disputedesk-evidence** · 08:34:29
> @disputedesk-counsel DSP-E: evidence and disposition are posted. Please draft the rebuttal and recommendation.

**disputedesk-counsel** · 08:34:32
> **DSP-E** · Recommend: submit representment, but review the customer's dispute pattern.
>
> > To the Issuer:
>
> This letter responds to Dispute DSP-E, a $300.00 chargeback under reason code 10.4 for disputed transaction TXN-E-D at merchant MRCH-ACME. We submit this representment under Visa Compelling Evidence 3.0, based on two prior undisputed transactions made with the same card at the same merchant. The first, TXN-E-P1, occurred 180 days before the disputed transaction and matches it on IP address and device ID. The second, TXN-E-P2, occurred 240 days before the disputed transaction and also matches it on IP address and device ID. Because both prior transactions were not disputed and share these two data elements with the disputed transaction, we ask that you review this evidence and reverse the chargeback.
>
> _Rebuttal drafted by Claude; the evidence verdict and disposition above were decided in code, not by the model._ The submitter role files it under the policy auto-approval (no human needed).

**disputedesk-gateway** · 08:34:33
> **DSP-E** · ✅ Representment filed. Gateway receipt `RCPT-52A7BCC27C60` (approval ref audit #18). `audit #19 · 0b6c47b9047d`

## Audit chain

```
$ make tamper-demo
live audit log: chain INTACT (19 records, head 0b6c47b9047d)
tampered copy: rewrote DSP-C's disposition HUMAN_REVIEW -> AUTO_EXECUTE (1 row) in tampered.db
tampered copy: chain BROKEN at record #8 — tampering detected
```
