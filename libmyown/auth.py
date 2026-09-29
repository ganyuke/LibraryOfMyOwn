from __future__ import annotations

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from libmyown.csrf import rotate_csrf_token
from libmyown.secrets import Secrets, verify_password


SESSION_KEY = "admin"
SESSION_EPOCH_KEY = "admin_epoch"


def _secrets(request: Request) -> Secrets:
    return request.app.state.libmyown.secrets


def is_admin(request: Request) -> bool:
    session = request.session
    if not session.get(SESSION_KEY):
        return False
    return session.get(SESSION_EPOCH_KEY) == _secrets(request).session_epoch


def require_admin(request: Request) -> Response | None:
    if not is_admin(request):
        return RedirectResponse("/login", status_code=303)
    return None


def login_admin(request: Request, secrets: Secrets, password: str) -> bool:
    if not secrets.admin_password_hash:
        return False
    if not verify_password(password, secrets.admin_password_hash):
        return False
    # Start from a clean session so nothing set before login carries over.
    request.session.clear()
    request.session[SESSION_KEY] = True
    request.session[SESSION_EPOCH_KEY] = secrets.session_epoch
    rotate_csrf_token(request)
    return True


def logout_admin(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)
    request.session.pop(SESSION_EPOCH_KEY, None)
    rotate_csrf_token(request)
