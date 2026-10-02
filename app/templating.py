from pathlib import Path

from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent

# Starlette's Jinja2Templates turns autoescape on; a test in tests/test_pages.py guards that.
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
