import logging
import math
from urllib.parse import urlparse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from app import clock, db, review, seed
from app.queue import FILTER_FIELDS, FILTER_OPTIONS, empty_kind, filters_from_query, list_queue, row_view, summary_text
from app.cooldown import Cooldown
from app.roles import COOKIE_MAX_AGE, COOKIE_NAME, ROLES
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
                      summary=summary_text(rows), empty=empty)
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
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME, role, max_age=COOKIE_MAX_AGE, path="/",
        httponly=True, samesite="lax", secure=_is_https(request),
    )
    return response


def _same_origin(request):
    """False if the browser says this request came from another site.

    Browsers send Origin (or at least Referer) on cross-site form posts. Neither header means
    a non-browser client such as curl, which is allowed: the guard is against a hostile web
    page, not against someone who can already send requests. The Origin value "null" is refused.
    """
    host = request.headers.get("host", "")
    for name in ("origin", "referer"):
        value = request.headers.get(name)
        if value is None:
            continue
        try:
            return bool(host) and urlparse(value).netloc == host
        except ValueError:
            return False
    return True


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
    if not _same_origin(request):
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
    with db.connect() as conn:
        data = review.load_review(conn, sid, number)
    if data is None:
        raise HTTPException(status_code=404)
    response = render(request, "review.html", head=review.header_view(data), copy=review.copy_view(data),
                      notes=(data["version"]["notes"] or "").strip())
    response.headers["Cache-Control"] = "no-store"  # the decision form depends on current state
    return response
