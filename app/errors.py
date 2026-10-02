from fastapi.responses import HTMLResponse

from app.security import apply_security_headers
from app.templating import render

# Used only if the error template itself fails to render.
FALLBACK_500 = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Something went wrong</title>"
    "</head><body><h1>Something went wrong</h1><p><a href=\"/\">Back to the queue</a></p></body></html>"
)


async def not_found(request, exc):
    return render(request, "error.html", status_code=404, heading="Page not found",
                  message="We could not find that page.")


async def server_error(request, exc):
    # No exception text, class name or path reaches the page. uvicorn logs the traceback.
    try:
        response = render(request, "error.html", status_code=500, heading="Something went wrong",
                          message="Please try again. If it keeps happening, tell whoever is running the demo.")
    except Exception:
        response = HTMLResponse(FALLBACK_500, status_code=500)
    # The outermost error middleware sends this response, so our header middleware never sees it.
    return apply_security_headers(response)
