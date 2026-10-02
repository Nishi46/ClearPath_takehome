# ClearPath_takehome

## Run locally

Requires Python 3.11 or newer.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. The database file is created on first start.

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
