from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.errors import not_found, server_error
from app.routes import pages
from app.security import BodyLimitMiddleware, SecurityHeadersMiddleware
from app.templating import APP_DIR


@asynccontextmanager
async def lifespan(app):
    # Runs at startup, not at import, so importing the app never touches the database.
    db.init_schema()
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
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

# Added last means outermost: headers are applied to everything, including the 413 from the body limit.
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_exception_handler(404, not_found)
app.add_exception_handler(Exception, server_error)
