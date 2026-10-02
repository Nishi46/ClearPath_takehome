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
| 1. Skeleton and first deploy | 1 hr | Done |
| 2. Seed loader and queue | 1.5 hrs | Done |
| 3. Rules engine | 1.5 hrs | Done |
| 4. Review screen and decisions | 2 hrs | Done |
| 5. Submit, marketer view, resubmit and diff | 2 hrs | Done |
| 6. Flag dismissal, snippets, audit trail | 1.5 hrs | Built, local manual walkthrough and deploy check to do |
| 7. Edge cases, microcopy, polish | 2 hrs | In progress: parts A to D done (see phase-7-steps.md) |
| 8. README, deploy check, demo prep | 2 hrs | |
| 9. Buffer, final walkthroughs | remainder | |
| Stretch: dashboard | only if time remains | |

---

## Phase 0: Scope, plan, seed data and rules (done)

- scope.md, assumptions.md, screens.md, seed-data.md written.
- `data/rules.json` (R1-R7) and `data/seed.json` (14 submissions) created.

## Phase 1: Skeleton and first deploy (done)

Live at https://clearpath-review.onrender.com

Detail in the approved skeleton plan.

- FastAPI app, six-table SQLite schema (`app/schema.sql`), idempotent `init_schema()`.
- Shared `base.html` with header: app name, Reviewer/Marketer switcher (cookie), Submit, Reset demo (stub).
- `/healthz`, placeholder queue page, `requirements.txt`, `.gitignore`, README "Run locally".
- Deploy to Render; confirm the public URL in an incognito window.

**Done when:** public URL loads, schema creates twice without error.

## Phase 2: Seed loader and queue (done)

Detail in phase-2-steps.md.

- `app/seed.py`: validates `data/seed.json`, converts day and hour offsets into real UTC timestamps, inserts the 14 submissions with versions, decisions, comments and the #13 dismissal (flags are not inserted yet). Seeds in one atomic transaction on first start when the database is empty; two simultaneous starts cannot double-seed.
- Reset demo: header link to a confirmation page, then `POST /reset` (confirmation field, same-origin check, 10-second cooldown). Restores the exact seed, with the same ids.
- Queue screen (`/`): launch date ascending; version column; the whole row follows the title link (`app/static/queue.js`); overdue and rush rows distinct by words plus color; status, product and channel filters (plain GET form, no JavaScript); item and "need attention" summary; empty states for zero filter results and an empty queue; stacked cards on narrow screens.
- Flag column showed a dash until phase 3, so unchecked items were not shown as clean; it now shows live counts.
- Static files carry a content-hash version in the URL, so a changed stylesheet is never served from a stale browser cache.

**Done when:** a fresh start opens into 14 rows, #11 shows overdue, #1 and #2 show rush, reset twice gives identical state. Verified locally; confirm on the public URL after the next deploy.

## Phase 3: Rules engine (done)

Detail in phase-3-steps.md; rule behavior and known limits in rules-engine.md.

- `app/rules.py`: loads and validates `data/rules.json`; `evaluate(product, channel, copy)` returns flags with rule id, severity, kind, matched text and offsets (null for missing-text rules), ordered by rule id then position. Pure: no database, clock or network. `describe(flag)` gives the name, explanation, snippet and severity words for a flag, so screens never carry their own copy of rule text.
- Phrase rules (R1, R4, R6) are forgiving on case, punctuation, curly quotes and invisible characters; R6 is suppressed when an end date is stated. R2 fires on a percentage near "rate" or "interest" with no APR. R3, R5, R7 flag a missing statement. Rules are scoped by product and channel.
- `app/flags.py`: `evaluate_version` and `store_flags` (replace a version's flags atomically; the caller owns the transaction). Flags are computed at seed time inside the seed transaction, never read from the seed file; a seeded dismissal must point at a flag that fired.
- Queue Flags column is live: distinct rules on the current version, dismissed rules excluded, with the highest severity as a letter (H, M, L) and the full wording for screen readers.
- Tests: table-driven over every seeded version (`tests/test_rules_seed.py`), a test file per rule, scope, ordering, hostile-input and speed tests, and reset, restart and concurrency tests with flags.

**Done when:** `pytest` passes for all 14 items and the queue shows real flag counts. Verified locally and on the public URL.

## Phase 4: Review screen and decisions (done)

Detail in phase-4-steps.md.

- Review screen (`/review/{id}`, optional `?v=N`): asset copy with server-built inline highlights beside flag cards; phrase flags highlight, missing-text flags are "Missing: add this" cards with the rule snippet; each card shows name, severity in words and the explanation; dismissed flags are listed with their note; the two fixed notes ("Flags assist the reviewer. They never decide." and "Rules are illustrative, not legal advice."); version selector, older-version notice, lock banner, read-only comments and a history strip. Two columns on a laptop (title, copy, first flags and decision buttons together at 1366x768), stacked on narrow screens.
- Decisions: `review.record_decision` is the only code that writes one. It takes the write lock, checks the outcome allowlist, the reason (required for request changes and reject, 2,000 characters at most), the current version, and that the version has no decision yet, then writes the decision and the new status in one transaction. `POST /review/{id}/decision` adds the origin check, the reviewer-only guard, and fixed error pages; success redirects (no resubmit on refresh). `app/static/review.js` only adds disabled buttons and a double-click guard; the server enforces everything without it.
- The reviewer recorded is the fixed demo name in `app/roles.py`, never taken from the request. Reviewer-only is a product guard, not authorization (assumption A4).
- Queue integration: a decision updates the queue row, and the back link keeps the queue filters through a validated `?back=` (rebuilt from the three known filters, never passed through).
- Tests: a file per area (`tests/test_review_*.py`), including real-browser layout and script tests in headless Chrome (skipped if Chrome is not installed) and a closing sweep: reset after decisions equals a fresh seed, the route surface, mass assignment and a fixed-seed fuzz run.

**Done when:** a reviewer can take #3 from in review to a decision and a second decision attempt is refused. Verified locally; confirm on the public URL after the next deploy.

## Phase 5: Submit, marketer view, resubmit and diff (2 hrs)

- Submit form: required-field validation with inline errors that keep input; whitespace-only copy rejected; past launch date and launch within 2 business days warn.
- Marketer view ("My submissions"): status and reviewer feedback visible; empty state for a marketer with no submissions.
- Resubmit as a new version: pre-filled from the previous version; unchanged resubmission blocked with a clear message; history preserved after reject then resubmit.
- Version diff between versions (try #5).

- Built (details and step-by-step status in [phase-5-steps.md](phase-5-steps.md)): `app/submit.py` (validation, `create_submission`, `create_version`, pre-check, copy comparison), `app/mine.py`, `app/diff.py` (pure word diff), `app/routes/submit_pages.py` (`/submit`, `/submit/check`, `/mine`, `/resubmit/{id}`, `/marketer`), and `?diff=1` on the review screen. Only `create_submission` and `create_version` write submissions and versions, each in one transaction under the write lock.
- Demo marketer identity: a `marketer` cookie limited to Maya Chen, Jordan Lee (owns #8 and #14, the live resubmit demos) and Sam Patel (no submissions). A label, not a login (assumption A4).
- Rules the server enforces: resubmit only by the submitter, only on the current version, only after changes requested or rejected; approved is locked; a copy equal to the previous version's (after line-ending and trimming normalization) is blocked; at most 300 submissions and 10 versions per submission in the shared demo.
- Tests: about 2,760 in total, including a sweep (`tests/test_phase5_sweep.py`) for the route table, mass assignment, CSRF, XSS, fuzz, caps and reset.

**Done when:** the full loop works unaided: submit, review, request changes, resubmit, approve. Verified locally, including the step 21 manual walkthrough; the public-URL run in incognito is still to do after the next deploy.

## Phase 6: Flag dismissal, snippets, audit trail (1.5 hrs)

- Reviewer can dismiss a flag with a required note; dismissal appears in the audit trail (see #13 and live on #12).
- Comment snippets: one click inserts the rule's snippet text as a comment linked to the rule.
- Comments on a locked version allowed (see #6).
- Audit trail: ordered union of versions, decisions, comments and dismissals, each with who, what and timestamp.

- Built (details and step-by-step status in [phase-6-steps.md](phase-6-steps.md)): `review.dismiss_flag` and `review.add_comment` (the only code that writes a dismissal or a comment, each under the write lock), `POST /review/{id}/dismiss` and `POST /review/{id}/comment`, `?snippet=R2` prefill (a GET that never writes), `app/audit.py` (the ordered trail, loaded in four queries) and an "Audit trail" section on the review screen. `BEFORE UPDATE` triggers stop decisions, comments and dismissals from being edited (schema version 2).
- Rules the server enforces: dismiss and comment are reviewer-only; a dismissal needs a note (1 to 1,000 characters), only on the current version with no decision yet, and is permanent; comments (1 to 2,000 characters, 200 per item) are allowed on any status including approved and rejected, but only on the current version; a dismissal never carries to the next version.
- Tests: about 3,300 in total, including `tests/test_phase6_sweep.py` (route table, mass assignment, role and locked matrices, CSRF, XSS, fuzz, caps, headers) and `tests/test_phase6_integration.py` (queue, My submissions, resubmit, approve, reset).

**Done when:** #12's R4 flag can be dismissed live and appears in the trail; #14's comments show built-from-snippet text. Verified by automated tests, including a headless Chrome check of the snippet script and the laptop layout. The manual walkthrough (step 17 of phase-6-steps.md) and the public-URL run are still to do.

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
