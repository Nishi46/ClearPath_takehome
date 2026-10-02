import logging
import math

from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from app import clock, db, review, seed
from app.queue import FILTER_FIELDS, FILTER_OPTIONS, empty_kind, filters_from_query, list_queue, row_view, summary_text
from app.cooldown import Cooldown
from app.roles import COOKIE_MAX_AGE, COOKIE_NAME, REVIEWER_NAME, ROLES, get_role
from app.security import same_origin
from app.templating import render

logger = logging.getLogger(__name__)
router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}

RESET_COOLDOWN_SECONDS = 10
reset_cooldown = Cooldown(RESET_COOLDOWN_SECONDS)


def _json(body, status_code):
    return Response(content=body, status_code=status_code, media_type="application/json",
                    headers=NO_STORE)


@router.get("/healthz")
def healthz():
    # The body is fixed text on purpose: no versions, paths or exception details.
    # The real error goes to the server log only.
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception:
        logger.exception("Health check failed")
        return _json('{"status":"unavailable"}', 503)
    return _json('{"status":"ok"}', 200)


@router.get("/")
def queue(request: Request):
    # A fixed flag, never the query value itself: only exactly ?reset=done shows the banner.
    reset_done = request.query_params.getlist("reset") == ["done"]
    filters = filters_from_query(request.query_params)
    with db.connect() as conn:
        today = clock.today()
        rows = [row_view(r, today) for r in list_queue(conn, **filters)]
        empty = empty_kind(conn, rows, filters)
    response = render(request, "queue.html", reset_done=reset_done, rows=rows, filters=filters,
                      filter_fields=FILTER_FIELDS, filter_options=FILTER_OPTIONS,
                      summary=summary_text(rows), empty=empty,
                      review_query=review.with_back("", review.safe_back("/?" + urlencode(
                          [(k, v) for k, v in filters.items() if v]))))
    response.headers["Cache-Control"] = "no-store"  # urgency labels depend on today's date
    return response


def _is_https(request):
    # Render terminates TLS at its proxy, so the app itself sees plain http.
    # A client can only use this header to mark its own cookie Secure, which is harmless.
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


@router.post("/role")
def set_role(request: Request, role: str = Form("")):
    if role not in ROLES:
        # Do not echo the submitted value back.
        return PlainTextResponse("Unknown role.", status_code=400)
    # A marketer lands on their own submissions, a reviewer on the queue.
    response = RedirectResponse("/mine" if role == "marketer" else "/", status_code=303)
    response.set_cookie(
        COOKIE_NAME, role, max_age=COOKIE_MAX_AGE, path="/",
        httponly=True, samesite="lax", secure=_is_https(request),
    )
    return response


@router.get("/reset/confirm")
def reset_confirm(request: Request):
    response = render(request, "reset_confirm.html")
    response.headers["Cache-Control"] = "no-store"
    return response


# Both roles may reset: the role is a demo label, not authorization (assumption A4), so there
# is nothing to check it against. The guards are the confirmation field, the same-origin
# check and the cooldown.
@router.post("/reset")
def reset(request: Request, confirm: str = Form("")):
    if not same_origin(request):
        return PlainTextResponse("Reset must be started from this site.", status_code=403, headers=NO_STORE)
    if confirm != "reset":
        return PlainTextResponse("Please confirm the reset.", status_code=400, headers=NO_STORE)
    wait = reset_cooldown.acquire(clock.now())
    if wait:
        return PlainTextResponse("A reset just ran. Please wait a few seconds and try again.",
                                 status_code=429, headers={**NO_STORE, "Retry-After": str(math.ceil(wait))})
    try:
        with db.connect() as conn:
            seed.reset_to_seed(conn)
    except Exception:
        reset_cooldown.release()
        raise
    return RedirectResponse("/?reset=done", status_code=303, headers=NO_STORE)


def _render_review(request, data, status_code=200, error=None, reason="", back="/", diff=False, dismiss_state=None,
                   comment_state=None):
    diff_data = review.diff_view(data, diff, back)
    # In diff mode nothing is highlighted, so no flag card links to a highlight that is not there.
    pieces = [] if diff_data and diff_data.get("pieces") else review.copy_view(data)
    cards, dismissed = review.cards_view(data, pieces), review.dismissals_view(data)
    comment_form = review.comment_form_view(data, back)
    if comment_form:  # "Use snippet" links only where a comment can be posted
        sid = data["submission"]["id"]
        for item in cards + dismissed:
            if item["snippet"]:
                item["snippet_href"] = review.snippet_href(sid, comment_form["version"], comment_form["version"],
                                                          item["rule_id"], back)
    response = render(request, "review.html", status_code=status_code, head=review.header_view(data, back),
                      copy=pieces, cards=cards, dismissed=dismissed,
                      versions=review.versions_view(data, back, bool(diff_data and diff_data["on"])), diff=diff_data, notices=review.notices_view(data, back),
                      history=review.history_view(data), comments=review.comments_view(data),
                      notes=(data["version"]["notes"] or "").strip(), error=error, reason=reason,
                      decision_form=review.decision_form_view(data, back), feedback=review.feedback_view(data),
                      dismiss_form=review.dismiss_form_view(data, back), dismiss_state=dismiss_state,
                      comment_form=comment_form, comment_state=comment_state)
    response.headers["Cache-Control"] = "no-store"  # the decision form depends on current state
    return response


@router.get("/review/{submission_id}")
def review_page(request: Request, submission_id: str):
    # The id is a plain str on purpose: a typed int would make FastAPI answer bad input with its
    # own JSON 422. Anything unparseable or unknown gets the same 404 page.
    sid = review.parse_id(submission_id)
    versions = request.query_params.getlist("v")
    if sid is None or len(versions) > 1:
        raise HTTPException(status_code=404)
    number = None
    if versions:
        number = review.parse_id(versions[0])
        if number is None:
            raise HTTPException(status_code=404)
    backs = request.query_params.getlist("back")
    back = review.safe_back(backs[0]) if len(backs) == 1 else "/"
    if back == "/" and get_role(request) == "marketer":
        back = review.MINE_URL  # a marketer goes back to their own list unless they came from a filtered queue
    with db.connect() as conn:
        data = review.load_review(conn, sid, number)
    if data is None:
        raise HTTPException(status_code=404)
    # Only exactly one ?diff=1 turns the diff on; anything else is ignored, never an error.
    # ?snippet=R2 prefills the comment box for a reviewer on the current version; any other value is
    # ignored silently and never printed. Nothing is written by a GET.
    prefill = None
    if get_role(request) == "reviewer" and review.comment_form_view(data) is not None:
        prefill = review.prefill_view(data, request.query_params.getlist("snippet"))
    return _render_review(request, data, back=back, diff=request.query_params.getlist("diff") == ["1"],
                          comment_state=prefill)


def _single(form, name):
    """The one value of a form field, or None if it is missing, repeated or an uploaded file."""
    values = form.getlist(name)
    return values[0] if len(values) == 1 and isinstance(values[0], str) else None


@router.post("/review/{submission_id}/decision")
async def decide(request: Request, submission_id: str):
    # The guards run in this order: origin, id, role, existence, form, then the decision rules.
    # All of the real rules live in review.record_decision; nothing here decides anything itself.
    if not same_origin(request):
        return PlainTextResponse("Decisions must be made from this site.", status_code=403, headers=NO_STORE)
    sid = review.parse_id(submission_id)
    if sid is None:
        raise HTTPException(status_code=404)
    # The role is a demo label (assumption A4), so this is a product guard, not authorization:
    # a marketer should not approve their own copy.
    if get_role(request) != "reviewer":
        return PlainTextResponse("Only reviewers can record decisions.", status_code=403, headers=NO_STORE)
    with db.connect() as conn:
        data = review.load_review(conn, sid)
    if data is None:
        raise HTTPException(status_code=404)

    form = await request.form()
    outcome, version, reason = _single(form, "outcome"), _single(form, "version"), _single(form, "reason")
    reason_text = reason or ""
    back = review.safe_back(_single(form, "back"))
    number = review.parse_id(version) if version is not None else None
    if outcome is None or number is None or (reason is None and form.getlist("reason")):
        return _render_review(request, data, 422, review.DECISION_MESSAGES["bad_form"], reason_text, back)

    try:
        with db.connect() as conn:
            review.record_decision(conn, sid, number, outcome, reason, REVIEWER_NAME, clock.now())
    except review.DecisionError as exc:
        with db.connect() as conn:
            data = review.load_review(conn, sid)
        if data is None:
            raise HTTPException(status_code=404)
        return _render_review(request, data, review.DECISION_STATUS[exc.code],
                              review.conflict_message(exc.code, data), reason_text, back)
    return RedirectResponse(review.with_back("/review/%d" % sid, back), status_code=303, headers=NO_STORE)


@router.post("/review/{submission_id}/dismiss")
async def dismiss(request: Request, submission_id: str):
    # Same guard order as /decision: origin, id, role, existence, form, then the dismissal rules,
    # which all live in review.dismiss_flag. The reviewer and time come from the server.
    if not same_origin(request):
        return PlainTextResponse("Dismissals must be made from this site.", status_code=403, headers=NO_STORE)
    sid = review.parse_id(submission_id)
    if sid is None:
        raise HTTPException(status_code=404)
    if get_role(request) != "reviewer":
        return PlainTextResponse("Only reviewers can dismiss flags.", status_code=403, headers=NO_STORE)
    with db.connect() as conn:
        data = review.load_review(conn, sid)
    if data is None:
        raise HTTPException(status_code=404)

    form = await request.form()
    rule_id, version, note = _single(form, "rule_id"), _single(form, "version"), _single(form, "note")
    back = review.safe_back(_single(form, "back"))
    number = review.parse_id(version) if version is not None else None
    if rule_id is None or number is None or note is None:
        return _render_review(request, data, 422, review.DECISION_MESSAGES["bad_form"], back=back)

    try:
        with db.connect() as conn:
            review.dismiss_flag(conn, sid, number, rule_id, note, REVIEWER_NAME, clock.now())
    except review.DismissError as exc:
        with db.connect() as conn:
            data = review.load_review(conn, sid)
        if data is None:
            raise HTTPException(status_code=404)
        status = review.DISMISS_STATUS[exc.code]
        if exc.code in review.NOTE_CODES:
            # Shown beside that flag's form with the typed note kept; rule_id only selects a card.
            state = {"rule_id": rule_id, "note": note, "error": review.NOTE_MESSAGES[exc.code]}
            return _render_review(request, data, status, back=back, dismiss_state=state)
        return _render_review(request, data, status, review.dismiss_conflict_message(exc.code, data, rule_id),
                              back=back)
    return RedirectResponse(review.with_back("/review/%d" % sid, back) + "#flags-heading", status_code=303,
                            headers=NO_STORE)


@router.post("/review/{submission_id}/comment")
async def comment(request: Request, submission_id: str):
    # Same guard order as /decision and /dismiss. The author and time come from the server.
    if not same_origin(request):
        return PlainTextResponse("Comments must be made from this site.", status_code=403, headers=NO_STORE)
    sid = review.parse_id(submission_id)
    if sid is None:
        raise HTTPException(status_code=404)
    if get_role(request) != "reviewer":
        return PlainTextResponse("Only reviewers can comment.", status_code=403, headers=NO_STORE)
    with db.connect() as conn:
        data = review.load_review(conn, sid)
    if data is None:
        raise HTTPException(status_code=404)

    form = await request.form()
    text, version = _single(form, "text"), _single(form, "version")
    rules = form.getlist("rule_id")  # absent means a free comment; "" is not absent
    rule_id = rules[0] if len(rules) == 1 and isinstance(rules[0], str) else None
    back = review.safe_back(_single(form, "back"))
    number = review.parse_id(version) if version is not None else None
    if text is None or number is None or (rules and rule_id is None):
        return _render_review(request, data, 422, review.DECISION_MESSAGES["bad_form"], back=back)

    try:
        with db.connect() as conn:
            review.add_comment(conn, sid, number, text, rule_id, REVIEWER_NAME, clock.now())
    except review.CommentError as exc:
        with db.connect() as conn:
            data = review.load_review(conn, sid)
        if data is None:
            raise HTTPException(status_code=404)
        status = review.COMMENT_STATUS[exc.code]
        state = {"text": text, "rule_id": rule_id if rule_id and any(
            f["rule_id"] == rule_id for f in data["flags"]) else None, "snippet": False, "error": None}
        if exc.code in review.COMMENT_FIELD_CODES:
            state["error"] = review.COMMENT_MESSAGES[exc.code]
            return _render_review(request, data, status, back=back, comment_state=state)
        banner = review.COMMENT_CONFLICTS.get(exc.code, review.DECISION_MESSAGES["bad_form"])
        return _render_review(request, data, status, banner, back=back, comment_state=state)
    return RedirectResponse(review.with_back("/review/%d" % sid, back) + "#comments-heading", status_code=303,
                            headers=NO_STORE)
