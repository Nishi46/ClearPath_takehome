from fastapi import FastAPI

# API docs UIs are off so the app does not disclose its route surface.
app = FastAPI(title="ClearPath Review", docs_url=None, redoc_url=None, openapi_url=None)
