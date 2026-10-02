# ClearPath Compliance Review: Screen Sketches

> Low-fidelity wireframes for each screen in `scope.md` section 7. Laptop-first layouts.

---

## 1. Reviewer Queue (home)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ClearPath Review      [ Reviewer ▾ | Marketer ]     [+ Submit]  [Reset demo] │
├──────────────────────────────────────────────────────────────────────────────┤
│ Status [All ▾]  Product [All ▾]  Channel [All ▾]  🔍 Search title…  Clear    │
│ 14 items · 3 need attention                                                  │
├────┬──────────────────────────┬────────┬─────────┬──────────┬─────┬─────────┬───┤
│ !  │ Title                    │Product │ Channel │ Launch ▲ │Flags│ Status  │ v │
├────┼──────────────────────────┼────────┼─────────┼──────────┼─────┼─────────┼───┤
│ 🔴 │ Spring Refi Email        │ Loan   │ Email   │ Overdue  │ 3 H │In review│ 2 │
│ 🟠 │ Mortgage Prequal Ad      │ Mort.  │ Social  │ Tomorrow │ 2   │ New     │ 1 │
│ ⚪ │ Card Rewards Landing     │ Card   │ Affil.  │ Oct 28   │ 0 ✓ │Approved │ 1 │
└────┴──────────────────────────┴────────┴─────────┴──────────┴─────┴─────────┴───┘
 Empty state: "No items match these filters."  [Clear filters]
```

- Row click opens the review screen.
- Flag count shows the highest severity (H/M/L).
- Urgency is color plus a text label, never color alone.
- Default sort is launch date ascending; overdue and soon-launching items are highlighted.
- On narrow widths the table collapses to stacked rows.

---

## 2. Review Screen (core)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ← Queue   Spring Refi Email   Loan · Email · Launch Oct 3 (2 days)  [v2 ▾]   │
│ Status: In review                                                            │
├─────────────────────────────────────────┬────────────────────────────────────┤
│ ASSET COPY      [ ] Changes since v1    │ FLAGS (3)                          │
│                                         │ ┌────────────────────────────────┐ │
│ Get the cash you need today!            │ │ 🔴 R1 Guaranteed approval      │ │
│ ▓▓Guaranteed approval▓▓ with rates      │ │ "Guaranteed approval"          │ │
│ as low as ▓▓5.9%▓▓. Act now...          │ │ Why: banned claim              │ │
│                                         │ │ [Use snippet] [Dismiss…]       │ │
│ (diff mode: +added / ~~removed~~)       │ └────────────────────────────────┘ │
│                                         │ 🟠 R2 APR missing  (click → text)  │
│ Notes from marketer:                    │                                    │
│ "Targeting existing customers"          │ COMMENTS                           │
│                                         │ Snippets ▾  [type a comment…]      │
│                                         │ ─ Reviewer: Add APR disclosure…    │
│                                         │                                    │
│                                         │ DECISION                           │
│                                         │ Reason* [__________________]       │
│                                         │ [Approve][Request changes][Reject] │
├─────────────────────────────────────────┴────────────────────────────────────┤
│ HISTORY  ● v1 submitted Oct 1 → ● Changes requested (reason) → ● v2 submitted│
└──────────────────────────────────────────────────────────────────────────────┘
```

- Clicking a flag scrolls to and pulses its text in the copy.
- Highlights encode severity (high, medium, low) and always carry an icon or letter.
- "Why it fired" shows the rule explanation and the matched text.
- Missing-text rules (R2, R3, R5, R7) have nothing to highlight, so they appear as "Missing: add this" cards with the rule snippet, and no inline highlight in the copy.
- Dismiss asks for a note; dismissed flags are greyed with the note visible and appear in the audit trail.
- Reason is required for Request changes and Reject; the buttons stay disabled until it is filled.
- A decided version shows a lock banner ("Locked: Approved by Reviewer, Oct 2") and hides the decision buttons.
- Comments on a locked version are allowed and labeled "post-decision".
- Decision buttons guard against double-clicks.

---

## 3. Submit Form

```
┌──────────────────────────────────────────────────────────────┬───────────────┐
│ New submission                                               │ PRE-CHECK     │
│ Title*      [______________________]                        │ (live)        │
│ Product*    [Select ▾]    Channel* [Select ▾]                │ 🔴 R1 …       │
│ Launch*     [date]   ⚠ Launches in 1 business day. A      │ 🟠 R2 …       │
│             rush review may not finish in time.                  │               │
│ Asset copy* [                                  ]             │ or            │
│             [                                  ]             │ ✓ No flags    │
│ Notes for reviewer [___________________]                     │   detected    │
│                              [Cancel]  [Submit for review]   │               │
└──────────────────────────────────────────────────────────────┴───────────────┘
```

- Inline, specific errors under each field (e.g. "Copy can't be empty or only spaces.").
- Rush warning when launch is within 2 business days; a past date gets its own warning. Neither blocks submission.
- Pre-check updates as the marketer types (debounced) and also shows "No flags detected" when clean.
- Flags don't block submitting; a note says reviewers will see them.
- Resubmitting identical copy is blocked with a clear message.

---

## 4. Marketer View: My Submissions

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ My submissions                                                  [+ Submit]   │
│ NEEDS YOUR ACTION (1)                                                        │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ Spring Refi Email · v1 · Changes requested                               │ │
│ │ "Add APR disclosure; remove 'guaranteed'."  (Reviewer, Oct 2)            │ │
│ │                                            [Edit and resubmit]           │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ IN PROGRESS / DONE                                                           │
│ Card Rewards Landing · v1 · Approved ✓       Mortgage Ad · v1 · In review    │
└──────────────────────────────────────────────────────────────────────────────┘
```

- "Changes requested" items surface first.
- "Edit and resubmit" opens the Submit form pre-filled with the previous version, with the reviewer feedback shown at the top.
- Rejected items show the reason and a "Fix and resubmit as new version" action.

---

## 5. Dashboard (stretch)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐                  │
│ │ Median time│ │ First-pass │ │ Backlog    │ │ Rounds per │                  │
│ │ to decision│ │ approval % │ │            │ │ approval   │                  │
│ └────────────┘ └────────────┘ └────────────┘ └────────────┘                  │
│ Backlog by status  ██████ new  ████ in review  ██ changes requested         │
│ Top flagged rules  R2 ██████████  R1 ███████  R5 ████  R6 ██                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

Cut first if time runs short.

---

## 6. Design Notes

- Severity and urgency are never conveyed by color alone (icon, letter, or text accompanies it).
- Laptop-first; reasonable on narrow widths (tables collapse to stacked rows, review panels stack).
- Consistent copy for errors, empty states, and warnings.
- Every empty state has a next action (clear filters, submit, etc.).
