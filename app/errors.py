from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.security import apply_security_headers
from app.templating import render

# Used only if the error template itself fails to render.
FALLBACK_500 = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>We hit a problem</title>"
    "</head><body><h1>We hit a problem</h1><p><a href=\"/\">Back to the queue</a></p></body></html>"
)

# One heading and one default sentence per status. The page always says the status in words and
# links back to the queue. Nothing from the request, an exception or the server ever goes in.
HEADINGS = {
    400: "Request not understood", 403: "Not allowed", 404: "Page not found", 405: "Action not available",
    409: "Out of date", 413: "Request too large", 422: "Could not read the request", 429: "Please wait a moment",
    500: "We hit a problem", 503: "The demo is full",
}
DEFAULT_MESSAGES = {
    400: "That request could not be understood. Go back and try again.",
    403: "You can't do that from here.",
    404: "We could not find that page.",
    405: "That action isn't available on this page.",
    409: "This changed since you opened it. Open it again to see where it stands.",
    413: "That request was too large. Shorten it and try again.",
    422: "That request could not be read. Go back and try again.",
    429: "Please wait a few seconds and try again.",
    500: "Please try again. If it keeps happening, tell whoever is running the demo.",
    503: "The demo is full right now. Ask whoever runs it to reset the demo, then try again.",
}


def error_response(request, status, message=None, headers=None):
    """The shared error page. `message` must be fixed text, never user input or exception text."""
    response = render(request, "error.html", status_code=status,
                      heading=HEADINGS.get(status, "We hit a problem"),
                      message=message or DEFAULT_MESSAGES.get(status, DEFAULT_MESSAGES[500]), status=status)
    response.headers["Cache-Control"] = "no-store"
    for name, value in (headers or {}).items():
        response.headers[name] = value
    return response


async def http_error(request, exc):
    # Only the status and, for a 405, the Allow header are used: exc.detail is never shown.
    status = exc.status_code if exc.status_code in HEADINGS else 500
    headers = {"Allow": exc.headers["Allow"]} if exc.headers and "Allow" in exc.headers else None
    return error_response(request, status, headers=headers)


async def validation_error(request, exc):
    return error_response(request, 422)


async def not_found(request, exc):
    return error_response(request, 404)


async def server_error(request, exc):
    # No exception text, class name or path reaches the page. uvicorn logs the traceback.
    try:
        response = error_response(request, 500)
    except Exception:
        response = HTMLResponse(FALLBACK_500, status_code=500)
    # The outermost error middleware sends this response, so our header middleware never sees it.
    return apply_security_headers(response)
