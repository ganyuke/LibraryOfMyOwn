from __future__ import annotations

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from archive.csrf import rotate_csrf_token
from archive.secrets import Secrets, verify_password


SESSION_KEY = "admin"
SETUP_PATH = "/setup"


def is_admin(request: Request) -> bool:
    return bool(request.session.get(SESSION_KEY))


def require_admin(request: Request) -> Response | None:
    if not is_admin(request):
        return RedirectResponse("/login", status_code=303)
    return None


def require_setup_complete(secrets: Secrets, request: Request) -> Response | None:
    if secrets.is_configured:
        return None
    if request.url.path.startswith(SETUP_PATH):
        return None
    if request.url.path.startswith("/static"):
        return None
    return RedirectResponse(SETUP_PATH, status_code=303)


def login_admin(request: Request, secrets: Secrets, password: str) -> bool:
    if not secrets.admin_password_hash:
        return False
    if not verify_password(password, secrets.admin_password_hash):
        return False
    request.session[SESSION_KEY] = True
    rotate_csrf_token(request)
    return True


def logout_admin(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)
