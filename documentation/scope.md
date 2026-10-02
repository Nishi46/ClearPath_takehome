# ClearPath Compliance Review: Scope & Plan

> Take-home project (24h). This document defines the problem, users, scope, assumptions, screens, data model, and success criteria. It doubles as the presentation outline.

---

## 1. Problem Statement

ClearPath Financial is a national online consumer finance company (personal loans, credit cards, mortgage prequalification). It markets through typical channels, including affiliate partners. Every marketing asset must pass compliance review before it goes live.

Today that review runs on **Excel and email**, and it is the bottleneck on growth.

**One-line pitch:** a review queue that makes each review faster and each submission better, so the compliance team can clear more marketing assets per reviewer without lowering the compliance bar.

### Where the time likely goes (hypotheses)

| Pain point | Why it costs throughput |
|---|---|
| Incomplete submissions | Reviewers spend the first round asking for product, channel, launch date, or context |
| Feedback scattered in email | Comments get lost; marketers misread or miss them; extra rounds follow |
| Hard to compare resubmissions | Reviewers re-read the entire asset to find what changed |
| No prioritization | A campaign launching tomorrow sits next to one launching next month |
| Repetitive comments | Reviewers retype the same finding ("add APR disclosure") dozens of times |
| No status visibility | Marketers email to ask "where is my asset?", creating more email |
| Weak audit trail | Reconstructing who approved what, and when, from email is slow and risky |

**Throughput levers this product targets:** reviewer time per item, and number of review rounds per item.

### How this solution solves it

Each pain point maps to a specific feature and to one of the two levers (**T** = reviewer time per item, **R** = review rounds per item).

| Pain point | How the product addresses it | Lever |
|---|---|---|
| Incomplete submissions | Structured form with required fields and inline validation; an incomplete submission cannot be submitted, so the "what product? what channel?" round disappears | R |
| Feedback scattered in email | Decisions and comments live on the submission, tied to a version and optionally to a rule; the marketer sees them in My Submissions | R |
| Hard to compare resubmissions | Every resubmit is a new version with a "changes since previous version" diff, so reviewers read only what changed | T |
| No prioritization | Queue sorts by launch date, highlights overdue and soon-launching items, and filters by status, product and channel | T |
| Repetitive comments | Rule-linked snippets turn a retyped finding ("add APR disclosure") into one click | T |
| No status visibility | Marketers see live status and the latest feedback, replacing "where is my asset?" emails | R |
| Weak audit trail | Versions, decisions, comments and flag dismissals are timestamped and immutable; approved versions are locked | Risk |

**Two features go beyond fixing the existing process:**
- **Automated pre-check flags** surface likely issues in context, inline for phrase rules and as "missing" cards for absent disclosures. This cuts the time a reviewer spends hunting for problems.
- **Pre-check before submitting** lets marketers fix issues themselves, removing whole review rounds. This is the cheapest throughput gain, because it removes work instead of speeding it up.

**What it deliberately does not do:** flags assist and never approve or reject. A human still makes every decision, so throughput improves through faster, better-prepared reviews, not through skipped reviews, and the compliance bar stays where it is.

---

## 2. Users

| User | Role in the process | What they need |
|---|---|---|
| **Compliance reviewer** (primary) | Reviews submissions, decides, gives feedback | Fast triage, issues surfaced in context, reusable feedback, a defensible record |
| **Marketer** (secondary) | Submits assets, responds to feedback | Easy structured submission, clear status, clear feedback, fast resubmission |

Affiliate partners are a likely third user but are **out of scope** for this version.

### Decision: one app, two views (no login)

A role switcher in the header toggles between **Reviewer** and **Marketer** views. It is a demo convenience, not authentication.

**Why both views rather than reviewer-only:**
- Reviewer throughput depends heavily on submission quality, and submission quality is a marketer-side problem. A reviewer-only tool ignores half the loop.
- The marketer view is small (My submissions, status, feedback, resubmit), so the cost is low.
- It lets the demo show the whole loop, including the feedback-then-resubmit handoff.

**Why not separate apps or real auth:** it adds time and no product insight. Role switching is enough for a take-home.

---

## 3. Scope

### In scope (must have)

1. **Structured submission form** with inline validation; incomplete submissions cannot be submitted
2. **Reviewer queue** sorted by urgency, with filters and search
3. **Review screen** with three outcomes: approve, request changes, reject. Comments are attached to each decision
4. **Resubmission** creates a new version, with a visible diff against the previous version
5. **Automated pre-check flags** on the review screen, tied to the exact text in the asset
6. **Reusable comment snippets** tied to rules
7. **Audit trail** per submission (versions, decisions, comments, timestamps)
8. **Marketer view:** "My submissions" with status and feedback
9. **Seed data and a reset button**

### Stretch (only if time remains)

- **Metrics dashboard:** median turnaround time, first-pass approval rate, current backlog, top flagged rules

### Deferred (explicitly later)

- **Image upload.** Text only for v1. Revisit after core scope is done.

### Out of scope

- Login, permissions, real roles
- Email/Slack/Jira integrations and notifications
- Video or rich-media review
- A full legal rules engine or real regulatory advice
- Affiliate partner portal
- Real ClearPath data
- Multi-reviewer assignment workflows, escalation rules, SLAs by contract

### Cut order if time runs short

1. Dashboard
2. Diff view polish (fall back to showing previous version side by side)
3. Search (keep filters)
4. Comment snippets (fall back to free-text comments)

Never cut: the core loop, flags, validation, audit trail.

---

## 4. Assumptions

State these out loud in the presentation.

**People and process**
- A small team of generalist reviewers; any reviewer can pick up any item
- All users are trusted internal users, so no authentication in v1
- Launch date is the main urgency signal
- Throughput is measured by reviewer time per item and number of review rounds

**Assets**
- Assets are text (ad copy, email body, landing page copy) with no images in v1
- One asset per submission

**Rules and flags**
- Rules are a simplified, illustrative starter set, **not legal advice** and not a real ClearPath policy
- Flags assist the reviewer; they **never auto-approve or auto-reject**
- Flags are pattern-based (phrases and required-disclosure checks), not AI judgment
- Each rule can apply to specific products and channels

**Workflow**
- Rejection is final for that version; the marketer fixes and resubmits as a new version
- "Request changes" is for fixable issues; "Reject" is for assets that should not run as conceived
- A reason is required for request changes and reject; optional for approve
- An approved version is locked; edits require a new submission version

---

## 5. Key Product Decisions and Tradeoffs

| Decision | Choice | Reasoning |
|---|---|---|
| Both "request changes" and "reject" | Keep both | Matches how real compliance teams work; the distinction matters for audit and marketer behavior |
| Image support | Text only | Biggest time savings; the core value is in text-based flags and workflow |
| Marketer view | Include, minimal | Reviewer throughput depends on submission quality |
| Flags: assist vs. decide | Assist only | Compliance decisions need human accountability |
| Rush launch dates | Warn, don't block | Marketers sometimes have real deadlines; blocking creates workarounds |
| Dashboard | Stretch | Valuable for managers, but not needed to prove the core loop |
| Auth | None | Not the point of this exercise |

---

## 6. Rules (Starter Set)

Illustrative only. Final list will be 6-8 rules.

| ID | Rule | Applies to | Severity | Kind | Detection | Snippet |
|---|---|---|---|---|---|---|
| R1 | No guaranteed-approval claims | All | High | phrase | "guaranteed approval", "everyone is approved", "no credit check", "can't be denied" | Remove guaranteed-approval language. Approval depends on credit review. |
| R2 | Rate shown requires APR | Loan, Card, Mortgage | High | missing | Percentage near "rate"/"interest" and no "APR" | Rate is shown without APR. Add the APR alongside it. |
| R3 | Equal Housing Lender statement | Mortgage | High | missing | "Equal Housing Lender" absent | Add the Equal Housing Lender statement. |
| R4 | "Prequalified" must not imply final approval | Mortgage, Loan | Medium | phrase | "you're approved", "pre-approved", "approved in minutes" | Use 'prequalify' wording. This isn't a final approval. |
| R5 | Credit approval disclaimer | Loan, Card | Medium | missing | "subject to credit approval" (or close variant) absent | Add 'subject to credit approval' language. |
| R6 | No pressure or false urgency | All | Low | phrase | "act now", "last chance", "hurry", "limited time" with no stated end date | Remove urgency language or state the actual offer end date. |
| R7 | Disclosure reachable in short formats | Paid social, Display | Medium | missing | No "terms apply" or link to full terms | Add 'terms apply' with a link to full disclosures. |

Each rule carries: a name, a plain-language explanation, matched text (phrase rules only), severity, and a **snippet** (a ready-to-use reviewer comment). Missing-text rules have nothing to highlight, so the review screen shows them as "Missing: add this" cards. See `seed-data.md` for the seed set built around these rules.

---

## 7. Screens

### 7.1 Reviewer Queue (home)

- Header: app name, role switcher, "Submit" button, "Reset demo data"
- Filters: status, product, channel; search by title
- Columns: urgency indicator, title, product, channel, launch date, flag count, status, version
- Default sort: launch date ascending; launching soon or overdue items highlighted
- Empty state: "No items match" with a clear-filters action

### 7.2 Review Screen (core)

- **Left:** asset copy with flagged phrases highlighted inline; toggle for "Changes since previous version" (diff)
- **Right:** automated flags list (click a flag to scroll to its text), comments thread, snippet picker, decision buttons
- **Bottom:** history timeline (submitted, decisions, resubmissions)
- Decision requires a reason for request changes and reject
- Decision buttons disabled once decided; locked states are clearly shown

### 7.3 Submit Form

- Fields: title*, product*, channel*, launch date*, asset copy*, notes for reviewer
- Inline validation with specific messages
- Rush warning if launch is within 2 business days (warn, not block)
- Preview of flags before submitting (lets marketers self-correct, reducing rounds)

### 7.4 Marketer View: My Submissions

- List of the marketer's submissions with status and latest feedback
- "Changes requested" items surface first, with a clear "Edit and resubmit" action
- Resubmit pre-fills the previous version's content

### 7.5 Dashboard (stretch)

- Median time to decision, first-pass approval rate, backlog by status, top flagged rules

---

## 8. Data Model

**Submission**
- `id`, `title`, `product` (loan | card | mortgage), `channel` (email | paid_social | affiliate_page | display)
- `launchDate`, `submittedBy`, `createdAt`
- `status`: `new | in_review | changes_requested | approved | rejected`
- `currentVersion`

**Version** (one per submit or resubmit)
- `submissionId`, `versionNumber`, `copy`, `notes`, `createdAt`
- `flags[]`: `{ ruleId, severity, kind (phrase | missing), matchedText, startIndex, endIndex }`. For `missing` flags, `matchedText`, `startIndex`, and `endIndex` are null.
- `flagDismissals[]`: `{ ruleId, note, dismissedBy, timestamp }` (a reviewer-dismissed flag keeps its note and appears in the audit trail)

**Decision**
- `submissionId`, `versionNumber`, `outcome` (approved | changes_requested | rejected)
- `reviewer`, `reason`, `timestamp`

**Comment**
- `submissionId`, `versionNumber`, `author`, `text`, `ruleId` (optional), `timestamp`

**Rule** (static)
- `id`, `name`, `description`, `appliesToProducts`, `appliesToChannels`
- `severity`, `detection`, `snippetText`

### Status flow

```
new -> in_review -> approved
                 -> changes_requested -> (resubmit) -> in_review
                 -> rejected (final for that version; resubmit as new version)
```

The audit trail is the ordered union of versions, decisions, and comments, so no separate model is needed.

---

## 9. Seed Data Plan

**Volume:** 12-15 submissions.

**Coverage:**
- Every status represented at least once
- At least one submission with 2+ versions (resubmission after changes requested)
- One item rejected, then resubmitted and approved
- Launch dates spread: tomorrow, this week, next week, a month out; one already past due
- All three products and all four channels
- Clean assets that trigger zero flags
- Clearly violating assets that trigger multiple flags
- Edge cases: very long copy, near-miss phrasing (to test false positives), an asset with every required disclosure missing

**Reset:** a button restores the original seed state. Seed data loads on first visit.

---

## 10. Edge Cases to Handle

- Empty queue; empty filter results; no flags found (explicitly show "no flags detected")
- Required fields missing; whitespace-only copy; extremely long copy
- Launch date in the past; launch date within 2 business days (warning)
- Resubmitting without changing anything (warn or block)
- Double-clicking a decision button
- Deciding on an already-decided version
- Reject followed by resubmission (new version, history preserved)
- Flag false positives: reviewer can see why a flag fired and dismiss it with a note
- Comments on a locked (approved) version
- Reset during an in-progress review

---

## 11. Success Criteria

**Product**
- A first-time user can complete the full loop (submit, review, request changes, resubmit, approve) without instructions
- Reviewers can see issues in context without leaving the review screen
- Every decision has a visible, timestamped record

**Quality**
- All listed edge cases handled gracefully, with no dead ends or broken states
- Consistent, clear copy for errors, empty states, and warnings
- Works on a standard laptop screen; reasonable on narrow widths

**Deliverables**
- Deployed URL, working in a private browser window
- GitHub repo with README (problem, assumptions, scope, what's next)
- A 5-minute demo along the core loop

---

## 12. Metrics (How We'd Know It Worked)

Not measurable in a take-home, but part of the presentation:

- Median reviewer time per item (target: down)
- Average review rounds per approved asset (target: down)
- First-pass approval rate (target: up, as marketers self-correct)
- Time from submission to decision (target: down)
- Share of reviewer comments that use snippets (adoption signal)

---

## 13. What I'd Build Next (Presentation Close)

1. Image and creative review
2. Real roles, assignment, and notifications (Slack/email)
3. Rules managed by compliance, not hardcoded
4. Affiliate partner submission portal
5. Smarter flagging (context-aware, learned from past decisions)
6. Reviewer workload balancing and SLA alerts
7. Export of audit records for regulators

---

## 14. Open Questions

- Who owns the rule set at a real company, and how often does it change?
- Do affiliates submit directly, or does marketing submit on their behalf?
- Is there a service-level target for review turnaround today?
- Which assets are highest volume, and should the product optimize for them first?

---

## 15. Time Plan

| Phase | Time |
|---|---|
| Scope and plan | 1 hr |
| Seed data and rules | 1 hr |
| Core loop build | 3-4 hrs |
| Differentiator (flags, snippets) | 2 hrs |
| Polish and edge cases | 2 hrs |
| Deploy, README, repo | 1 hr |
| Presentation prep | 1 hr |
| Buffer and sleep | remainder |