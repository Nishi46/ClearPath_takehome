from starlette.datastructures import MutableHeaders
from starlette.responses import PlainTextResponse

# Scripts, styles and images come only from our own origin. htmx is vendored in
# app/static, and its inline-style and eval features are turned off in base.html.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": CSP,
}

# Forms here are small (the longest seed copy is a few KB). Anything bigger is refused.
MAX_BODY_BYTES = 1024 * 1024


def apply_security_headers(response):
    """For responses built outside the middleware stack, such as the 500 handler's."""
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_static = scope["path"].startswith("/static/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                # Without this a browser may keep using an old stylesheet for hours after a
                # change or a redeploy. "no-cache" still allows caching, but only after the
                # server confirms via the ETag that the file is unchanged.
                if is_static and "cache-control" not in headers:
                    headers["Cache-Control"] = "no-cache"
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodyLimitMiddleware:
    """Refuse request bodies over MAX_BODY_BYTES, whether or not Content-Length is honest."""

    def __init__(self, app, max_bytes=MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._too_large(scope, receive, send)
            return

        received = 0
        too_large = False
        started = False

        async def counting_receive():
            nonlocal received, too_large
            if too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Not an exception: FastAPI turns body-read errors into a 400.
                    too_large = True
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message):
            nonlocal started
            if too_large:
                return  # drop whatever the app answers to a body we cut off
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except Exception:
            if not too_large:
                raise
        if too_large and not started:
            await self._too_large(scope, receive, send)

    @staticmethod
    async def _too_large(scope, receive, send):
        response = PlainTextResponse("Request too large.", status_code=413)
        await response(scope, receive, send)
