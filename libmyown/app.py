from __future__ import annotations

import contextlib
import logging

from a2wsgi import WSGIMiddleware
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.types import ASGIApp, Receive, Scope, Send

from libmyown.config import Settings, load_settings, require_admin_password
from libmyown.git_http import AuthenticatedGitApp, mount_path_for_git
from libmyown.git_store import GitStore
from libmyown.pdf import PdfService
from libmyown.site_config import (
    ConfigStore,
    default_site_config,
    save_site_config,
)
from libmyown.snapshot import SnapshotHolder
from libmyown.web import admin, auth, public
from libmyown.web.security import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from libmyown.web.state import AppState, effective_branch
from libmyown.worker import PostReceiveWorker

logger = logging.getLogger(__name__)

SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
GIT_HTTP_WORKERS = 4


async def unhandled_exception(request: Request, exc: Exception) -> Response:
    if isinstance(exc, HTTPException):
        return HTMLResponse(str(exc.detail), status_code=exc.status_code)
    logger.exception("Unhandled error")
    return HTMLResponse("Something went wrong.", status_code=500)


def _session_cookie_secure(settings: Settings, public_url: str) -> bool:
    if settings.https_enabled is not None:
        return settings.https_enabled
    return public_url.strip().lower().startswith("https://")


class SiteSessionMiddleware:
    """Session cookies whose Secure flag follows the current public URL.

    SessionMiddleware fixes the flag at construction, so keep one of each and pick
    per request. A public URL changed in the admin panel then applies without a restart.
    """

    def __init__(self, app: ASGIApp, *, settings: Settings, config: ConfigStore, **options) -> None:
        self._settings = settings
        self._config = config
        self._secure = SessionMiddleware(app, https_only=True, **options)
        self._plain = SessionMiddleware(app, https_only=False, **options)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        secure = _session_cookie_secure(self._settings, self._config.get().public_url)
        await (self._secure if secure else self._plain)(scope, receive, send)


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or load_settings()
    require_admin_password(settings.secrets)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.pdf_cache_dir.mkdir(parents=True, exist_ok=True)
    if not settings.site_config_path.is_file():
        save_site_config(settings.site_config_path, default_site_config())

    config = ConfigStore(settings.site_config_path)
    site = config.get()
    store = GitStore(settings.stories_repo, branch=effective_branch(site))
    snapshots = SnapshotHolder(store, settings.work_index_path)
    worker = PostReceiveWorker(
        store,
        snapshots,
        resolve_branch=lambda: effective_branch(config.get()),
        record_path=settings.maintenance_record_path,
    )
    templates = Jinja2Templates(directory=str(settings.templates_dir))

    static_versions: dict[str, int] = {}

    def static_url(path: str) -> str:
        version = static_versions.get(path)
        if version is None:
            target = settings.static_dir / path
            version = int(target.stat().st_mtime) if target.is_file() else 0
            static_versions[path] = version
        return f"/static/{path}?v={version}"

    templates.env.globals["static_url"] = static_url

    state = AppState(
        settings=settings,
        config=config,
        store=store,
        snapshots=snapshots,
        worker=worker,
        pdf=PdfService(
            settings.pdf_scripts,
            settings.pdf_cache_dir,
            max_cache_bytes=settings.pdf_cache_max_bytes,
            timeout_seconds=settings.pdf_timeout_seconds,
        ),
        templates=templates,
    )

    git_wsgi = AuthenticatedGitApp(
        store,
        check_credentials=lambda user, password: state.secrets.git_credentials_match(user, password),
        on_receive=worker.request,
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        store.repair_head()
        snapshots.load_or_build()
        worker.start()
        try:
            yield
        finally:
            worker.stop()
            store.close()

    routes = [
        *public.routes,
        *auth.routes,
        *admin.routes,
        Mount(mount_path_for_git(), app=WSGIMiddleware(git_wsgi, workers=GIT_HTTP_WORKERS)),
        Mount("/static", app=StaticFiles(directory=str(settings.static_dir)), name="static"),
    ]

    app = Starlette(
        routes=routes,
        middleware=[
            Middleware(SecurityHeadersMiddleware),
            Middleware(BodySizeLimitMiddleware),
            Middleware(
                SiteSessionMiddleware,
                settings=settings,
                config=config,
                secret_key=settings.session_secret,
                same_site="lax",
                max_age=SESSION_MAX_AGE_SECONDS,
            ),
        ],
        exception_handlers={Exception: unhandled_exception},
        lifespan=lifespan,
    )
    app.state.libmyown = state
    return app
