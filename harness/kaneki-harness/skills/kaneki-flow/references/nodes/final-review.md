# final-review

**Goal:** the full rulepack over the final subject, and receipts the delivery can trust.

1. Judge every rule in the rules file (all phases). Reuse a code-phase verdict already PASS at code-review
   only if its hunks have not changed since (cite the review).
2. Confirm the evidence: every in-scope case has an OK with captured state, or an accepted N/A.
3. `final-review.md`: one line per rule, `<RULE-ID>: PASS | N/A (reason) | FAIL — evidence`. The gate checks every
   rule id of the rules file is there; any FAIL means the node cannot be approved.

On approval the driver records each repository's subject digest and issues one receipt per repository
(TTL `delivery.receiptTtlHours`). Any later change to a checkout breaks its receipt.
