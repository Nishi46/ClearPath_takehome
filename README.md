# ClearPath_takehome

## Run locally

Requires Python 3.11 or newer.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. The database file is created on first start and loaded with 14 sample
submissions. Launch dates and timestamps are relative to the day you start the app, so the queue
always has one overdue item and a few rush ones.

**Reset demo** (header) restores the original sample data for everyone using the database. It asks
for confirmation first, and a second reset within 10 seconds is refused. To start completely fresh
locally, stop the app and delete `clearpath.db`.

**Flags.** Every asset is checked against seven illustrative rules (R1 to R7) when the demo is seeded
or reset. The queue's Flags column shows how many rules fired on the current version and the highest
severity (H, M or L). Flags assist a reviewer and never decide anything. The rules are illustrative,
not legal advice, and are deliberately simple keyword checks; what they miss is listed in
[documentation/rules-engine.md](documentation/rules-engine.md).

**Reviewing.** Open any row to see the copy, the flags and a decision form. A reviewer can approve, request
changes or reject; a reason is required for the last two. Once a version has a decision it is locked, and
the server refuses a second one even if the page is stale or the request is sent directly. Decisions are
recorded as one fixed demo reviewer, and the Reviewer/Marketer switch is a demo label, not a login.

**Dismissing flags, snippets and the audit trail.** Flags can be wrong, so a reviewer can dismiss one with a
required note (try R4 on #12, a phrase inside a sentence that explains the rule). A dismissal is permanent and
visible: it is listed with its note and reviewer, it removes the flag from the open list and the queue count,
and nothing can edit or delete it. It applies to the current version before a decision only, and the next
version starts with fresh flags. **Use snippet** on a flag card fills the comment box with that rule's ready-made
comment (try R2 on #14); the reviewer edits it and posts it, linked to the rule. Comments are allowed on locked
items too, such as an approved one. Every review page ends with an **Audit trail**: versions, dismissals,
comments and decisions in time order, each with who, what and when. Names are demo labels, so the trail shows
the shape of a real record, not proof of who acted. Marketer replies in comments are out of scope.

**Submitting and resubmitting.** A marketer submits from **Submit**, sees the flags in a pre-check before
sending (they never block), and follows their items on **My submissions**, where anything that needs action
comes first. After changes are requested or an item is rejected, the marketer edits and resubmits it as a new
version; the reviewer can read a word-level diff against the previous version (`?diff=1` on the review screen).
Rules the server enforces:
- A resubmission with the same copy as the previous version is blocked.
- Approved items are locked. A change after approval is out of scope.
- Only the marketer who submitted an item can resubmit it, and only its current version.
- The demo holds at most 300 submissions and 10 versions per submission, and an identical double submit is refused.

"Who I am" as a marketer is a demo label (Maya Chen, Jordan Lee or Sam Patel), not a login. Try Jordan Lee:
#14 (changes requested) and #8 (rejected) can be resubmitted, and #5 shows a v1 to v2 diff.

**Affiliate partners.** Switch Role to **Affiliate** to act as a partner (Northwind Referrals, BlueLeaf Media
or Summit Savers). Partners submit and resubmit their own assets directly, under the same rules as a marketer,
but only for the **Affiliate page** channel: the server sets the channel, whatever the form says. The queue
shows a **Partner** badge on their items and has a **Submitted by** filter (affiliate partners or internal
marketers). Like marketers, partners are demo labels, not logins. Try Northwind Referrals: switch to Reviewer,
request changes on #3, then switch back to Affiliate and resubmit it. BlueLeaf Media owns #12, and Summit
Savers owns nothing, so it shows the empty state.

Run the tests (from the project root, with the venv active):

```bash
pytest
```

`--reload` is for local development only. Never use it in a deployed start command.

## Environment

| Variable | Purpose | Default |
|---|---|---|
| `CLEARPATH_DB` | Path to the SQLite database file. Its folder must already exist and be writable. | `./clearpath.db` |

No secrets are required. The app has no API keys, passwords or tokens.
