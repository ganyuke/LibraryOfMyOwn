from __future__ import annotations

from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from libmyown.auth import is_admin, login_admin, logout_admin
from libmyown.request_url import request_is_secure
from libmyown.theme import set_theme_response
from libmyown.web.common import form_post, render
from libmyown.web.state import get_state


def login_get(request: Request) -> Response:
    if is_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return render(request, "login.html", {})


@form_post(admin=False)
def login_post(request: Request, form: FormData) -> Response:
    password = str(form.get("password", ""))
    if login_admin(request, get_state(request).secrets, password):
        return RedirectResponse("/admin", status_code=303)
    return render(request, "login.html", {"error": "Wrong password."}, status_code=401)


@form_post(admin=False)
def logout(request: Request, form: FormData) -> Response:
    logout_admin(request)
    return RedirectResponse("/", status_code=303)


@form_post(admin=False)
def theme_post(request: Request, form: FormData) -> Response:
    return set_theme_response(
        theme=str(form.get("theme", "")),
        next_url=str(form.get("next", "/")),
        secure=request_is_secure(request, env_https_enabled=get_state(request).settings.https_enabled),
    )


routes = [
    Route("/login", login_get, methods=["GET"]),
    Route("/login", login_post, methods=["POST"]),
    Route("/logout", logout, methods=["POST"]),
    Route("/theme", theme_post, methods=["POST"]),
]
