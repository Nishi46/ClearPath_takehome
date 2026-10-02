import secrets
import threading
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app import clock, db, xlsx_import
from app.errors import error_response
from app.roles import SUBMITTING_ROLES, get_role, get_submitter
from app.routes.pages import NO_STORE, _single
from app.security import same_origin
from app.templating import render

router = APIRouter()

NOT_SUBMITTER_TEXT = ("Only marketers and affiliate partners can import submissions. "
                      "Switch role using the Role buttons above.")
EXPIRED_TEXT = ("That preview has expired, or it was already imported. Load the file again to see where "
                "things stand. Nothing was imported.")
PREVIEW_TTL = timedelta(minutes=10)
MAX_PENDING = 20


class PendingImports:
    """Previewed workbooks waiting for "Import". In memory and per process, like the reset cooldown.

    A token is single use, expires after PREVIEW_TTL and only works for the submitter who previewed.
    """

    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()

    def put(self, submitter, data, now):
        with self._lock:
            self._items = {t: v for t, v in self._items.items() if v[2] > now}
            while len(self._items) >= MAX_PENDING:
                self._items.pop(min(self._items, key=lambda t: self._items[t][2]))
            token = secrets.token_urlsafe(16)
            self._items[token] = (submitter, data, now + PREVIEW_TTL)
            return token

    def take(self, token, submitter, now):
        """The workbook bytes, or None if the token is unknown, expired, used or someone else's."""
        with self._lock:
            item = self._items.get(token) if isinstance(token, str) else None
            if item is None or item[0] != submitter or item[2] <= now:
                return None
            del self._items[token]
            return item[1]

    def clear(self):
        with self._lock:
            self._items.clear()


pending = PendingImports()


def _render_import(request, status_code=200, error=None, preview=None):
    response = render(request, "import.html", status_code=status_code, error=error, preview=preview,
                      max_rows=xlsx_import.MAX_ROWS)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/import")
def import_form(request: Request):
    return _render_import(request)


@router.get("/import/sample.xlsx")
def import_sample(request: Request):
    try:
        data = xlsx_import.read_sample()
    except xlsx_import.ImportFileError as exc:
        return error_response(request, 404, exc.message)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="clearpath-import-sample.xlsx"',
                             **NO_STORE})


def _summary(results):
    counts = {"ok": 0, "error": 0, "duplicate": 0}
    for r in results:
        counts[r["status"]] += 1
    return counts


@router.post("/import/preview")
async def import_preview(request: Request):
    # Guards in order: origin, role, then the source. Nothing is written here. The sample is a fixed path in the
    # repo; an upload is only ever read as bytes and never saved. The submitter never comes from the file.
    if not same_origin(request):
        return error_response(request, 403, "This must be started from this site. Reload the page and try again.")
    role = get_role(request)
    if role not in SUBMITTING_ROLES:
        return error_response(request, 403, NOT_SUBMITTER_TEXT)
    form = await request.form()
    try:
        if _single(form, "source") == "sample":
            data, name = xlsx_import.read_sample(), "the sample file"
        else:
            upload = form.get("file")
            if upload is None or isinstance(upload, str) or not getattr(upload, "filename", ""):
                raise xlsx_import.ImportFileError("empty")
            data, name = await upload.read(), "your file"
        force = xlsx_import.force_channel_for(role)
        rows = xlsx_import.parse_workbook(data, need_channel=force is None)
    except xlsx_import.ImportFileError as exc:
        return _render_import(request, status_code=422, error=exc.message)
    submitter = get_submitter(request)
    with db.connect() as conn:
        results = xlsx_import.preview_rows(conn, rows, clock.today(), submitter, force)
    counts = _summary(results)
    token = pending.put(submitter, data, clock.now()) if counts["ok"] else None
    return _render_import(request, preview={"results": results, "counts": counts, "token": token, "name": name})


@router.post("/import/confirm")
async def import_confirm(request: Request):
    # Re-reads and re-checks the previewed file: what was shown is never trusted. Each row is its own
    # transaction, so the import is not all-or-nothing and a partial result is reported, not hidden.
    if not same_origin(request):
        return error_response(request, 403, "This must be started from this site. Reload the page and try again.")
    role = get_role(request)
    if role not in SUBMITTING_ROLES:
        return error_response(request, 403, NOT_SUBMITTER_TEXT)
    form = await request.form()
    submitter = get_submitter(request)
    data = pending.take(_single(form, "token"), submitter, clock.now())
    if data is None:
        return _render_import(request, status_code=409, error=EXPIRED_TEXT)
    force = xlsx_import.force_channel_for(role)
    try:
        rows = xlsx_import.parse_workbook(data, need_channel=force is None)
    except xlsx_import.ImportFileError as exc:
        return _render_import(request, status_code=422, error=exc.message)
    with db.connect() as conn:
        summary = xlsx_import.import_rows(conn, rows, submitter, clock.now(), force)
    query = "imported=%d&skipped=%d&failed=%d" % (summary["created"], summary["skipped"], summary["failed"])
    if summary["stopped"]:
        query += "&full=1"
    return RedirectResponse("/mine?" + query, status_code=303, headers=NO_STORE)
