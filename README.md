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
