from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from app import clock, db, mine as mine_view, review, submit
from app.queue import FILTER_OPTIONS
from app.roles import COOKIE_MAX_AGE, MARKETER_COOKIE_NAME, MARKETERS, get_marketer, get_role
from app.routes.pages import NO_STORE, _is_https, _single
from app.security import same_origin
from app.templating import render

router = APIRouter()

FIELDS = ("title", "product", "channel", "launch_date", "copy", "notes")

SUBMIT_ERROR_STATUS = {"duplicate": 409, "capacity": 503, "bad_marketer": 400}
SUBMIT_ERROR_TEXT = {
    "capacity": "The demo is full right now. Ask whoever runs it to reset the demo, then try again.",
    "bad_marketer": "Choose who you are submitting as, then try again.",
}


@router.post("/marketer")
async def set_marketer(request: Request):
    # Like /role, this is a demo label, not a login. The value is only ever compared with the
    # allowlist and never echoed back.
    if not same_origin(request):
        return PlainTextResponse("This must be started from this site.", status_code=403, headers=NO_STORE)
    form = await request.form()
    name = _single(form, "name")
    if name not in MARKETERS:
        return PlainTextResponse("Unknown marketer.", status_code=400, headers=NO_STORE)
    response = RedirectResponse("/mine", status_code=303, headers=NO_STORE)
    response.set_cookie(MARKETER_COOKIE_NAME, name, max_age=COOKIE_MAX_AGE, path="/",
                        httponly=True, samesite="lax", secure=_is_https(request))
    return response


@router.get("/mine")
def mine(request: Request):
    # The success banner is built from the database, never from the query value, and only for the
    # current marketer's own submission. The list is whichever marketer is selected, shown to either
    # role; only the marketer role gets the resubmit actions (a product guard, not authorization).
    banner = None
    values = request.query_params.getlist("submitted")
    sid = review.parse_id(values[0]) if len(values) == 1 else None
    if sid is not None:
        with db.connect() as conn:
            row = conn.execute("SELECT id, title, launch_date, submitted_by FROM submission WHERE id = ?",
                               (sid,)).fetchone()
        if row is not None and row["submitted_by"] == get_marketer(request):
            today = clock.today()
            warnings = []
            try:
                launch = clock.date.fromisoformat(row["launch_date"])
                warnings = [submit.warning_text(c, row["launch_date"], today)
                            for c in submit.launch_warnings(launch, today)]
            except ValueError:
                pass
            banner = {"id": row["id"], "title": row["title"], "warnings": warnings}
    today = clock.today()
    with db.connect() as conn:
        rows = mine_view.list_mine(conn, get_marketer(request), today)
    response = render(request, "mine.html", banner=banner, groups=mine_view.group_mine(rows), total=len(rows))
    response.headers["Cache-Control"] = "no-store"
    return response


def _values(form):
    """The raw text of each submit field for showing again; anything missing or repeated is blank."""
    return {name: (_single(form, name) or "") for name in FIELDS}


def _warning_texts(warnings, values, today):
    return [submit.warning_text(code, values["launch_date"], today) for code in warnings]


def _render_form(request, values, errors=None, warnings=(), check=None, banner=None, status_code=200):
    today = clock.today()
    response = render(
        request, "submit.html", status_code=status_code, values=values, errors=errors or {},
        warnings=_warning_texts(warnings, values, today), check=check, banner=banner,
        product_options=FILTER_OPTIONS["product"], channel_options=FILTER_OPTIONS["channel"],
        copy_count="{:,}".format(len(values["copy"])), max_copy="{:,}".format(submit.MAX_COPY_CHARS),
        max_title=submit.MAX_TITLE_CHARS, max_notes=submit.MAX_NOTES_CHARS)
    response.headers["Cache-Control"] = "no-store"  # the form depends on the role and the date
    return response


@router.get("/submit")
def submit_form(request: Request):
    return _render_form(request, {name: "" for name in FIELDS})


@router.post("/submit")
async def submit_post(request: Request):
    # The guards run in this order: origin, role, form, validation, then create_submission, which
    # holds all the real rules. submitted_by, status, version and timestamps are never read from the form.
    if not same_origin(request):
        return PlainTextResponse("Submissions must be made from this site.", status_code=403, headers=NO_STORE)
    # The role is a demo label (assumption A4): this is a product guard, not authorization.
    if get_role(request) != "marketer":
        return PlainTextResponse("Only marketers can submit.", status_code=403, headers=NO_STORE)
    form = await request.form()
    raw = {name: _single(form, name) for name in FIELDS}
    values = {name: raw[name] or "" for name in FIELDS}
    today = clock.today()
    clean, errors, warnings = submit.validate_submission(raw, today)
    if errors:
        return _render_form(request, values, errors, warnings, status_code=422)
    try:
        with db.connect() as conn:
            sid = submit.create_submission(conn, clean, get_marketer(request), clock.now())
    except submit.SubmitError as exc:
        banner = {"text": SUBMIT_ERROR_TEXT.get(exc.code), "existing_id": exc.existing_id}
        if exc.code == "duplicate":
            banner["text"] = "You already submitted this."
        return _render_form(request, values, {}, warnings, banner=banner,
                            status_code=SUBMIT_ERROR_STATUS[exc.code])
    return RedirectResponse("/mine?submitted=%d" % sid, status_code=303, headers=NO_STORE)


@router.post("/submit/check")
async def submit_check(request: Request):
    # Read-only: this route never opens the database. It shows what the rules say about the draft.
    if not same_origin(request):
        return PlainTextResponse("This must be started from this site.", status_code=403, headers=NO_STORE)
    if get_role(request) != "marketer":
        return PlainTextResponse("Only marketers can submit.", status_code=403, headers=NO_STORE)
    form = await request.form()
    raw = {name: _single(form, name) for name in FIELDS}
    values = {name: raw[name] or "" for name in FIELDS}
    today = clock.today()
    _, errors, warnings = submit.validate_submission(raw, today)
    check = submit.precheck(raw["product"], raw["channel"], raw["copy"])
    if request.headers.get("hx-request") == "true":
        response = render(request, "_precheck_update.html", check=check,
                          warnings=_warning_texts(warnings, values, today))
        response.headers["Cache-Control"] = "no-store"
        return response
    return _render_form(request, values, errors, warnings, check=check)
