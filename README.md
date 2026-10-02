# ClearPath Review

**Live demo: https://clearpath-review.onrender.com**

A review queue for marketing compliance. It makes each review faster and each submission better, so a
compliance team can clear more assets per reviewer without lowering the bar.

> ClearPath Financial is a fictional online lender (personal loans, credit cards, mortgage prequalification).
> The rules, names and data are illustrative. **The rules are not legal advice and not a real policy.**

The demo runs on Render's free tier. The first request after a quiet spell can take about a minute while the
service wakes up. Its database is wiped on every restart or deploy and re-seeded on startup, so the demo always
opens in a known state.

## The problem and the thesis

Every marketing asset must pass compliance review before it goes live. Today that review runs on Excel and
email, and it is the bottleneck on growth: incomplete submissions, feedback scattered across threads,
resubmissions that must be re-read in full, no prioritization, the same comment retyped dozens of times, and an
audit trail rebuilt from an inbox.

Throughput comes from two levers: **reviewer time per item** and **review rounds per item**. Each feature
targets one of them:

| Pain point | What the product does |
|---|---|
| Incomplete submissions | Structured form with required fields and inline errors that keep your input |
| Feedback in email | Decisions and comments live on the submission, per version, and show on **My submissions** |
| Re-reading resubmissions | Every resubmit is a new version with a word-level diff against the previous one |
| No prioritization | Queue sorts by launch date; overdue and rush items stand out; filters by status, product, channel, submitter |
| Repeated comments | One-click snippets tied to each rule |
| Rework from preventable issues | A pre-check shows flags to the marketer before they submit |
| Weak audit trail | Versions, flags dismissed, comments and decisions are timestamped, append-only, and approved versions are locked |

**Flags assist, they never decide.** The seven rules are transparent keyword checks. They point a reviewer at
likely problems in context; a human makes every approve, request-changes or reject call. The speed-up comes from
better-prepared reviews, not skipped ones.

## Try this (about 3 minutes)

The demo opens as a **Reviewer** on a populated queue. Launch dates are relative to today, so one item is always
overdue and a few are rush.

1. **Triage.** The queue is sorted by launch date. #11 is overdue and #1 and #2 are rush. Try the filters
   (for example Status = Rejected and Product = Card shows the zero-results state).
2. **Open #1** (personal loan holiday email). Flagged phrases are highlighted in the copy. Missing disclosures show
   as "Missing: add this" cards. Each flag gives its rule, severity and a plain explanation.
3. **Use a snippet.** On the R2 card click **Use snippet**, edit the comment, post it. Then **Request changes**
   with a reason (a reason is required for request changes and reject).
4. **Dismiss a false positive.** Open #12 and dismiss the R4 flag (an explanatory use of "pre-approved") with a
   note. It stays visible with the note and your name, and it drops out of the count. #13 shows one already dismissed.
5. **Switch Role to Marketer** and pick Jordan Lee. **My submissions** puts items needing action first. Edit and
   resubmit #14. Try submitting it unchanged to see it blocked.
6. **Back as Reviewer**, open #5 and click the diff toggle for the v1 to v2 changes. Every review page ends with an
   **Audit trail**.
7. **Affiliate partners.** Switch Role to Affiliate (Northwind Referrals). Resubmit #3 after requesting changes.
8. **Import.** As a marketer or partner open **Import**, preview the sample sheet, and import the ready rows.
9. **Reset demo** (header) restores the exact seed for everyone.

Flag coverage worth knowing: R1 to R7 each fire on a seed item; #10 and #11 are near-misses that must not fire;
#13 is a deliberate false positive; #8 contains a violation no rule catches ("everyone gets a yes").

## Scope

**In:** structured submission with validation and a pre-check; reviewer queue with urgency and filters; review
screen with approve, request changes and reject; versioned resubmission with diff; seven scoped rules with
inline highlights and "missing" cards; flag dismissal with a required note; rule-linked comment snippets;
audit trail; My submissions; affiliate partner role; Excel import; seed data and reset; light and dark theme.

**Out, on purpose:** login and real permissions, notifications and integrations (email, Slack, Jira), image or
video review, a real legal rules engine, multi-reviewer assignment and SLAs, campaign-level review, a metrics
dashboard (the stretch item that was cut), title search on the queue, marketer replies inside comments.
The reasoning is in [documentation/scope.md](documentation/scope.md).

## Assumptions and shortcuts, stated openly

Full list with tradeoffs: [documentation/assumptions.md](documentation/assumptions.md).

- **No authentication.** Role (Reviewer, Marketer, Affiliate) and "who I am" are demo labels in cookies, not
  logins. All decisions are attributed to one fixed demo reviewer (Alex Rivera). The audit trail shows the
  *shape* of a real record, not proof of who acted.
- **One shared SQLite database.** Everyone using the demo sees the same data, and anyone can press Reset.
  Reset needs confirmation, and a second reset within 10 seconds is refused.
- **Text only, one asset per submission.** Disclosure placement and image content are invisible to the checks.
- **Rules are keyword matches**, trading recall for explainability. What they miss, and why, is listed in
  [documentation/rules-engine.md](documentation/rules-engine.md).
- **Launch date is the urgency signal**, and a rush date warns rather than blocks.
- **Excel import is not all-or-nothing.** Rows are saved one at a time and a partial result is reported. Uploaded
  files are held in memory for 10 minutes between preview and import and never saved.

## How the product works

**Rules R1 to R7**

| ID | Rule | Applies to | Severity | Kind |
|---|---|---|---|---|
| R1 | No guaranteed-approval claims | All | High | phrase |
| R2 | Rate shown requires APR | Loan, card, mortgage | High | missing |
| R3 | Equal Housing Lender statement | Mortgage | High | missing |
| R4 | "Prequalified" must not imply final approval | Mortgage, loan | Medium | phrase |
| R5 | Credit approval disclaimer | Loan, card | Medium | missing |
| R6 | No pressure or false urgency (unless an end date is stated) | All | Low | phrase |
| R7 | Disclosure reachable in short formats | Paid social, display | Medium | missing |

Rules live in [data/rules.json](data/rules.json). Flags are computed by the engine when an item is seeded or
submitted, so seed data and live behavior cannot drift. Severity and status are always shown as text as well as
color.

**Rules the server enforces**
- A decision needs a reason for request changes and reject. A version with a decision is locked, and a second
  decision is refused even from a stale page, a double click or a direct request.
- Approved items are locked. Comments are still allowed.
- A resubmission with identical copy is blocked. Only the submitter can resubmit, and only the current version.
- Partners can only submit for the Affiliate page channel; the server sets it whatever the form says.
- Demo limits: 300 submissions, 10 versions per submission; an identical double submit is refused.

**Importing from Excel.** Columns: Title, Product, Channel, Launch date, Copy, optional Notes (first sheet,
up to 200 rows, under 1 MB, no macros). Preview shows each row as *Ready*, *Needs fixing* or *Already exists*
and writes nothing. Import then creates only the ready rows through the same checks as the form. The sheet
cannot set the submitter. The sample [data/import-sample.xlsx](data/import-sample.xlsx) has dates counted from
when it was generated; regenerate it with `python -m tests.tools.make_import_sample` if it is stale.

## Navigating the repo

```
app/
  main.py            FastAPI app, startup (schema + seed), middleware, error handlers
  routes/
    pages.py         queue, review screen, decisions, dismissals, comments, role, reset, /healthz
    submit_pages.py  submit, pre-check, resubmit, My submissions, marketer/affiliate identity
    import_pages.py  Excel import: page, preview, confirm, sample download
  rules.py           rules engine (phrase and missing-text checks)
  flags.py           flag storage and dismissal state
  review.py          review-screen view model and decision/comment/dismiss logic
  submit.py          submission and version creation, validation, limits
  queue.py           queue query, filters, urgency
  mine.py            My submissions view
  diff.py            word-level version diff
  audit.py           audit trail
  xlsx_import.py     workbook parsing, row checks, import
  seed.py, clock.py  seed loading and relative dates
  roles.py           demo roles and identities (not auth)
  security.py        security headers, request size limit, same-origin checks
  db.py, schema.sql  SQLite connection and schema
  templates/         Jinja pages and HTMX partials
  static/            CSS, small per-page JS, theme toggle, vendored htmx
data/                rules.json, seed.json, import-sample.xlsx
documentation/       scope, assumptions, screens, rules engine, seed data, build log, UI notes
tests/               pytest suite (unit, route, edge-case and polish sweeps)
render.yaml          Render deployment blueprint
```

Stack: Python 3.11, FastAPI, Jinja2 server-rendered pages, HTMX for the pre-check, SQLite, openpyxl for import.
No build step and no front-end framework.

Where to read more:

| Document | What it covers |
|---|---|
| [documentation/scope.md](documentation/scope.md) | Problem, users, scope, cut order, data model |
| [documentation/assumptions.md](documentation/assumptions.md) | Every assumption with tradeoff and "revisit if" |
| [documentation/screens.md](documentation/screens.md) | Screen-by-screen behavior |
| [documentation/rules-engine.md](documentation/rules-engine.md) | How flags are produced and their known limits |
| [documentation/seed-data.md](documentation/seed-data.md) | The 14 seed items and what each one demonstrates |
| [documentation/build.md](documentation/build.md) | Build plan and the phase-by-phase steps files |
| [documentation/dependency-audit.md](documentation/dependency-audit.md) | Pinned dependencies and audit results |

## What's next

This was built in 24 hours, so several things were cut on purpose. These three would be built first, in this
order, because each one removes a limit the demo currently states openly.

1. **Metrics dashboard.** Median time to decision, first-pass approval rate, backlog by status, and top flagged
   rules. The data is already captured (versions, decisions and timestamps), so this is mostly queries and one
   screen. It is what a compliance manager needs to see whether the two throughput levers (time per item and
   rounds per item) are moving, and the "top flagged rules" view shows which rules to tighten or retire.
2. **Real identity and roles.** Replace the cookie labels with login, so "who approved this" in the audit trail
   is a verified fact, a marketer cannot act as a reviewer, and partners only see their own items. This comes
   before anything involving real data, because the audit record is not defensible without it.
3. **Notifications and queue search.** Email or Slack alerts when changes are requested, an item is decided, or
   one is about to go overdue, plus title search on the queue. Marketers stop asking "where is my asset?" and
   reviewers stop missing urgent items.

After those: image and layout review so disclosure placement can be checked, versioned rules with an optional
LLM second pass for paraphrased violations (still assistive only), routing by product, and campaign-level review.

## Run locally

Requires Python 3.11 or newer.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. The database file is created on first start and loaded with 14 sample submissions.
To start completely fresh, stop the app and delete `clearpath.db`.

Run the tests from the project root with the venv active:

```bash
pytest
```

`--reload` is for local development only. Never use it in a deployed start command.

## Deploying

[render.yaml](render.yaml) defines the Render web service: build `pip install -r requirements.txt`, start
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/healthz`, Python 3.11.8. The free tier has no
persistent disk, so the database lives in `/tmp` and resets on restart.

| Variable | Purpose | Default |
|---|---|---|
| `CLEARPATH_DB` | Path to the SQLite file. Its folder must exist and be writable. | `./clearpath.db` |

No secrets are required. The app has no API keys, passwords or tokens.
