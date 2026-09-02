from __future__ import annotations

import secrets
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.types import ASGIApp

CSRF_SESSION_KEY = "csrf_token"
CSRF_FORM_FIELD = "csrf_token"
FORM_STATE_KEY = "form"


async def get_form(request: Request):
    cached = getattr(request.state, FORM_STATE_KEY, None)
    if cached is not None:
        return cached
    form = await request.form()
    setattr(request.state, FORM_STATE_KEY, form)
    return form


def get_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def rotate_csrf_token(request: Request) -> str:
    token = secrets.token_urlsafe(32)
    request.session[CSRF_SESSION_KEY] = token
    return token


def csrf_exempt(path: str) -> bool:
    return path.startswith("/git/") or path.startswith("/static/")


async def csrf_valid(request: Request) -> bool:
    session_token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(session_token, str) or not session_token:
        return False
    form = await get_form(request)
    submitted = form.get(CSRF_FORM_FIELD, "")
    if isinstance(submitted, str) and secrets.compare_digest(submitted, session_token):
        return True
    header = request.headers.get("x-csrf-token", "")
    return isinstance(header, str) and secrets.compare_digest(header, session_token)


class CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method == "POST" and not csrf_exempt(request.url.path):
            if not await csrf_valid(request):
                return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        return await call_next(request)
