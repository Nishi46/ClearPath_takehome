from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.routes import pages


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
    lifespan=lifespan,
)

app.include_router(pages.router)
