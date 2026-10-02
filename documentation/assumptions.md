# ClearPath Compliance Review: Assumptions


## How to read this

- **Assumption:** what I'm treating as true or deciding
- **Why:** the reasoning behind it
- **Tradeoff:** what this gains and what it gives up
- **Revisit if:** the signal that should change the decision

Assumptions fall into five groups: users and process, assets, rules and flags, workflow, and product and technical scope.

---

## A. Users and Process

### A1. A small team of generalist reviewers; any reviewer can pick up any item
- **Why:** The prompt describes one "compliance marketing team" with a shared Excel tracker. That implies a pooled queue, not specialist routing by product.
- **Tradeoff:** Keeps the queue simple (one list, no routing logic). Gives up specialization: a real team might want mortgage items routed to a mortgage-trained reviewer, and a pooled queue can't do that.
- **Revisit if:** The team splits by product or seniority, or a regulation (e.g. mortgage) needs a specific licensed reviewer.

### A2. Two users: reviewer (primary) and marketer (secondary)
- **Why:** The prompt targets compliance team throughput, so the reviewer is the primary user. But reviewer workload is driven by submission quality, which the marketer controls. Serving only the reviewer would leave the largest source of rework untouched.
- **Tradeoff:** The marketer view is extra build time. In exchange, the demo shows the full loop and addresses the cause of rework, not just the symptom.
- **Revisit if:** Marketers already have a separate intake tool, or the compliance team wants a reviewer-only system.

### A3. Affiliates submit directly, as a third role on the same app
- **Why:** Affiliates often produce the riskiest copy, so their assets should reach the reviewer without a marketer retyping them. A third role reuses the whole submit, flag, review and resubmit loop. Identity works as it does for marketers (A4): a demo label picked in the browser, never a login.
- **Rules:** Partners can submit only for the `affiliate_page` channel, and the server enforces it. A partner can resubmit only their own items. Partner names never overlap marketer names, because the submitter name is what marks an item as a partner item (no schema change). Reviewers see a Partner badge and a Submitted by filter in the queue.
- **Tradeoff:** No partner onboarding, per-partner reporting or access control, and a partner can switch to any other partner or marketer in the demo, like any role. Keeps the build small and the review loop identical for everyone.
- **Revisit if:** Partners need real accounts, or the volume needs per-partner views, SLAs or contracts.

### A4. No authentication; all users are trusted internal users
- **Why:** Login is not what is being evaluated. The prompt says reviewers are judged on product intuition and detail, not architecture. A role switcher in the header demonstrates both views.
- **Tradeoff:** Saves meaningful time and keeps the demo frictionless for evaluators. Gives up real accountability: with no identity, "who approved this" is a label, not a verified fact, which matters for a real audit trail.
- **Revisit if:** Moving beyond a demo. Real identity is a prerequisite for a defensible audit record.
- **In the build:** the review screen records decisions as one fixed demo reviewer (Alex Rivera), and only the reviewer role can post a decision. That is a product guard (a marketer should not approve their own copy), not authorization: anyone can switch the role in the header or send a request with no cookie, which counts as reviewer.

- **In the build (marketers):** "who I am as a marketer" is a `marketer` cookie limited to three demo names (Maya Chen, Jordan Lee, Sam Patel). `submitted_by` comes only from that cookie, never from a form, and only the submitter's identity can resubmit. Like the role, it is a demo label: anyone can switch it, so it narrows what the page shows and is not access control.

### A5. Launch date is the main urgency signal
- **Why:** Marketing deadlines are the most concrete, universal reason a review is urgent, and the field is easy for marketers to supply accurately.
- **Tradeoff:** Simple, explainable sorting. Gives up nuance: risk level, campaign spend, or channel could matter more than date. A low-risk email launching tomorrow outranks a high-risk mortgage page launching in three days.
- **Revisit if:** Reviewers report that date-based sorting hides risky items.

### A6. Throughput is measured by reviewer time per item and number of review rounds
- **Why:** These are the two levers that directly determine how many assets a team can clear. They also map to specific product features: pre-checks and snippets reduce time per item; structured intake and self-check reduce rounds.
- **Tradeoff:** Gives a clear frame for judging every feature. Gives up other measures, such as quality of decisions or cost of a missed violation, which are harder to measure but more important in compliance.
- **Revisit if:** Leadership defines success differently (for example, fewer post-launch violations).

---

## B. Assets

### B1. Assets are text only in version one
- **Why:** Text is where pattern-based flagging is reliable, and it is where most of the repeated reviewer effort sits (disclosures, banned phrases). Images are deferred by decision.
- **Tradeoff:** Lets the time go into flags, diff, and edge cases instead of upload handling and rendering. Gives up realism: many ads are image-led, and a disclosure inside an image is invisible to text checks.
- **Revisit if:** Image-led assets are a major share of reviews. This is the first thing to add after core scope.

### B2. One asset per submission
- **Why:** Keeps the data model and the review screen simple. A campaign can be submitted as several items.
- **Tradeoff:** Simple versioning and clear decisions per asset. Gives up campaign-level review, where a reviewer would want to see an email, its landing page, and its social post together for consistency.
- **Revisit if:** Reviewers need to approve campaigns as a unit.

### B3. Asset copy is entered or pasted as plain text
- **Why:** Reviewers need to see the exact words. Plain text avoids formatting issues and makes highlighting and diffing reliable.
- **Tradeoff:** Reliable flags and diffs. Gives up how the asset really looks (layout, font size of disclosures, placement), which is itself a compliance concern for "clear and conspicuous" rules.
- **Revisit if:** Disclosure placement or formatting becomes part of what reviewers check.

---

## C. Rules and Flags

### C1. The rule set is simplified and illustrative, not legal advice
- **Why:** ClearPath is fictional, so there is no real policy. The rules exist to show how the product works. Presenting them as real legal guidance would be inaccurate and risky.
- **Tradeoff:** Lets the demo be concrete and believable. Gives up authority: the rules shouldn't be taken as a compliance standard. The README should say so clearly.
- **Revisit if:** A real compliance team supplies its actual policies.

### C2. Flags assist reviewers; they never auto-approve or auto-reject
- **Why:** Compliance decisions carry legal accountability and need a human decision-maker. Auto-decisions would also be unsafe given that flags are only pattern-based.
- **Tradeoff:** Preserves trust and accountability, and avoids the worst failure (an unreviewed violation going live). Gives up some throughput: a fully clean asset still requires a human click, so the efficiency gain comes from speed of review, not removal of review.
- **Revisit if:** Compliance leadership decides low-risk, zero-flag items can follow a lighter path. That would be a policy decision, not a product one.

### C3. Flags are pattern-based (phrases and missing required text), not AI judgment
- **Why:** Pattern rules are deterministic, explainable, and testable. A reviewer can see exactly why something fired. That predictability is more valuable than coverage in a compliance setting, and it is achievable in 24 hours.
- **Tradeoff:** Transparent, reliable, easy to verify. Gives up recall: it misses paraphrased violations ("everyone gets a yes") and can fire on harmless text (false positives).
- **Revisit if:** Missed violations are a bigger concern than explainability, in which case an LLM-based second pass could be added, still assistive only.

### C4. Reviewers can dismiss a flag with a note
- **Why:** Pattern rules will produce false positives. If reviewers can't override a flag, they will distrust the tool. A dismissal with a note also keeps the audit record honest.
- **Tradeoff:** Builds reviewer trust and handles false positives. Gives up strictness: a reviewer could dismiss a valid flag, so the dismissal needs to be recorded.
- **How it is bounded (built):** only a reviewer can dismiss, a note is required (1 to 1,000 characters), and only on the current version before any decision. A dismissal is permanent: no undo or edit, and the database refuses updates to it. It hides that rule's flag on that version only, and the stored flag is kept as evidence. The next version is checked afresh. Everything appears in the audit trail.
- **Comments on locked items:** reviewers can still comment on an approved or rejected item (a comment is not a decision and never changes status). Marketer replies in comments are out of scope.
- **Revisit if:** Dismissals are being misused, which would call for a second approver on high-severity flags.

### C5. Each rule can apply to specific products and channels
- **Why:** Requirements differ by product (Equal Housing language is for mortgage) and channel (a paid social post has limited space). Applying every rule to every asset would create noise.
- **Tradeoff:** Fewer irrelevant flags, so reviewers trust the flags that do appear. Gives up simplicity: rule configuration is more complex than a flat list.
- **Revisit if:** The rule matrix becomes hard to maintain.

### C6. Marketers see flags before submitting
- **Why:** Catching an issue before submission removes a whole review round, which is the cheapest throughput gain available.
- **Tradeoff:** Reduces rounds and reviewer load. Gives up some control: marketers can learn to word around the patterns, which makes the flags less effective over time.
- **Revisit if:** Evidence appears of marketers gaming the checks. Human review remains the safeguard.

---

## D. Workflow

### D1. Both "request changes" and "reject" exist
- **Why:** They mean different things. Request changes means fixable; reject means this should not run as conceived. The distinction matters for audit records and for how marketers respond.
- **Tradeoff:** More realistic and a more useful record. Gives up simplicity: two negative outcomes need clear definitions in the interface.
- **Revisit if:** Users can't tell the two apart in practice, which would argue for collapsing them.

### D2. Reject is final for that version; the marketer resubmits as a new version
- **Why:** Keeps history immutable, so every decision stays tied to the exact text it judged. That is what makes the audit trail trustworthy.
- **Tradeoff:** Clean, defensible history. Gives up flexibility: there is no "reopen" for a rejection made in error, so the product needs a clear path for that (a new version).
- **Revisit if:** Reviewers need to correct mistaken decisions without creating a new version.

### D3. A reason is required for request changes and reject; optional for approve
- **Why:** A marketer can't fix what isn't explained, and an unexplained rejection is a weak audit record. Approvals need less justification.
- **Tradeoff:** Better feedback and cleaner records, fewer follow-up questions. Gives up some reviewer speed, partly offset by snippets.
- **Revisit if:** Reviewers find the requirement slows obvious cases. A rule-linked snippet could count as the reason.

### D4. Approved versions are locked
- **Why:** An approval applies to specific text. Editing after approval would invalidate it without anyone noticing.
- **Tradeoff:** Protects the integrity of approvals. Gives up convenience: a typo fix after approval requires a new version and a new review.
- **Revisit if:** Compliance defines a class of minor edits that can bypass review. That would need a policy, not a quiet loophole.

- **In the build:** a change after approval is out of scope. The resubmit page says the item is approved and locked, and the server refuses it.

### D7. An unchanged resubmission is blocked, not warned
- **Why:** A reviewer decides on the copy. Resubmitting identical copy (after line-ending and trimming normalization) would cost them a full review for nothing, so the server refuses it with a message telling the marketer to edit or reply to the reviewer. A notes-only or launch-date-only change counts as unchanged.
- **Tradeoff:** Protects reviewer time. Gives up the case where a marketer only wants to add a note or move a date; that has to go through the reviewer.
- **Revisit if:** Reviewers want a lighter path for date-only changes.

### D5. Rush launch dates produce a warning, not a block
- **Why:** Marketers sometimes have genuine short deadlines. Blocking them would push people to work around the tool, such as going back to email.
- **Tradeoff:** Keeps the tool the path of least resistance. Gives up some discipline: it can't force marketers to plan ahead.
- **Revisit if:** Rush submissions overwhelm the queue, which could justify an escalation path or a reason field.

### D8. "Today" is the UTC date
- **Why:** One date source (`app/clock.py`) keeps the queue label, the submit-form warning and the seed dates in agreement, and avoids daylight-saving surprises. Rush counts Monday to Friday only, with no holidays.
- **Tradeoff:** A user far from UTC can see "today" change a few hours early or late (a launch at 11pm local may read as already past). Accepted for a demo.
- **Revisit if:** Real users in several time zones need a per-user or per-market date.

### D6. No assignment or claiming in version one
- **Why:** With a small pooled team, claiming adds a step to every review. Open items are visible to everyone.
- **Tradeoff:** Fewer clicks, simpler model. Gives up protection against two reviewers working the same item.
- **Revisit if:** The team grows or double-reviewing becomes a real problem. A lightweight "in review by" marker would be the first fix.

---

## E. Product and Technical Scope

### E1. Dashboard is a stretch goal
- **Why:** Metrics are valuable to managers but don't improve the core loop. Without real volume, the numbers would come from seed data and prove little.
- **Tradeoff:** Time goes to the reviewer and marketer experience, which is what the judging criteria emphasize. Gives up a visible answer to "how do you know it's working?"
- **Revisit if:** Time remains after the core and polish are done.

### E2. Seed data loads on first visit, with a reset button
- **Why:** An empty system looks broken, and evaluators will open the URL and want to see it working in seconds. Seed data also demonstrates edge cases deliberately (see `seed-data.md`). With a shared database, reset affects everyone using the demo, so it should be clearly labeled.
- **Tradeoff:** Strong first impression and easy exploration. Gives up the empty-start experience, which is covered by explicit empty states and by reset.
- **Revisit if:** The product were used for real work, where seed data would be removed.

### E3. A small Python backend (FastAPI + Jinja + HTMX) with a SQLite database
- **Why:** The workflow logic (rules, immutable versions, decisions, diffs) is server-shaped, and a shared database makes the marketer-to-reviewer handoff real: a submission made on one device shows up in the reviewer's queue on another. Server-side records also make the audit trail more defensible than browser storage, which a user can edit. One codebase and one deploy keep it feasible in 24 hours.
- **Tradeoff:** Real shared state, a stronger audit trail, and no dependence on browser storage (works in a private window). Gives up a static-host deploy: it needs a small server host, and free tiers may cold-start on first load. The UI is server-rendered with light HTMX, so it is less richly interactive than a React app.
- **Revisit if:** Cold starts hurt the demo (consider a paid tier or keep-warm ping), or the UI needs more client-side interactivity than HTMX handles comfortably.
- **Note:** This replaces the earlier assumption of browser-only persistence. Shared state does not change A4: there is still no authentication, so "who did this" is a demo label.

### E6. The shared demo is capped
- **Why:** The demo database is public and shared, so a script could fill it. The server allows at most 300 submissions in total and 10 versions per submission, and refuses an identical resubmission by the same marketer (title, product, channel and copy) as a duplicate. Reset clears everything.
- **Tradeoff:** A demo that cannot be filled or flooded. Gives up unlimited use, which a real deployment would not want to cap this way.
- **Revisit if:** This becomes more than a demo.

### E4. Notifications and integrations are out of scope
- **Why:** Email and Slack alerts are real needs but are integrations, not product logic, and can't be demonstrated meaningfully without real accounts.
- **Tradeoff:** Saves time for the core. Gives up a major driver of real-world turnaround: reviewers and marketers still have to check the tool.
- **Revisit if:** Immediately after core scope. It would be the first roadmap item.

### E5. Optimized for a desktop laptop screen, reasonable on narrow widths
- **Why:** Reviewers do side-by-side work on a computer. Deep mobile optimization isn't where the value is.
- **Tradeoff:** The review screen can be laid out for comparison. Gives up polished mobile use, such as approving from a phone.
- **Revisit if:** Reviewers or managers need to approve on the go.

---

## Summary: the assumptions that matter most

If time in the presentation is short, lead with these:

1. **Flags assist, never decide** (C2): the safety stance for a compliance product.
2. **Two users, because rework starts with the submitter** (A2): the product insight.
3. **Text only, pattern-based flags** (B1, C3): the scoping tradeoff that buys quality and polish.
4. **Rules are illustrative, not legal advice** (C1): honesty about what the demo is.
5. **Small server + SQLite, no auth** (E3, A4): a real shared database for the handoff, with identity still a demo label.
6. **Immutable versions and decisions** (D2, D4): the basis of a trustworthy audit trail.