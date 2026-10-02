# ClearPath Compliance Review: Rules Engine

> How the flags are produced and where they are blind. Rules are illustrative, not legal advice. Flags assist a reviewer; they never approve, reject or block anything.

## How it works

- **Input:** product, channel and the asset copy. **Output:** a list of flags, ordered by rule id and then position in the copy.
- **Phrase rules (R1, R4, R6)** highlight the matched text. Matching ignores case and punctuation, accepts curly quotes and a closed-up form ("preapproved", "youre approved"), allows up to three punctuation or space characters between words, and cannot be hidden by zero-width characters or a soft hyphen. A phrase must start and end on a word boundary ("act now" does not match "react now").
- **Missing-text rules (R2, R3, R5, R7)** flag an absence, so there is nothing to highlight; the review screen shows a "Missing: add this" card. R2 only applies once a percentage appears within 60 characters of "rate" or "interest" in the same paragraph.
- **R6** is not reported when the copy states an end date: an end word (ends, expires, through, until, thru) followed within 20 characters by a month and day or a numeric date.
- **R5** accepts "subject to credit approval", "subject to credit review", "credit approval required" and "credit approval is required". **R7** accepts "terms apply", "terms and conditions apply", "see full terms", or a link with a scheme or a path (a bare domain does not count).
- **Scope:** each rule lists the products and channels it applies to; R3 never fires on a loan, R7 never on an email.
- **Counted per rule, shown per occurrence:** every occurrence of a phrase is a separate flag (so each is highlighted), but the queue counts distinct rules. Dismissed rules are not counted.
- **Computed, not stored in the seed:** flags are produced by the engine when the demo is seeded or reset (and, in phase 5, when a version is submitted), so seed data and live behavior cannot drift.
- **Fails loudly:** an unknown product or channel, an unreadable rules file, or a rule that errors raises instead of returning "no flags", because a silent clean result would be a false assurance. A bad rules file stops startup with a logged message.

## Known limits (stated openly, not bugs)

| Limit | Example | Why accepted |
|---|---|---|
| Misses paraphrased violations (recall) | Seed #8 "everyone gets a yes" fires no rule | Transparent keyword rules trade recall for explainability; the reviewer is the safety net. Asserted in `tests/test_rules_seed.py` so a change is a conscious decision |
| Close variants outside the phrase list are missed | "guaranteed approvals" (plural), "approval guaranteed", "guarantee approval", "everyone's approved", "cannot be denied" / "can not be denied", "you are approved" | The rules match the exact phrases in `data/rules.json` (the spec in seed-data.md). Each is a one-line addition to the file if wanted |
| Reads words, not intent (false positives) | Seed #13 explains why "guaranteed approval" is not offered, and still fires R1 | That is the dismissal demo; a reviewer dismisses it with a note |
| Negation not understood | "Not subject to credit approval" counts as the R5 disclaimer | Same: words, not intent |
| Month abbreviation counts as APR | "Offer ends Apr 3" satisfies R2 | Whole-word "APR" is matched; telling the month from the rate needs context |
| Nearby unrelated date can suppress R6 | "Offer expires soon. Posted Oct 31" counts as a stated end date | The 20-character window is a deliberate approximation |
| Month and day are not checked against a calendar | "ends Feb 31" suppresses R6 | A real date check adds little for a keyword assistant |
| Plural "Equal Housing Lenders" does not satisfy R3 | | Phrases end on a word boundary, consistent across rules |
| Copy over 100,000 characters is refused | | Backstop; the submit form will enforce a much smaller limit |
| Rules are unversioned | A stored flag keeps the severity it was raised with; if a rule is edited or removed, old flags still render (a removed rule shows "Rule no longer available") | Fine for a demo; a real system would version rules |
