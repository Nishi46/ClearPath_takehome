# ClearPath Compliance Review: Rules and Seed Data

> Designed backward from what the demo needs to show: every rule has an asset that triggers it, and every seed item exists to demonstrate one specific behavior. Rules are illustrative, not legal advice.

## Principles

- Launch dates, decision times, and comment times are **offsets from today**, never fixed dates, so urgency highlighting stays meaningful.
- Flags are computed by the rules engine at seed time, not hardcoded, so seed data and live behavior cannot drift.
- One submission holds many versions (a resubmission is a new version, not a new submission).
- Fixed demo names: marketer **Maya Chen** (most items), **Jordan Lee** (a few), reviewer **Alex Rivera**.

---

## 1. Rules (starter set)

| ID | Rule | Applies to | Severity | Kind | Detection | Snippet (reviewer comment) |
|---|---|---|---|---|---|---|
| R1 | No guaranteed-approval claims | All | High | phrase | "guaranteed approval", "everyone is approved", "no credit check", "can't be denied" | Remove guaranteed-approval language. Approval depends on credit review. |
| R2 | Rate shown requires APR | Loan, Card, Mortgage | High | missing | A percentage near "rate"/"interest" and no "APR" in the copy | Rate is shown without APR. Add the APR alongside it. |
| R3 | Equal Housing Lender statement | Mortgage | High | missing | "Equal Housing Lender" absent | Add the Equal Housing Lender statement. |
| R4 | "Prequalified" must not imply final approval | Mortgage, Loan | Medium | phrase | "you're approved", "pre-approved", "approved in minutes" | Use 'prequalify' wording. This isn't a final approval. |
| R5 | Credit approval disclaimer | Loan, Card | Medium | missing | "subject to credit approval" (or close variant) absent | Add 'subject to credit approval' language. |
| R6 | No pressure or false urgency | All | Low | phrase | "act now", "last chance", "hurry", "limited time" with no stated end date | Remove urgency language or state the actual offer end date. |
| R7 | Disclosure reachable in short formats | Paid social, Display | Medium | missing | No "terms apply" or link/reference to full terms | Add 'terms apply' with a link to full disclosures. |

**Design points**

- **Missing-text rules (R2, R3, R5, R7)** flag an absence, so there is no text to highlight. The review screen shows them as "Missing: add this" cards. Flag `startIndex`/`endIndex` are null for these.
- **Phrase rules (R1, R4, R6)** highlight the matched text inline.
- Rules only run where they apply (R3 never fires on a loan email), which keeps noise down.
- Matching is forgiving on case and punctuation but specific: "money-back guarantee" must not trip R1, and "limited time: offer ends Oct 31" must not trip R6.

---

## 2. Seed submissions (14)

Today is assumed to be a Thursday; offsets are launch date relative to today.

| # | Title | Product / Channel | Launch | Status (versions) | Why it exists |
|---|---|---|---|---|---|
| 1 | Personal loan holiday email | Loan / Email | +1 | New (v1) | Rush warning, top of queue. "Guaranteed approval, rates as low as 5.99%": R1, R2, R5 |
| 2 | Cashback card paid social | Card / Paid social | +2 | New (v1) | Rush; short format fires R7 (and R5) |
| 3 | Mortgage prequal landing page | Mortgage / Affiliate | +5 | In review (v1) | Missing Equal Housing (R3) and "you're approved" (R4) |
| 4 | Debt consolidation display ad | Loan / Display | +6 | New (v1) | High-severity phrase buried in otherwise decent copy: R1, R7 |
| 5 | Balance transfer email | Card / Email | +8 | In review (v2) | v1 changes requested (R2, R5), v2 fixed. Diff demo and two-version history |
| 6 | Refinance email series pt 1 | Mortgage / Email | +10 | Approved (v1) | Fully clean, disclosures in footer, "no flags detected"; locked; one post-decision comment |
| 7 | Student-friendly card promo | Card / Paid social | +12 | Approved (v2) | v1 rejected for a concept problem not fixable by edit; v2 is a new concept, approved. Reject-then-approve audit trail |
| 8 | Quick cash loan landing page | Loan / Affiliate | +14 | Rejected (v1) | Aggressive affiliate copy (R1, R4, R6) plus "everyone gets a yes", which no rule catches (recall limit). Final; can be resubmitted live |
| 9 | Rate-drop mortgage email | Mortgage / Email | +20 | New (v1) | Equal Housing present, rate with no APR: R2 fires alone |
| 10 | Brand awareness display ad | Card / Display | +25 | New (v1) | Zero flags; near-miss "money-back guarantee" must not fire R1 |
| 11 | Spring loan promo | Loan / Email | -1 | In review (v1) | Overdue; near-miss "limited time: offer ends Oct 31" must not fire R6 |
| 12 | Long-form mortgage guide | Mortgage / Affiliate | +30 | New (v1) | 500+ words; an explanatory use of "pre-approved" fires R4 undismissed, so a reviewer can dismiss it live |
| 13 | "Why we don't promise guaranteed approval" email | Loan / Email | +9 | Approved (v1) | R1 false positive already dismissed with a note, visible in the audit trail |
| 14 | Home equity display ad | Mortgage / Display | +3 | Changes requested (v1) | Every missing-text rule fires (R2, R3, R7); reviewer comments built from snippets |

### Coverage

- **Statuses:** new 1, 2, 4, 9, 10, 12; in review 3, 5, 11; changes requested 14; approved 6, 7, 13; rejected 8
- **Version history:** #5 (two versions), #7 (reject, then approve)
- **Products and channels:** all 3 products, all 4 channels
- **Launch spread:** overdue #11, tomorrow #1, this week #1-4 and #14, next week #5 and #13, month out #12
- **Content mix:** clean (#6, #10), violating (#1, #4, #8, #14), borderline (#10, #11, #12, #13)

---

## 3. Edge-case coverage (scope.md section 10)

| Edge case | Covered by |
|---|---|
| No flags found | #6, #10 |
| Empty filter results | e.g. Status = Rejected + Product = Card |
| Empty queue | Test fixture only (not seedable) |
| Missing fields, whitespace-only copy | Form validation tests |
| Very long copy | #12 |
| Launch in the past | #11 |
| Launch within 2 business days | #1, #2 on any weekday; #14 (+3 days) is rush on some weekdays and not others, since the rule counts business days |
| Resubmit without changes | Try on #14 (blocked) |
| Double-click a decision; decide on an already-decided version | Live on #3 or #5; #6, #7, #8 are locked (direct POST rejected in test) |
| Reject then resubmit, history preserved | #7 (done), #8 (resubmit live) |
| Flag false positive: dismissed / live | #13 / #12 |
| Comment on a locked version | #6 |
| Reset during an in-progress review | Manual test |
| Violation the rules miss | #8 |

---

## 4. Copy approach

- Hand-write each item at 60-150 words in a believable marketing voice, with violations woven in rather than screaming. Only #12 is long.
- Clean items put disclosures in a real-looking footer so "no flags detected" looks earned.
- Each seeded record includes the submission, 1-2 versions, decisions (Alex Rivera) with reasons, comments (some rule-linked), and the dismissal for #13.
