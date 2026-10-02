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

## Deploy (Render)

Settings live in [render.yaml](render.yaml). To set up by hand, create a Web Service from this repo with:

| Setting | Value |
|---|---|
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/healthz` |
| `PYTHON_VERSION` | `3.11.8` |
| `CLEARPATH_DB` | `/tmp/clearpath.db` |

Never add `--reload` to the start command.

### Known tradeoffs on the free tier

- **Cold start.** After about 15 minutes idle, Render stops the service. The next visit takes roughly 30 to 60 seconds to load. Open the URL a minute before a demo, or point a free uptime monitor (such as UptimeRobot) at `/healthz` every 5 to 10 minutes to keep it awake. Measured delay: _to be recorded after the first deploy_.
- **Data is not permanent.** The SQLite file lives on a disk that is wiped on every restart or redeploy. The schema is recreated automatically at startup, and from phase 2 the seed data is reloaded, so the demo always starts in a known state. Anything entered during a session is lost on restart.
