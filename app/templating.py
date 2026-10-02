import hashlib
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


_STATIC = APP_DIR / "static"
_asset_versions = {}


def asset_url(name):
    """URL of a static file with a short content hash, e.g. /static/style.css?v=1a2b3c4d5e.

    The URL changes whenever the file's contents change, so a browser can never keep showing an
    old stylesheet. `name` comes from templates only, and must be a plain file name in static/.
    """
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("asset name must be a plain file name")
    path = _STATIC / name
    stat = path.stat()
    key = (name, stat.st_mtime_ns, stat.st_size)
    version = _asset_versions.get(key)
    if version is None:
        version = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
        _asset_versions[key] = version
    return "/static/%s?v=%s" % (name, version)


templates.env.globals["asset_url"] = asset_url
