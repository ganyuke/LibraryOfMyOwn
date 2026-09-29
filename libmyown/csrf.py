from __future__ import annotations

import secrets

from starlette.requests import Request

CSRF_SESSION_KEY = "csrf_token"
CSRF_FORM_FIELD = "csrf_token"


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


def csrf_valid(request: Request, form) -> bool:
    session_token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(session_token, str) or not session_token:
        return False
    submitted = form.get(CSRF_FORM_FIELD, "")
    if isinstance(submitted, str) and secrets.compare_digest(submitted, session_token):
        return True
    header = request.headers.get("x-csrf-token", "")
    return bool(header) and secrets.compare_digest(header, session_token)
