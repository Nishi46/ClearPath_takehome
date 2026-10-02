from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.roles import ROLES, get_role

APP_DIR = Path(__file__).resolve().parent

# Starlette's Jinja2Templates turns autoescape on; a test in tests/test_pages.py guards that.
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def render(request: Request, name: str, status_code: int = 200, **context):
    """Render a template with the shared context (current role) every page needs."""
    context["role"] = get_role(request)
    context["roles"] = ROLES
    return templates.TemplateResponse(request, name, context, status_code=status_code)
