from __future__ import annotations

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

THEME_COOKIE = "libmyown_theme"
THEME_LIGHT = "light"
THEME_DARK = "dark"


def get_theme(request: Request) -> str:
    value = request.cookies.get(THEME_COOKIE, THEME_LIGHT)
    return THEME_DARK if value == THEME_DARK else THEME_LIGHT


def is_local_path(url: str) -> bool:
    """Same-origin absolute path; rejects //host and /\\host (browsers treat both as off-site)."""
    return (
        url.startswith("/")
        and not url.startswith(("//", "/\\"))
        and not any(ch in url for ch in "\r\n\t")
    )


def set_theme_response(*, theme: str, next_url: str, secure: bool = False) -> Response:
    if theme not in (THEME_LIGHT, THEME_DARK):
        theme = THEME_LIGHT
    if not is_local_path(next_url):
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
