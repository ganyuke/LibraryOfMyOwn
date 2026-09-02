from __future__ import annotations

from collections.abc import Callable

from starlette.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from archive.auth import SETUP_PATH


class ForwardedProtoMiddleware:
    """Honor X-Forwarded-Proto from reverse proxies for URL scheme and secure cookies."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            proto = headers.get(b"x-forwarded-proto", b"").decode("latin-1")
            if proto:
                scope["scheme"] = proto.split(",")[0].strip().lower()
        await self.app(scope, receive, send)


class SetupRequiredMiddleware:
    """Redirect to /setup until an admin password exists."""

    def __init__(self, app: ASGIApp, *, is_configured: Callable[[], bool]) -> None:
        self.app = app
        self._is_configured = is_configured

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self._is_configured():
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith(SETUP_PATH) or path.startswith("/static"):
            await self.app(scope, receive, send)
            return
        response = RedirectResponse(SETUP_PATH, status_code=303)
        await response(scope, receive, send)
