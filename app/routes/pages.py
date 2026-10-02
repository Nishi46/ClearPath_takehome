import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from app import db
from app.roles import COOKIE_MAX_AGE, COOKIE_NAME, ROLES
from app.templating import render

logger = logging.getLogger(__name__)
router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}


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
    return render(request, "queue.html")


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
