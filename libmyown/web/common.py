"""Rendering and handler plumbing shared by the public and admin routes.

Handlers are plain synchronous functions. Starlette runs sync endpoints in its
threadpool, so git reads, markdown rendering, diffs and PDF builds never block
the event loop. POST handlers are wrapped so the form is parsed (async) and
CSRF/admin checks happen before the sync body runs in the threadpool.
"""

from __future__ import annotations

import functools
from typing import Callable
from urllib.parse import urlencode

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from libmyown.auth import is_admin
from libmyown.csrf import csrf_valid, get_csrf_token
from libmyown.request_url import request_origin
from libmyown.site_config import APP_LABEL, HOME_LABEL, SOURCE_REPO_URL
from libmyown.theme import get_theme
from libmyown.web.state import get_state

MAX_FORM_FIELDS = 5000


def render(request: Request, name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    state = get_state(request)
    site = state.site()
    ctx = {
        "origin": request_origin(request, site),
        "site_title": site.site_title,
        "home_label": HOME_LABEL,
        "source_repo_url": SOURCE_REPO_URL,
        "app_label": APP_LABEL,
        "show_login_link": site.show_login_link,
        "robots_noindex": site.robots_noindex,
        "is_admin": is_admin(request),
        "theme": get_theme(request),
        "flags": site.flags,
        **context,
        "csrf_token": get_csrf_token(request),
    }
    return state.templates.TemplateResponse(request, name, ctx, status_code=status_code)


def not_found() -> HTMLResponse:
    return HTMLResponse("Not found", status_code=404)


def message(request: Request, title: str, text: str, status_code: int) -> HTMLResponse:
    return render(request, "message.html", {"title": title, "message": text}, status_code=status_code)


def redirect(path: str, status_code: int = 303, **params: str) -> RedirectResponse:
    query = {key: value for key, value in params.items() if value}
    return RedirectResponse(f"{path}?{urlencode(query)}" if query else path, status_code=status_code)


def admin_page(handler: Callable[[Request], Response]) -> Callable[[Request], Response]:
    """Sync GET handler that requires an admin session."""

    @functools.wraps(handler)
    def wrapper(request: Request) -> Response:
        if not is_admin(request):
            return RedirectResponse("/login", status_code=303)
        return handler(request)

    return wrapper


def form_post(*, admin: bool) -> Callable:
    """Parse the form, enforce CSRF (and admin), then run the sync handler off-loop."""

    def decorate(handler: Callable[[Request, FormData], Response]):
        @functools.wraps(handler)
        async def wrapper(request: Request) -> Response:
            form = await request.form(max_files=0, max_fields=MAX_FORM_FIELDS)
            try:
                if not csrf_valid(request, form):
                    return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
                if admin and not is_admin(request):
                    return RedirectResponse("/login", status_code=303)
                return await run_in_threadpool(handler, request, form)
            finally:
                await form.close()

        return wrapper

    return decorate
