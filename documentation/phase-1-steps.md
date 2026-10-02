# Phase 1: Skeleton and First Deploy, Implementation Steps

> Breaks phase 1 of build.md into the smallest steps. Every step has a test (automated unless marked manual) covering its edge cases and security. A step is done only when its tests pass. Stack: FastAPI + Jinja + HTMX + SQLite, Python 3.11 locally (3.11.8).

**Done when (phase):** public URL loads in incognito, schema creates twice without error, all tests below pass.

**Conventions**
- Tests live in `tests/`, run with `pytest`. Each test uses a temporary DB file via the `CLEARPATH_DB` env var, never the real `clearpath.db`.
- Dev-only packages (`pytest`, `httpx`) go in `requirements-dev.txt`, so the deploy installs only what it needs.
- Target Python 3.11.8 locally and on Render. Patched dependency versions need 3.10 or later (see dependency-audit.md).

---

## Step 1: Repo hygiene and environment

**Do**
1. Create `.gitignore`: `.venv/`, `__pycache__/`, `*.pyc`, `*.db`, `*.db-journal`, `*.db-wal`, `*.db-shm`, `.env`, `.pytest_cache/`, `.DS_Store`.
2. Create `.venv` with Python 3.11.8 (`~/.pyenv/versions/3.11.8/bin/python -m venv .venv`) and activate it.
3. Create `requirements.txt` (runtime) with exact pins: `fastapi`, `uvicorn`, `jinja2`, `python-multipart`. Create `requirements-dev.txt` (`-r requirements.txt`, `pytest`, `httpx`).
4. Install both, then run `pip freeze` and copy the resolved versions into the pins.

**Test**
- `git status` shows no `.venv`, `*.db` or `.env` files as untracked after creating dummy `test.db` and `.env` files, then delete the dummies.
- `pip install -r requirements.txt` succeeds in a fresh venv (proves the pins resolve on Python 3.11).
- `pip check` reports no broken dependencies.
- Security: every dependency has an exact version (`==`), none is unpinned; run `pip-audit` (or `pip list --outdated` as a fallback) and record any known-vulnerable pin before continuing.

---

## Step 2: Package layout and a bare app that starts

**Do**
1. Create `app/__init__.py`, `app/main.py` with `app = FastAPI(...)` and the docs UIs turned off in production: `docs_url=None`, `redoc_url=None`, `openapi_url=None`.
2. Create `tests/conftest.py` with a fixture that sets `CLEARPATH_DB` to a temp path and returns a `TestClient`.

**Test**
- App imports without side effects (no DB file created on import).
- `GET /docs`, `GET /redoc` and `GET /openapi.json` return 404 (security: no API surface disclosure).
- `GET /does-not-exist` returns 404 with a plain page, not a stack trace.
- Start with `uvicorn app.main:app` and confirm it binds and shuts down cleanly (manual).

---

## Step 3: Database helper

**Do**
1. Create `app/db.py` with `get_db_path()` reading `CLEARPATH_DB` (default `./clearpath.db`).
2. `connect()` opens `sqlite3` with `row_factory = sqlite3.Row`, runs `PRAGMA foreign_keys = ON`, and sets a busy timeout (5 seconds).
3. Enable `PRAGMA journal_mode = WAL` once at init, so reads do not block while writing.
4. Use a context manager so connections always close and commit or roll back.

**Test**
- `connect()` returns rows addressable by column name.
- `PRAGMA foreign_keys` returns 1 on every new connection.
- An exception inside the context manager rolls back (insert a row, raise, confirm the row is absent).
- Connections are closed after the context exits (using one afterwards raises `ProgrammingError`).
- Missing parent folder for the DB path produces a clear error, not a silent failure.
- Security: the DB path comes only from the environment, never from request data (grep confirms no request value reaches `get_db_path`).

---

## Step 4: Schema, part 1 (submission and version)

**Do**
1. Create `app/schema.sql` with `submission` and `version`, using `CREATE TABLE IF NOT EXISTS`.
2. `submission`: `id INTEGER PRIMARY KEY`, `title TEXT NOT NULL`, `product` and `channel` and `status` with `CHECK (... IN (...))`, `launch_date TEXT NOT NULL`, `submitted_by TEXT NOT NULL`, `created_at TEXT NOT NULL`, `current_version INTEGER NOT NULL`.
3. `version`: `submission_id` foreign key with `ON DELETE CASCADE`, `version_number INTEGER NOT NULL`, `copy TEXT NOT NULL`, `notes TEXT`, `created_at TEXT NOT NULL`, `UNIQUE(submission_id, version_number)`.
4. Add `CHECK (length(trim(title)) > 0)` and `CHECK (length(trim(copy)) > 0)` so empty or whitespace-only values are rejected at the database level as well as in the form later. SQLite's `trim()` strips only spaces, so trim `' ' || char(9) || char(10) || char(11) || char(12) || char(13)` instead (found by the tab/newline test); reuse this form for every non-blank CHECK in steps 5 and 6.
5. Give `version` its own `id INTEGER PRIMARY KEY` (added in step 5 so `flag` and `flag_dismissal` can reference `version_id`; the composite pair stays UNIQUE for step 6). Add indexes: `submission(status)`, `submission(launch_date)`, `version(submission_id)`.

**Test** (insert directly with SQL)
- A valid submission and version insert succeeds.
- Invalid `product`, `channel` or `status` is rejected by the CHECK.
- Empty and whitespace-only `title` and `copy` are rejected.
- A duplicate `(submission_id, version_number)` is rejected.
- A version pointing at a nonexistent submission is rejected (foreign keys on).
- Deleting a submission deletes its versions.
- Security: inserting a value such as `'; DROP TABLE submission; --` as a title stores it as literal text and the table survives (parameterized queries only; no string-built SQL anywhere in `app/`).

---

## Step 5: Schema, part 2 (flag and dismissal)

**Do**
1. Add `flag` (with an `id` primary key and an index on `version_id`): `version_id` foreign key to `version(id)` (cascade), `rule_id`, `severity` CHECK (`high|medium|low`), `kind` CHECK (`phrase|missing`), nullable `matched_text`, `start_index`, `end_index`.
2. Add a table-level CHECK tying the nullable fields to the kind: `phrase` rows must have all three set; `missing` rows must have all three null; and `start_index < end_index` for phrase rows.
3. Add `flag_dismissal`: `version_id` foreign key (cascade), `rule_id`, `note TEXT NOT NULL` with a non-blank CHECK, `dismissed_by`, `created_at`, `UNIQUE(version_id, rule_id)`.

**Test**
- A `phrase` flag with null indices is rejected; a `missing` flag with indices is rejected.
- `start_index >= end_index` is rejected for phrase flags.
- Invalid severity or kind is rejected.
- A dismissal with an empty or whitespace-only note is rejected.
- Dismissing the same rule twice on one version is rejected.
- Deleting a version deletes its flags and dismissals.

---

## Step 6: Schema, part 3 (decision and comment)

**Do**
1. Add `decision`: `submission_id` foreign key (cascade), `version_number`, `outcome` CHECK (`approved|changes_requested|rejected`), `reviewer`, `reason`, `created_at`, `UNIQUE(submission_id, version_number)` so a version can only be decided once at the database level.
2. Add a CHECK that `reason` is non-blank when the outcome is `changes_requested` or `rejected`.
3. Add `comment`: `submission_id` foreign key (cascade), `version_number`, `author`, `text` non-blank CHECK, nullable `rule_id`, `created_at`.
4. Add a composite foreign key from `decision` and `comment` to `version(submission_id, version_number)` so they cannot reference a version that does not exist (requires a `UNIQUE` on that pair, which step 4 already has).

**Test**
- Invalid outcome is rejected.
- A second decision on the same version is rejected (this backs the "cannot re-decide" rule even if the app code has a bug).
- `rejected` and `changes_requested` with an empty reason are rejected; `approved` with no reason is accepted.
- A comment on a nonexistent version is rejected.
- A comment with empty or whitespace-only text is rejected.
- A comment with a null `rule_id` is accepted.

---

## Step 7: Idempotent `init_schema()`

**Do**
1. In `db.py`, `init_schema()` reads `schema.sql` (path resolved relative to the module file, not the working directory) and runs it with `executescript`.
2. Call it from an app startup handler (FastAPI lifespan), not at import time.
3. Add `PRAGMA user_version = 1` at the end of the schema as a schema marker for later migrations.

**Test**
- Running `init_schema()` twice on one DB does not error and leaves data untouched (insert a row between the runs, confirm it survives).
- `sqlite_master` lists exactly the six tables and the expected indexes.
- Works when the process is started from a different working directory.
- A fresh temp DB (file does not exist yet) is created on first start.
- A read-only DB path produces a clear startup error rather than a half-started app.
- App startup through `TestClient` as a context manager creates the tables.

---

## Step 8: Health check

**Do**
1. Add `GET /healthz` in `app/routes/pages.py` that runs `SELECT 1` against the DB and returns `{"status": "ok"}` with 200, or 503 `{"status": "unavailable"}` if the DB check fails.
2. The response must not include versions, paths, exception text or environment details.

**Test**
- Returns 200 and exact body `{"status":"ok"}` on a healthy DB.
- With the DB path pointed somewhere unwritable, returns 503 and the body contains no path or exception text.
- `POST /healthz` returns 405.
- Responds with `Cache-Control: no-store`.

---

## Step 9: Templates and base layout

**Do**
1. Set up `Jinja2Templates` with autoescape on (verify it is on for `.html` templates).
2. Create `app/templates/base.html`: `<!doctype html>`, charset and viewport metas, a title block, header with the app name "ClearPath Review", the role switcher, a "Submit" link, a disabled "Reset demo" button, and a content block. Load HTMX from a pinned, trusted CDN URL with a Subresource Integrity (`integrity`) hash and `crossorigin`, or vendor it into `app/static/`.
3. Add a visible skip link and landmark roles (`header`, `main`, `nav`) so the page works with keyboard and screen readers.
4. Create `app/templates/queue.html` extending the base, with the placeholder text "Queue coming next".
5. Create `app/static/style.css` with color tokens, base typography and a header layout that works at laptop and narrow widths.

**Test**
- `GET /` returns 200 with `text/html` and contains the app name, the Submit link and the Reset demo button.
- Autoescape: render a template with `<script>alert(1)</script>` as a variable and confirm the output contains `&lt;script&gt;`.
- Static files are served (`GET /static/style.css` returns 200 with a CSS content type).
- Path traversal: `GET /static/../app/main.py` and `GET /static/%2e%2e/app/main.py` return 404, not source code.
- The HTMX script tag has an `integrity` attribute (or is served locally), so a CDN compromise cannot inject code.
- Status and role text is shown as text, not only color (check the markup contains the words).
- Manual: open in a browser with the console open; no errors or warnings. Resize to a narrow width; the header does not overflow horizontally.

---

## Step 10: Role switcher (cookie)

**Do**
1. Define `ROLES = ("reviewer", "marketer")` in one place.
2. Add a dependency `get_role(request)` that reads the `role` cookie and returns it only if it is in `ROLES`, else the default `reviewer`.
3. Add `POST /role` with a form field `role`. Reject unknown values with 400. On success set the cookie (`HttpOnly`, `SameSite=Lax`, `Secure` when served over HTTPS, `Path=/`, max age 30 days) and redirect with 303 to `/`.
4. Show the current role in the header and mark the active choice with `aria-current` and visible text.
5. Pass the role into every template through one shared context helper.

**Test**
- No cookie gives `reviewer`.
- A valid cookie `marketer` renders the marketer state in the header.
- A tampered cookie (`role=admin`, `role=`, a 5,000-character value, `role=<script>`, `role=reviewer; x`) falls back to `reviewer` and the page still returns 200 with no reflection of the bad value in the HTML.
- `POST /role` with `role=marketer` returns 303 and sets the cookie with `HttpOnly` and `SameSite=Lax` in the `Set-Cookie` header.
- `POST /role` with a missing, empty or unknown role returns 400 (or 422) and sets no cookie.
- `GET /role` returns 405 (state changes use POST only, so a link or image tag cannot switch the role).
- Switching persists across a second request that sends the cookie back.
- Security: the cookie is a demo label only (assumption A4). Add a code comment saying so and confirm nothing reads it as authorization yet.

---

## Step 11: Reset demo stub

**Do**
1. Render the "Reset demo" control as a disabled button with a title explaining it is coming in phase 2, and the warning copy that reset affects everyone using the demo.
2. Do not add a `/reset` route yet.

**Test**
- `GET /` shows the button with the `disabled` attribute and the warning text.
- `POST /reset` returns 404 or 405 (no accidental destructive endpoint exists before it is built with its guards in phase 2).

---

## Step 12: Security headers and error handling

**Do**
1. Add middleware that sets on every response: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, and a `Content-Security-Policy` that allows only `'self'` for scripts, styles and images, plus the HTMX source if it is on a CDN (inline styles are allowed only if the templates need them).
2. Add handlers so 404 and 500 return a friendly HTML page ("Page not found" and "Something went wrong") with a link back to the queue, with no stack trace or internal detail.
3. Make sure the app never runs with debug mode or `--reload` in the start command used for deploy.

**Test**
- Every route (`/`, `/healthz`, `/static/style.css`, a 404 path) carries all four headers.
- The CSP does not contain `unsafe-eval`, and does not use a wildcard `*` for scripts.
- A forced exception in a test-only route returns the friendly 500 page, with no traceback text, file paths or exception class names in the body.
- 404 page includes the link back to the queue.
- Security: a request with an oversized header or a 10 MB body to `/role` is rejected or limited without crashing the app (note: if the host or server enforces this, record that here).
- Manual: browser console shows no CSP violations while loading `/`.

**Result (oversized input):** the app caps request bodies at 1 MiB in `app/security.py` and answers 413, whether or not `Content-Length` is honest; a 10 MB body to `/role` is refused. Oversized headers are refused by uvicorn itself (h11 allows 64 KiB of headers, so a 300 KB header gets 400 or a dropped connection); a test starts a real uvicorn to check this and confirms the app still answers afterwards. On Render, the platform proxy also enforces its own header and body limits in front of the app.

---

## Step 13: README "Run locally" and a smoke script

**Do**
1. Add a "Run locally" section to `README.md`: create the venv, install, run `uvicorn app.main:app --reload`, open the local URL, run `pytest`.
2. Add a short "Environment" note listing `CLEARPATH_DB` and that no secrets are required.

**Test**
- Follow the README literally in a fresh clone in a new folder (manual): every command works with no extra steps.
- `pytest` passes from a clean checkout.
- `git grep -i -E "secret|password|token|api[_-]?key"` finds nothing sensitive in the repo.

---

## Step 14: Full local regression pass

**Do**
1. Run the whole test suite, then start the app by hand.

**Test**
- `pytest` fully green, including startup twice on one DB file.
- Manual walkthrough: load `/`, switch roles both ways, reload, hit `/healthz`, hit a missing page, check the console.
- `sqlite3 clearpath.db .tables` lists the six tables; `PRAGMA integrity_check` returns `ok`; `PRAGMA foreign_key_check` returns nothing.
- `pip check` clean again; `git status` clean of generated files.

---

## Step 15: Deploy to Render

**Do**
1. Commit, then push to the public GitHub repo (confirm with the user before pushing).
2. Create a Render web service from the repo: build command `pip install -r requirements.txt`; start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (no `--reload`); set `PYTHON_VERSION` to `3.11.8`; set `CLEARPATH_DB` to a writable path.
3. Add `render.yaml` only if useful for repeatable setup; otherwise record the settings in the README.

**Test**
- Build and deploy logs show no errors; the service reports healthy using `/healthz` as the health check path.
- Open the public URL in an incognito window: the page loads over HTTPS and shows the header.
- `GET /healthz` on the public URL returns 200 and `{"status":"ok"}`.
- `GET /docs` and `GET /openapi.json` on the public URL return 404.
- Response headers on the public URL include the security headers from step 12 (check with `curl -I`).
- Role cookie on the public URL includes the `Secure` flag.
- Restart the service from the Render dashboard: it comes back up with the schema created, no manual step.
- Open the URL on a second device (phone): layout holds and the page loads.
- Note the cold-start delay after idle and record it in the README as a known tradeoff.
- Security: confirm no environment values or secrets appear in the build logs or the repo, and the repo contains no `.env` or `.db` files.

---

## Exit checklist

- [ ] Public URL loads in incognito and on a second device
- [ ] Schema creates twice without error (automated test)
- [ ] All six tables exist with CHECK, foreign key and UNIQUE constraints verified by tests
- [ ] Role switcher survives tampered cookies; state changes are POST only
- [ ] Security headers present locally and on the deployed URL; docs endpoints return 404
- [ ] No secrets, DB files or `.env` committed
- [ ] README "Run locally" works from a fresh clone
- [ ] build.md status table updated: phase 1 done, phase 2 next
