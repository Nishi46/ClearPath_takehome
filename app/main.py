import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db, seed
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import http_error, server_error, validation_error
from app.routes import import_pages, pages, submit_pages
from app.security import BodyLimitMiddleware, SecurityHeadersMiddleware
from app.templating import APP_DIR


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    # Runs at startup, not at import, so importing the app never touches the database.
    db.init_schema()
    try:
        with db.connect() as conn:
            if seed.seed_if_empty(conn):
                logger.info("Empty database: loaded the demo seed.")
    except Exception:
        # Seeding is all-or-nothing. A demo that starts empty or half-loaded looks broken,
        # so fail the start and leave the real error in the log.
        logger.exception("Could not load the demo seed; refusing to start.")
        raise
    yield


# API docs UIs are off so the app does not disclose its route surface.
app = FastAPI(
    title="ClearPath Review",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    debug=False,
    lifespan=lifespan,
)

app.include_router(pages.router)
app.include_router(submit_pages.router)
app.include_router(import_pages.router)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

# Added last means outermost: headers are applied to everything, including the 413 from the body limit.
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_exception_handler(StarletteHTTPException, http_error)
app.add_exception_handler(RequestValidationError, validation_error)
app.add_exception_handler(Exception, server_error)
