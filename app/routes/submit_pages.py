from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import clock, db, mine as mine_view, review, submit
from app.errors import error_response
from app.queue import FILTER_OPTIONS
from app.roles import (AFFILIATE_CHANNEL, AFFILIATE_COOKIE_NAME, AFFILIATES, COOKIE_MAX_AGE, MARKETER_COOKIE_NAME,
                       MARKETERS, SUBMITTING_ROLES, get_role, get_submitter)
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
        return error_response(request, 403, "This must be started from this site. Reload the page and try again.")
    form = await request.form()
    name = _single(form, "name")
    if name not in MARKETERS:
        return error_response(request, 400, "Unknown marketer. Choose one from the list on My submissions.")
    response = RedirectResponse("/mine", status_code=303, headers=NO_STORE)
    response.set_cookie(MARKETER_COOKIE_NAME, name, max_age=COOKIE_MAX_AGE, path="/",
                        httponly=True, samesite="lax", secure=_is_https(request))
    return response


@router.post("/affiliate")
async def set_affiliate(request: Request):
    # Same as /marketer: a demo label compared with the allowlist, never echoed back.
    if not same_origin(request):
        return error_response(request, 403, "This must be started from this site. Reload the page and try again.")
    form = await request.form()
    name = _single(form, "name")
    if name not in AFFILIATES:
        return error_response(request, 400, "Unknown affiliate partner. Choose one from the list on My submissions.")
    response = RedirectResponse("/mine", status_code=303, headers=NO_STORE)
    response.set_cookie(AFFILIATE_COOKIE_NAME, name, max_age=COOKIE_MAX_AGE, path="/",
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
            row = conn.execute("SELECT id, title, launch_date, submitted_by, current_version FROM submission"
                               " WHERE id = ?",
                               (sid,)).fetchone()
        if row is not None and row["submitted_by"] == get_submitter(request):
            today = clock.today()
            warnings = []
            try:
                launch = clock.date.fromisoformat(row["launch_date"])
                warnings = [submit.warning_text(c, row["launch_date"], today)
                            for c in submit.launch_warnings(launch, today)]
            except ValueError:
                pass
            banner = {"id": row["id"], "title": row["title"], "warnings": warnings,
                      "resubmitted_as": row["current_version"] if row["current_version"] > 1 else None}
    today = clock.today()
    with db.connect() as conn:
        rows = mine_view.list_mine(conn, get_submitter(request), today)
    response = render(request, "mine.html", banner=banner, imported=_import_banner(request), groups=mine_view.group_mine(rows), total=len(rows))
    response.headers["Cache-Control"] = "no-store"
    return response


def _import_banner(request):
    """Counts from an Excel import, shown once on My submissions. They are whole numbers clamped to a
    sane range and only ever printed as numbers, so a hand-made link can say nothing but a count."""
    params = request.query_params
    if len(params.getlist("imported")) != 1:
        return None

    def count(name):
        values = params.getlist(name)
        text = values[0] if len(values) == 1 else "0"
        return min(int(text), submit.MAX_SUBMISSIONS) if text.isdigit() and len(text) <= 4 else 0

    if not params["imported"].isdigit() or len(params["imported"]) > 4:
        return None
    return {"created": min(int(params["imported"]), submit.MAX_SUBMISSIONS), "skipped": count("skipped"),
            "failed": count("failed"), "full": params.getlist("full") == ["1"]}


def _read_fields(request, form):
    """Raw and display values of the submit fields. A partner's channel is always affiliate_page,
    whatever the form says: the server decides it, not the browser."""
    raw = {name: _single(form, name) for name in FIELDS}
    if get_role(request) == "affiliate":
        raw["channel"] = AFFILIATE_CHANNEL
    return raw, {name: raw[name] or "" for name in FIELDS}


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
    values = {name: "" for name in FIELDS}
    if get_role(request) == "affiliate":
        values["channel"] = AFFILIATE_CHANNEL
    return _render_form(request, values)


@router.post("/submit")
async def submit_post(request: Request):
    # The guards run in this order: origin, role, form, validation, then create_submission, which
    # holds all the real rules. submitted_by, status, version and timestamps are never read from the form.
    if not same_origin(request):
        return error_response(request, 403, "Submissions must be made from this site. Reload the page and try again.")
    # The role is a demo label (assumption A4): this is a product guard, not authorization.
    if get_role(request) not in SUBMITTING_ROLES:
        return error_response(request, 403, "Only marketers and affiliate partners can submit. Switch role using the Role buttons above.")
    form = await request.form()
    raw, values = _read_fields(request, form)
    today = clock.today()
    clean, errors, warnings = submit.validate_submission(raw, today)
    if errors:
        return _render_form(request, values, errors, warnings, status_code=422)
    try:
        with db.connect() as conn:
            sid = submit.create_submission(conn, clean, get_submitter(request), clock.now())
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
        return error_response(request, 403, "This must be started from this site. Reload the page and try again.")
    if get_role(request) not in SUBMITTING_ROLES:
        return error_response(request, 403, "Only marketers and affiliate partners can submit. Switch role using the Role buttons above.")
    form = await request.form()
    raw, values = _read_fields(request, form)
    today = clock.today()
    _, errors, warnings = submit.validate_submission(raw, today)
    check = submit.precheck(raw["product"], raw["channel"], raw["copy"])
    if request.headers.get("hx-request") == "true":
        response = render(request, "_precheck_update.html", check=check,
                          warnings=_warning_texts(warnings, values, today))
        response.headers["Cache-Control"] = "no-store"
        return response
    return _render_form(request, values, errors, warnings, check=check)


# ---- resubmit ----

RESUBMIT_FIELDS = ("copy", "notes", "launch_date")
RESUBMIT_ERROR_STATUS = {"not_found": 404, "not_owner": 403, "stale_version": 409, "not_resubmittable": 409,
                         "unchanged": 422, "capacity": 409}
RESUBMIT_ERROR_TEXT = {
    "stale_version": "This item changed since you opened the form (another tab or a reviewer got there first). "
                     "This page now shows where it stands. Nothing was saved.",
    "not_resubmittable": "This item can't be resubmitted right now. Nothing was saved.",
    "capacity": "This item has reached the limit of %d versions for the demo. Nothing was saved." % submit.MAX_VERSIONS,
}
UNCHANGED_TEXT = ("Nothing has changed in the copy. Edit it to address the reviewer's feedback, or leave it as is "
                  "and reply to your reviewer.")


def _load(sid):
    with db.connect() as conn:
        return review.load_review(conn, sid)


def _render_resubmit(request, data, values=None, errors=None, warnings=(), banner=None, status_code=200,
                     show_diff=False):
    """The resubmit page: the form when this marketer may resubmit, else a sentence saying why (no form)."""
    sub, version = data["submission"], data["version"]
    block = submit.resubmit_block(data, get_submitter(request), get_role(request))
    if values is None:
        values = {"copy": version["copy"], "notes": version["notes"] or "", "launch_date": sub["launch_date"]}
    today = clock.today()
    response = render(
        request, "resubmit.html", status_code=status_code, sub=sub, block=block, values=values,
        errors=errors or {}, warnings=_warning_texts(warnings, {"launch_date": values["launch_date"]}, today),
        banner=banner,
        compare=submit.compare_copy(version["copy"], values["copy"]) if show_diff and block is None else None, feedback=review.feedback_view(data), base_version=sub["current_version"],
        check=submit.precheck(sub["product"], sub["channel"], values["copy"]) if block is None else None,
        copy_count="{:,}".format(len(values["copy"])), max_copy="{:,}".format(submit.MAX_COPY_CHARS),
        max_notes=submit.MAX_NOTES_CHARS,
        product_text=dict(FILTER_OPTIONS["product"]).get(sub["product"], sub["product"]),
        channel_text=dict(FILTER_OPTIONS["channel"]).get(sub["channel"], sub["channel"]))
    response.headers["Cache-Control"] = "no-store"  # Back must not show a stale form
    return response


@router.get("/resubmit/{submission_id}")
def resubmit_form(request: Request, submission_id: str):
    # A plain str id so bad input gets our 404 page, not FastAPI's JSON 422; unknown and unparseable look the same.
    sid = review.parse_id(submission_id)
    data = _load(sid) if sid is not None else None
    if data is None:
        raise HTTPException(status_code=404)
    return _render_resubmit(request, data)


@router.post("/resubmit/{submission_id}")
async def resubmit_post(request: Request, submission_id: str):
    # Guards in order: origin, id, role, existence and owner, form, validation, then create_version, which
    # holds the real rules. Title, product, channel, submitter, status and version numbers never come from the form.
    if not same_origin(request):
        return error_response(request, 403, "Resubmissions must be made from this site. Reload the page and try again.")
    sid = review.parse_id(submission_id)
    if sid is None:
        raise HTTPException(status_code=404)
    # The role and marketer are demo labels (assumption A4): product guards, not authorization.
    if get_role(request) not in SUBMITTING_ROLES:
        return error_response(request, 403, "Only marketers and affiliate partners can resubmit. Switch role using the Role buttons above.")
    data = _load(sid)
    if data is None:
        raise HTTPException(status_code=404)
    marketer = get_submitter(request)
    if data["submission"]["submitted_by"] != marketer:
        return error_response(request, 403, "This item belongs to someone else. Switch to them on My submissions to edit it.")

    form = await request.form()
    raw = {name: _single(form, name) for name in RESUBMIT_FIELDS}
    values = {name: raw[name] or "" for name in RESUBMIT_FIELDS}
    base_version = review.parse_id(_single(form, "base_version") or "")
    today = clock.today()
    sub = data["submission"]
    # The fixed fields come from the database so the shared validator has everything it needs.
    checked = dict(raw, title=sub["title"], product=sub["product"], channel=sub["channel"])
    clean, errors, warnings = submit.validate_submission(checked, today)
    errors = {k: v for k, v in errors.items() if k in RESUBMIT_FIELDS}
    if errors:
        return _render_resubmit(request, data, values, errors, warnings, status_code=422, show_diff=True)
    if _single(form, "action") == "check":
        # "Check flags": show the flags and the changes so far for what is typed. Nothing is written.
        return _render_resubmit(request, data, values, {}, warnings, show_diff=True)
    try:
        with db.connect() as conn:
            submit.create_version(conn, sid, base_version, clean, marketer, clock.now())
    except submit.SubmitError as exc:
        if exc.code == "not_found":
            raise HTTPException(status_code=404)
        if exc.code == "not_owner":
            return error_response(request, 403, "This item belongs to someone else. Switch to them on My submissions to edit it.")
        if exc.code == "unchanged":
            return _render_resubmit(request, data, values, {"copy": UNCHANGED_TEXT}, warnings, status_code=422,
                                show_diff=True)
        # Stale, locked or full: show the item as it really is now, keeping what was typed if a form remains.
        data = _load(sid)
        if data is None:
            raise HTTPException(status_code=404)
        return _render_resubmit(request, data, values, {}, warnings,
                                banner=RESUBMIT_ERROR_TEXT[exc.code], status_code=RESUBMIT_ERROR_STATUS[exc.code])
    return RedirectResponse("/mine?submitted=%d" % sid, status_code=303, headers=NO_STORE)
