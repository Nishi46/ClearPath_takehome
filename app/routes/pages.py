import logging

from fastapi import APIRouter
from fastapi.responses import Response

from app import db

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
