from __future__ import annotations

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

THEME_COOKIE = "archive_theme"
THEME_LIGHT = "light"
THEME_DARK = "dark"


def get_theme(request: Request) -> str:
    value = request.cookies.get(THEME_COOKIE, THEME_LIGHT)
    return THEME_DARK if value == THEME_DARK else THEME_LIGHT


def set_theme_response(*, theme: str, next_url: str, secure: bool = False) -> Response:
    if theme not in (THEME_LIGHT, THEME_DARK):
        theme = THEME_LIGHT
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(
        THEME_COOKIE,
        theme,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    return response
