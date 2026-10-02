# ClearPath Compliance Review: Build Schedule

> Ordered build plan for the 24-hour take-home. Budgets come from the time plan in scope.md section 15. Stack: FastAPI + Jinja + HTMX + SQLite (assumptions E3). Build vertical slices, deploy early, and stop building before the final walkthroughs.

## Principles

- **Slice, don't layer.** Each phase ends with something visible and deployed.
- **Deploy in phase 1** so the public URL is proven before features exist.
- **Protect in this order if time runs short:** (1) deployed URL works, (2) core loop end to end, (3) flags work and are explained, (4) edge cases and microcopy, (5) README and tradeoffs, (6) everything else.
- **No new building in the last 2-3 hours.** That window is for fresh-eyes walkthroughs and fixes only.
- Record the real deadline (with timezone) and the stop-building time at the top of this file when known.

## Status

| Phase | Budget | Status |
|---|---|---|
| 0. Scope, plan, seed data and rules | 2 hrs | Done |
| 1. Skeleton and first deploy | 1 hr | Next |
| 2. Seed loader and queue | 1.5 hrs | |
| 3. Rules engine | 1.5 hrs | |
| 4. Review screen and decisions | 2 hrs | |
| 5. Submit, marketer view, resubmit and diff | 2 hrs | |
| 6. Flag dismissal, snippets, audit trail | 1.5 hrs | |
| 7. Edge cases, microcopy, polish | 2 hrs | |
| 8. README, deploy check, demo prep | 2 hrs | |
| 9. Buffer, final walkthroughs | remainder | |
| Stretch: dashboard | only if time remains | |

---

## Phase 0: Scope, plan, seed data and rules (done)

- scope.md, assumptions.md, screens.md, seed-data.md written.
- `data/rules.json` (R1-R7) and `data/seed.json` (14 submissions) created.

## Phase 1: Skeleton and first deploy (1 hr)

Detail in the approved skeleton plan.

- FastAPI app, six-table SQLite schema (`app/schema.sql`), idempotent `init_schema()`.
- Shared `base.html` with header: app name, Reviewer/Marketer switcher (cookie), Submit, Reset demo (stub).
- `/healthz`, placeholder queue page, `requirements.txt`, `.gitignore`, README "Run locally".
- Deploy to Render; confirm the public URL in an incognito window.

**Done when:** public URL loads, schema creates twice without error.

## Phase 2: Seed loader and queue (1.5 hrs)

- `app/seed.py`: convert `launchOffsetDays` and `hoursAgo` into real timestamps; insert 14 submissions with versions, decisions, comments and the #13 dismissal. Runs on first start when the DB is empty. Flags are not inserted yet.
- Reset demo button: clearly labeled as affecting everyone, restores the exact seed.
- Queue screen (`/`): default sort by launch date ascending; overdue and within-2-business-days rows distinct by label and color; filters for status, product, channel; zero-results empty state; columns for title, product, channel, launch date, status, submitter, flag count.

**Done when:** a fresh start opens into 14 rows, #11 shows overdue, #1 and #2 show rush, reset twice gives identical state.

## Phase 3: Rules engine (1.5 hrs)

- `app/rules.py`: load `data/rules.json`; `evaluate(product, channel, copy)` returns flags with rule id, severity, kind, matched text and indices (null for missing-text rules).
- Phrase rules: forgiving on case and punctuation. R6 suppressed when an end date is stated. R2 fires on a percentage near "rate" or "interest" with no "APR".
- Scoped by product and channel.
- Table-driven tests from `data/seed.json` against the expected flags in seed-data.md section 2, including near-misses #10 and #11.
- Wire into the loader so flags are computed at seed time; queue flag count goes live.

**Done when:** `pytest` passes for all 14 items and the queue shows real flag counts.

## Phase 4: Review screen and decisions (2 hrs)

- Review screen: asset copy and flags side by side on a laptop without scrolling; phrase flags highlighted inline; missing-text flags as "Missing: add this" cards; each flag shows name, severity (label, not color only) and explanation.
- "Assist, never decide" note and "rules are illustrative, not legal advice" note.
- Decisions: approve, request changes, reject; reason required for the last two; decision recorded with reviewer and timestamp.
- Guards: double-click safe, a decided version cannot be re-decided, approved versions locked (server-side rejection, not just a disabled button).
- Status transitions follow scope.md section 8.

**Done when:** a reviewer can take #3 from in review to a decision and a second decision attempt is refused.

## Phase 5: Submit, marketer view, resubmit and diff (2 hrs)

- Submit form: required-field validation with inline errors that keep input; whitespace-only copy rejected; past launch date and launch within 2 business days warn.
- Marketer view ("My submissions"): status and reviewer feedback visible; empty state for a marketer with no submissions.
- Resubmit as a new version: pre-filled from the previous version; unchanged resubmission blocked with a clear message; history preserved after reject then resubmit.
- Version diff between versions (try #5).

**Done when:** the full loop works unaided: submit, review, request changes, resubmit, approve.

## Phase 6: Flag dismissal, snippets, audit trail (1.5 hrs)

- Reviewer can dismiss a flag with a required note; dismissal appears in the audit trail (see #13 and live on #12).
- Comment snippets: one click inserts the rule's snippet text as a comment linked to the rule.
- Comments on a locked version allowed (see #6).
- Audit trail: ordered union of versions, decisions, comments and dismissals, each with who, what and timestamp.

**Done when:** #12's R4 flag can be dismissed live and appears in the trail; #14's comments show built-from-snippet text.

## Phase 7: Edge cases, microcopy, polish (2 hrs)

- Run every row of seed-data.md section 3 against the app and fix gaps.
- Empty queue state, zero-result filters, "no flags detected" message.
- Consistent error, empty-state and warning copy; status and severity never color-only; works on a laptop and reasonably on narrow widths; no console errors.
- First impression check: opens into a populated queue, urgent item obvious, one click to a flagged item.

**Done when:** all edge-case rows pass and a first-time user finishes the loop without help.

## Phase 8: README, deploy check, demo prep (2 hrs)

- README: problem and thesis first, deployed link at the top, scope in and out, assumptions (link to documentation), rules not legal advice, the main shortcut stated openly (no auth, shared demo SQLite), a try-this path, what's next, run locally.
- Confirm deployed version equals the repo; test the URL in incognito and on a second device.
- Rehearse the 5-minute demo along the core loop on the deployed version.
- Decide any cuts and prepare to defend them.

## Phase 9: Buffer and final walkthroughs (remainder)

- No new building.
- Walk through as marketer, then as reviewer, with fresh eyes.
- Rerun all edge cases on the deployed version.
- Reset and confirm the first-impression state before submitting.

## Stretch (only if time remains after phase 8)

- Dashboard: median time to decision, first-pass approval rate, backlog by status, top flagged rules.

---

## Risks to watch

- **Cold start on the free host** can hurt the first load: add a keep-warm ping if it does.
- **Shared database reset** affects everyone using the demo: keep the label clear.
- **Rule false positives and misses** are known and intentional (#13, #12, #8): explain them in the demo.
- **Checklist mismatches to resolve:** README must name the real shortcut (SQLite, no auth), not browser-only storage; unchanged resubmission is blocked rather than warned.
