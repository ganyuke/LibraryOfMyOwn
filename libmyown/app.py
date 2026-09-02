from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from a2wsgi import WSGIMiddleware
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from libmyown.auth import (
    SETUP_PATH,
    is_admin,
    login_admin,
    logout_admin,
    require_admin,
)
from libmyown.authorship import AUTHOR_MODE_EARLIEST
from libmyown.csrf import CSRFMiddleware, get_csrf_token, get_form
from libmyown.config import Settings, load_settings
from libmyown.middleware import ForwardedProtoMiddleware, SetupRequiredMiddleware
from libmyown.request_url import normalize_public_url, request_is_secure, request_origin
from libmyown.secrets import (
    rotate_git_password,
    rotate_session_secret,
    save_secrets,
    set_admin_password,
    verify_password,
)
from libmyown.content import (
    extract_work_body,
    format_rev_date,
    parse_field_name_list,
)
from libmyown.continuity import (
    continuity_for_work,
    continuity_selection,
    continuity_story_options,
)
from libmyown.diff import diff_byte_stats, diff_html as render_diff_html, format_compare_byte_summary
from libmyown.git_http import AuthenticatedGitApp, mount_path_for_git
from libmyown.git_repo import StoriesRepo, path_to_slug
from libmyown.pdf import discover_pdf_options, generate_pdf
from libmyown.service import LibraryService
from libmyown.site_config import (
    APP_LABEL,
    Crosspost,
    DEFAULT_SITE_TITLE,
    HOME_LABEL,
    SOURCE_REPO_URL,
    SiteConfig,
    StoryContinuity,
    FlagDef,
    default_site_config,
    load_site_config,
    normalize_flag_id,
    save_site_config,
    site_config_mtime,
)
from libmyown.theme import get_theme, set_theme_response
from libmyown.work_index import WorkIndexStore


logger = logging.getLogger(__name__)


async def unhandled_exception(request: Request, exc: Exception) -> Response:
    if isinstance(exc, HTTPException):
        return HTMLResponse(str(exc.detail), status_code=exc.status_code)
    logger.exception("Unhandled error")
    return HTMLResponse("Something went wrong.", status_code=500)


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.pdf_cache_dir.mkdir(parents=True, exist_ok=True)
    if not settings.site_config_path.is_file():
        save_site_config(settings.site_config_path, default_site_config())

    site = load_site_config(settings.site_config_path)

    def effective_branch(current_site: SiteConfig | None = None) -> str | None:
        current_site = current_site or load_site_config(settings.site_config_path)
        for value in (
            settings.stories_branch,
            current_site.stories_branch.strip() or None,
        ):
            if value:
                return value
        return None

    repo = StoriesRepo(settings.stories_repo, branch=effective_branch(site))
    work_index = WorkIndexStore(settings.work_index_path, repo)
    templates = Jinja2Templates(directory=str(settings.templates_dir))

    def static_url(path: str) -> str:
        target = settings.static_dir / path
        version = int(target.stat().st_mtime) if target.is_file() else 0
        return f"/static/{path}?v={version}"

    templates.env.globals["static_url"] = static_url

    secrets = settings.secrets

    state = {
        "settings": settings,
        "secrets": secrets,
        "repo": repo,
        "work_index": work_index,
        "templates": templates,
    }

    def get_secrets():
        return state["secrets"]

    def get_site() -> SiteConfig:
        return load_site_config(settings.site_config_path)

    def git_remote_url_display(site: SiteConfig, request: Request) -> str:
        origin = request_origin(request, site)
        username = get_secrets().git_username
        host = origin.removeprefix("https://").removeprefix("http://")
        return f"https://{username}@{host}/git/stories.git"

    def sync_repo_branch(site: SiteConfig | None = None) -> None:
        branch = effective_branch(site)
        if repo._branch != branch:
            repo._branch = branch
            repo.invalidate()

    def get_service() -> LibraryService:
        return LibraryService(
            repo,
            get_site(),
            work_index,
            site_mtime=site_config_mtime(settings.site_config_path),
        )

    def render(
        request: Request,
        name: str,
        context: dict,
        status_code: int = 200,
    ) -> HTMLResponse:
        current_theme = get_theme(request)
        site = get_site()
        ctx = {
            "origin": request_origin(request, site),
            "site_title": site.site_title,
            "home_label": HOME_LABEL,
            "source_repo_url": SOURCE_REPO_URL,
            "app_label": APP_LABEL,
            "show_login_link": site.show_login_link,
            "robots_noindex": site.robots_noindex,
            "is_admin": is_admin(request),
            "theme": current_theme,
            "flags": site.flags,
            **context,
            "csrf_token": get_csrf_token(request),
        }
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    def resolve_admin_story(
        service: LibraryService, story_param: str
    ) -> tuple[str, str | None]:
        paths = service.all_paths()
        if not story_param:
            return "", None
        path = service.resolve_slug(story_param)
        if path:
            slug = path_to_slug(path)
            return slug or story_param, path
        if story_param in paths:
            slug = path_to_slug(story_param)
            return slug or "", story_param
        return story_param, None

    def admin_story_redirect(service: LibraryService, story_path: str, **params: str) -> str:
        slug = path_to_slug(story_path)
        if not slug:
            return ""
        query = {"story": slug, **params}
        return f"?{urlencode(query)}"

    def work_page_context(
        path: str, view, service: LibraryService | None = None
    ) -> dict:
        service = service or get_service()
        site = get_site()
        continuity = continuity_for_work(site, service, path)
        linked_paths = set(site.story_continuity.get(path, StoryContinuity()).previous)
        linked_paths.update(site.story_continuity.get(path, StoryContinuity()).next)
        related_works = [
            work for work in service.related_works(path) if work.path not in linked_paths
        ]
        field_order = site.field_order or None
        head_sha = repo.head_sha()
        viewing_revision = bool(
            view.commit_sha and head_sha and view.commit_sha != head_sha
        )
        breadcrumb_items: list[tuple[str, str | None]] = [
            (HOME_LABEL, "/"),
            (view.meta.title, f"/works/{view.slug}"),
        ]
        if viewing_revision:
            breadcrumb_items.append((view.short_sha, None))
        return {
            "work": view,
            "related_works": related_works,
            "continuity": continuity,
            "pdf_options": discover_pdf_options(settings.pdf_scripts),
            "work_blurb": view.meta.blurb(site.blurb_fields),
            "meta_rows": view.meta.display_rows(site.blurb_fields, field_order),
            "crossposts": site.crossposts_for(path),
            "breadcrumb_items": breadcrumb_items,
        }

    def slug_redirect_response(request: Request, slug: str) -> Response | None:
        canonical = get_service().canonical_slug(slug)
        if canonical == slug:
            return None
        prefix = f"/works/{slug}"
        suffix = request.url.path.removeprefix(prefix)
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/works/{canonical}{suffix}{query}", status_code=301)

    async def index(request: Request) -> Response:
        service = get_service()
        works = service.published_works()
        return render(request, "index.html", {"works": works})

    async def work_view(request: Request) -> Response:
        slug = request.path_params["slug"]
        redirected = slug_redirect_response(request, slug)
        if redirected:
            return redirected
        service = get_service()
        path = service.resolve_slug(slug)
        if not path or not service.is_published(path):
            return HTMLResponse("Not found", status_code=404)
        view = service.work_at(path)
        if view is None:
            return HTMLResponse("Not found", status_code=404)
        return render(request, "work.html", work_page_context(path, view, service))

    async def work_revision(request: Request) -> Response:
        slug = request.path_params["slug"]
        redirected = slug_redirect_response(request, slug)
        if redirected:
            return redirected
        rev = request.path_params["rev"]
        service = get_service()
        path = service.resolve_slug(slug)
        if not path:
            return HTMLResponse("Not found", status_code=404)
        sha = repo.resolve_sha(rev)
        if not sha:
            return HTMLResponse("Not found", status_code=404)
        if not service.can_view_revision(path, sha, admin=is_admin(request)):
            return render(
                request,
                "message.html",
                {"title": "Revision unavailable", "message": "This revision is not available."},
                status_code=403,
            )
        view = service.work_at(path, sha)
        if view is None:
            return HTMLResponse("Not found", status_code=404)
        return render(request, "work.html", work_page_context(path, view, service))

    async def work_history(request: Request) -> Response:
        slug = request.path_params["slug"]
        redirected = slug_redirect_response(request, slug)
        if redirected:
            return redirected
        service = get_service()
        path = service.resolve_slug(slug)
        if not path or not service.is_published(path):
            return HTMLResponse("Not found", status_code=404)
        history = service.visible_history(path)
        old_rev = request.query_params.get("old", "")
        new_rev = request.query_params.get("new", "")
        if history and not old_rev and not new_rev and len(history) >= 2:
            new_rev = history[0].revision.short_sha
            old_rev = history[1].revision.short_sha
        view = service.work_summary(path)
        if view is None:
            return HTMLResponse("Not found", status_code=404)
        site = get_site()
        return render(
            request,
            "history.html",
            {
                "work": view,
                "history": history,
                "service": service,
                "old_rev": old_rev,
                "new_rev": new_rev,
                "show_history_details": site.public_history or is_admin(request),
            },
        )

    async def work_history_compare(request: Request) -> Response:
        slug = request.path_params["slug"]
        redirected = slug_redirect_response(request, slug)
        if redirected:
            return redirected
        service = get_service()
        path = service.resolve_slug(slug)
        if not path or not service.is_published(path):
            return HTMLResponse("Not found", status_code=404)
        old_rev = request.query_params.get("old", "")
        new_rev = request.query_params.get("new", "")
        if not old_rev or not new_rev:
            return RedirectResponse(f"/works/{slug}/history", status_code=303)
        diff_view = request.query_params.get("diff_view", "immersive")
        if diff_view not in ("immersive", "split", "unified"):
            diff_view = "immersive"
        old_sha = repo.resolve_sha(old_rev)
        new_sha = repo.resolve_sha(new_rev)
        admin = is_admin(request)
        if not old_sha or not new_sha:
            return RedirectResponse(f"/works/{slug}/history", status_code=303)
        if not service.can_view_revision(path, old_sha, admin=admin):
            return HTMLResponse("Not found", status_code=404)
        if not service.can_view_revision(path, new_sha, admin=admin):
            return HTMLResponse("Not found", status_code=404)
        old_text = service.revision_blob_text(path, old_sha) or ""
        new_text = service.revision_blob_text(path, new_sha) or ""
        old_body = extract_work_body(old_text)
        new_body = extract_work_body(new_text)
        byte_summary = format_compare_byte_summary(diff_byte_stats(old_body, new_body))
        diff_html = render_diff_html(old_body, new_body, view=diff_view)
        view = service.work_summary(path)
        if view is None:
            return HTMLResponse("Not found", status_code=404)
        return render(
            request,
            "compare.html",
            {
                "work": view,
                "service": service,
                "old_rev": old_rev,
                "new_rev": new_rev,
                "diff_view": diff_view,
                "diff_html": diff_html,
                "byte_summary": byte_summary,
            },
        )

    async def work_compare_redirect(request: Request) -> Response:
        slug = request.path_params["slug"]
        redirected = slug_redirect_response(request, slug)
        if redirected:
            return redirected
        old_rev = request.query_params.get("old", "")
        new_rev = request.query_params.get("new", "")
        query = ""
        if old_rev or new_rev:
            parts = []
            if old_rev:
                parts.append(f"old={old_rev}")
            if new_rev:
                parts.append(f"new={new_rev}")
            query = "?" + "&".join(parts)
        return RedirectResponse(f"/works/{slug}/history/compare{query}", status_code=301)

    async def work_pdf(request: Request) -> Response:
        slug = request.path_params["slug"]
        redirected = slug_redirect_response(request, slug)
        if redirected:
            return redirected
        script_id = request.path_params["kind"]
        pdf_options = discover_pdf_options(settings.pdf_scripts)
        valid_ids = {opt.id for opt in pdf_options}
        if script_id not in valid_ids or settings.pdf_scripts is None:
            return HTMLResponse("Not found", status_code=404)
        service = get_service()
        path = service.resolve_slug(slug)
        if not path or not service.is_published(path):
            return HTMLResponse("Not found", status_code=404)
        rev = request.query_params.get("rev")
        sha = repo.resolve_sha(rev) if rev else repo.head_sha()
        if not sha or not service.can_view_revision(path, sha, admin=is_admin(request)):
            return HTMLResponse("Not found", status_code=404)
        text = service.revision_blob_text(path, sha) if rev else repo.get_blob_text(path, sha)
        if text is None:
            return HTMLResponse("Not found", status_code=404)
        committed_at = repo.commit_date(sha)
        rev_label = f"Rev: {format_rev_date(committed_at)}" if committed_at else ""
        site = get_site()
        try:
            pdf_path = generate_pdf(
                markdown_text=text,
                script_id=script_id,
                pdf_scripts_dir=settings.pdf_scripts,
                cache_dir=settings.pdf_cache_dir,
                commit_sha=sha,
                path=path,
                author=service.work_author(path),
                rev_label=rev_label,
                blurb_fields=site.blurb_fields,
            )
        except Exception:
            logger.exception("PDF generation failed for %s", path)
            return HTMLResponse("PDF generation failed.", status_code=500)
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=pdf_path.name,
        )

    async def setup_get(request: Request) -> Response:
        if get_secrets().is_configured:
            return RedirectResponse("/", status_code=303)
        site = get_site()
        return render(
            request,
            "setup.html",
            {"suggested_public_url": request_origin(request, site)},
        )

    async def setup_post(request: Request) -> Response:
        if get_secrets().is_configured:
            return RedirectResponse("/", status_code=303)
        form = await get_form(request)
        password = str(form.get("admin_password", ""))
        confirm = str(form.get("admin_password_confirm", ""))
        public_url = normalize_public_url(str(form.get("public_url", "")))
        if len(password) < 8:
            return render(
                request,
                "setup.html",
                {
                    "error": "Admin password must be at least 8 characters.",
                    "suggested_public_url": public_url,
                },
                status_code=400,
            )
        if password != confirm:
            return render(
                request,
                "setup.html",
                {
                    "error": "Passwords do not match.",
                    "suggested_public_url": public_url,
                },
                status_code=400,
            )
        if not public_url:
            return render(
                request,
                "setup.html",
                {
                    "error": "Public site URL is required.",
                    "suggested_public_url": request_origin(request, get_site()),
                },
                status_code=400,
            )
        current = get_secrets()
        set_admin_password(settings.secrets_path, current, password)
        site = get_site()
        site.public_url = public_url
        save_site_config(settings.site_config_path, site)
        login_admin(request, current, password)
        return RedirectResponse("/admin", status_code=303)

    async def login_get(request: Request) -> Response:
        if is_admin(request):
            return RedirectResponse("/admin", status_code=303)
        return render(request, "login.html", {})

    async def login_post(request: Request) -> Response:
        form = await get_form(request)
        password = str(form.get("password", ""))
        if login_admin(request, get_secrets(), password):
            return RedirectResponse("/admin", status_code=303)
        return render(request, "login.html", {"error": "Wrong password."})

    async def logout(request: Request) -> Response:
        logout_admin(request)
        return RedirectResponse("/", status_code=303)

    async def theme_post(request: Request) -> Response:
        form = await get_form(request)
        theme = str(form.get("theme", ""))
        next_url = str(form.get("next", "/"))
        return set_theme_response(
            theme=theme,
            next_url=next_url,
            secure=request_is_secure(request, env_https_enabled=settings.https_enabled),
        )

    async def admin_index(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        return render(request, "admin/index.html", {})

    async def admin_publish_get(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        site = get_site()
        paths = repo.list_markdown_paths()
        directories = sorted({str(Path(p).parent) for p in paths if "/" in p})
        return render(
            request,
            "admin/publish.html",
            {
                "paths": paths,
                "directories": directories,
                "published_paths": site.published_paths,
                "published_directories": site.published_directories,
            },
        )

    async def admin_publish_post(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        form = await get_form(request)
        site = get_site()
        selected = set(form.getlist("path"))
        directories = set(form.getlist("directory"))
        site.published_paths = selected
        site.published_directories = directories
        save_site_config(settings.site_config_path, site)
        return RedirectResponse("/admin/publish", status_code=303)

    async def admin_history_get(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        site = get_site()
        service = get_service()
        paths = repo.list_markdown_paths()
        selected = request.query_params.get("path", paths[0] if paths else "")
        history = repo.file_history(selected, follow=True) if selected else []
        suppressed = service.sanitize_suppressed(
            selected, site.suppressed_commits.get(selected, set())
        )
        latest_sha = history[0].sha if history else ""
        return render(
            request,
            "admin/history.html",
            {
                "paths": paths,
                "selected_path": selected,
                "history": history,
                "suppressed": suppressed,
                "latest_sha": latest_sha,
            },
        )

    async def admin_history_post(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        form = await get_form(request)
        path = str(form.get("path", ""))
        site = get_site()
        service = get_service()
        suppressed = set(form.getlist("suppressed"))
        if path:
            suppressed = service.sanitize_suppressed(path, suppressed)
            if suppressed:
                site.suppressed_commits[path] = suppressed
            elif path in site.suppressed_commits:
                del site.suppressed_commits[path]
        save_site_config(settings.site_config_path, site)
        return RedirectResponse(f"/admin/history?path={path}", status_code=303)

    async def admin_merge_get(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        site = get_site()
        paths = repo.list_markdown_paths()
        current = set(paths)
        historical_paths = [
            path for path in repo.list_historical_markdown_paths() if path not in current
        ]
        return render(
            request,
            "admin/merge.html",
            {
                "paths": paths,
                "historical_paths": historical_paths,
                "history_merges": site.history_merges,
                "slug_redirects": site.slug_redirects,
            },
        )

    async def admin_merge_post(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        form = await get_form(request)
        action = str(form.get("action", "merge"))
        source = str(form.get("source", ""))
        dest = str(form.get("dest", ""))
        site = get_site()
        service = get_service()
        if action == "unmerge":
            error = service.remove_history_merge(source=source, dest=dest)
        else:
            error = service.apply_history_merge(source=source, dest=dest)
        if error:
            paths = repo.list_markdown_paths()
            current = set(paths)
            historical_paths = [
                path for path in repo.list_historical_markdown_paths() if path not in current
            ]
            return render(
                request,
                "admin/merge.html",
                {
                    "paths": paths,
                    "historical_paths": historical_paths,
                    "history_merges": site.history_merges,
                    "slug_redirects": site.slug_redirects,
                    "error": error,
                    "source": source,
                    "dest": dest,
                },
                status_code=400,
            )
        save_site_config(settings.site_config_path, service.site)
        return RedirectResponse("/admin/merge", status_code=303)

    async def admin_flags_get(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        site = get_site()
        paths = repo.list_markdown_paths()
        flag_ids = sorted(site.flags)
        selected_flag = request.query_params.get("flag", "")
        if selected_flag not in site.flags:
            selected_flag = flag_ids[0] if flag_ids else ""
        return render(
            request,
            "admin/flags.html",
            {
                "paths": paths,
                "flags": site.flags,
                "work_flags": site.work_flags,
                "selected_flag": selected_flag,
                "error": request.query_params.get("error", ""),
            },
        )

    async def admin_flags_post(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        form = await get_form(request)
        site = get_site()
        action = str(form.get("action", "save"))

        if action == "add_flag":
            flag_id = normalize_flag_id(str(form.get("new_flag_id", "")))
            label = str(form.get("new_flag_label", "")).strip()
            color = str(form.get("new_flag_color", "")).strip()
            if not flag_id:
                return RedirectResponse("/admin/flags?error=invalid-id", status_code=303)
            if not label:
                label = flag_id.replace("-", " ").title()
            if flag_id in site.flags:
                return RedirectResponse("/admin/flags?error=exists", status_code=303)
            site.flags[flag_id] = FlagDef(label=label, color=color)
            save_site_config(settings.site_config_path, site)
            return RedirectResponse(f"/admin/flags?flag={flag_id}", status_code=303)

        if action == "remove_flag":
            flag_id = str(form.get("flag_id", ""))
            if flag_id in site.flags:
                del site.flags[flag_id]
                for path, path_flags in list(site.work_flags.items()):
                    site.work_flags[path] = [f for f in path_flags if f != flag_id]
                    if not site.work_flags[path]:
                        del site.work_flags[path]
            save_site_config(settings.site_config_path, site)
            remaining = sorted(site.flags)
            query = f"?flag={remaining[0]}" if remaining else ""
            return RedirectResponse(f"/admin/flags{query}", status_code=303)

        for flag_id, flag in list(site.flags.items()):
            label = str(form.get(f"label-{flag_id}", flag.label)).strip()
            color = str(form.get(f"color-{flag_id}", flag.color)).strip()
            if label:
                site.flags[flag_id] = FlagDef(label=label, color=color)

        paths = repo.list_markdown_paths()
        work_flags: dict[str, list[str]] = {
            path: [flag_id for flag_id in path_flags if flag_id in site.flags]
            for path, path_flags in site.work_flags.items()
        }
        work_flags = {path: flags for path, flags in work_flags.items() if flags}
        selected_flag = str(form.get("selected_flag", ""))
        if selected_flag in site.flags:
            assigned = set(form.getlist(f"assign-{selected_flag}"))
            for path in paths:
                path_flags = set(work_flags.get(path, []))
                if path in assigned:
                    path_flags.add(selected_flag)
                else:
                    path_flags.discard(selected_flag)
                if path_flags:
                    work_flags[path] = sorted(path_flags)
                elif path in work_flags:
                    del work_flags[path]
        site.work_flags = work_flags
        query = f"?flag={selected_flag}" if selected_flag in site.flags else ""
        save_site_config(settings.site_config_path, site)
        return RedirectResponse(f"/admin/flags{query}", status_code=303)

    async def admin_wip_redirect(request: Request) -> Response:
        return RedirectResponse("/admin/flags", status_code=301)

    async def admin_metadata_get(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        site = get_site()
        return render(
            request,
            "admin/metadata.html",
            {
                "blurb_fields": site.blurb_fields,
                "field_order": site.field_order,
                "discovered_fields": work_index.discovered_field_keys(),
            },
        )

    async def admin_metadata_post(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        form = await get_form(request)
        site = get_site()
        site.blurb_fields = parse_field_name_list(str(form.get("blurb_fields", ""))) or ["summary"]
        site.field_order = parse_field_name_list(str(form.get("field_order", "")))
        save_site_config(settings.site_config_path, site)
        return RedirectResponse("/admin/metadata", status_code=303)

    async def admin_continuity_get(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        site = get_site()
        service = get_service()
        stories = continuity_story_options(service)
        story_param = request.query_params.get("story", "")
        selected_slug, selected_path = resolve_admin_story(service, story_param)
        selected_previous: set[str] = set()
        selected_next: set[str] = set()
        if selected_path:
            selected_previous, selected_next = continuity_selection(site, selected_path)
        return render(
            request,
            "admin/continuity.html",
            {
                "stories": stories,
                "selected_slug": selected_slug,
                "selected_path": selected_path,
                "selected_previous": selected_previous,
                "selected_next": selected_next,
            },
        )

    async def admin_continuity_post(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        form = await get_form(request)
        site = get_site()
        service = get_service()
        story_path = str(form.get("story_path", ""))
        valid_paths = set(service.all_paths())
        if story_path in valid_paths:
            previous = [
                path
                for path in form.getlist("previous")
                if path in valid_paths and path != story_path
            ]
            next_paths = [
                path
                for path in form.getlist("next")
                if path in valid_paths and path != story_path
            ]
            if previous or next_paths:
                site.story_continuity[story_path] = StoryContinuity(
                    previous=previous,
                    next=next_paths,
                )
            elif story_path in site.story_continuity:
                del site.story_continuity[story_path]
        save_site_config(settings.site_config_path, site)
        query = admin_story_redirect(service, story_path)
        return RedirectResponse(f"/admin/continuity{query}", status_code=303)

    async def admin_crossposts_get(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        site = get_site()
        service = get_service()
        stories = continuity_story_options(service)
        story_param = request.query_params.get("story", "")
        selected_slug, selected_path = resolve_admin_story(service, story_param)
        selected_story = None
        if stories:
            if selected_path:
                selected_story = next(
                    (story for story in stories if story.path == selected_path),
                    None,
                )
            if selected_story is None:
                selected_story = stories[0]
                selected_slug = selected_story.slug
                selected_path = selected_story.path
        crosspost_rows: list[Crosspost | None] = []
        slots = 1
        add_row_url = ""
        fewer_row_url = ""
        if selected_path:
            saved = list(site.crossposts.get(selected_path, ()))
            min_slots = max(len(saved) + 1, 1)
            slots_param = request.query_params.get("slots", "").strip()
            slots = max(int(slots_param), min_slots) if slots_param.isdigit() else min_slots
            crosspost_rows = [
                saved[index] if index < len(saved) else None
                for index in range(slots)
            ]
            story_query = urlencode({"story": selected_slug})
            add_row_url = f"/admin/crossposts?{story_query}&slots={slots + 1}"
            fewer_row_url = (
                f"/admin/crossposts?{story_query}&slots={slots - 1}"
                if slots > min_slots
                else ""
            )
        return render(
            request,
            "admin/crossposts.html",
            {
                "stories": stories,
                "selected_story": selected_story,
                "selected_slug": selected_slug,
                "selected_path": selected_path,
                "crosspost_rows": crosspost_rows,
                "add_row_url": add_row_url,
                "fewer_row_url": fewer_row_url,
            },
        )

    async def admin_crossposts_post(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        form = await get_form(request)
        site = get_site()
        service = get_service()
        story_path = str(form.get("story_path", ""))
        valid_paths = set(service.all_paths())
        if story_path in valid_paths:
            items: list[Crosspost] = []
            for label, url in zip(
                form.getlist("crosspost_label"),
                form.getlist("crosspost_url"),
            ):
                label = str(label).strip()
                url = str(url).strip()
                if label and url:
                    items.append(Crosspost(label=label, url=url))
            if items:
                site.crossposts[story_path] = items
            elif story_path in site.crossposts:
                del site.crossposts[story_path]
        save_site_config(settings.site_config_path, site)
        query = admin_story_redirect(service, story_path)
        return RedirectResponse(f"/admin/crossposts{query}", status_code=303)

    async def admin_authorship_get(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        site = get_site()
        paths = sorted(repo.list_markdown_paths(), key=str.lower)
        return render(
            request,
            "admin/authorship.html",
            {
                "default_author": site.default_author,
                "paths": paths,
                "work_author_mode": site.work_author_mode,
                "work_author_override": site.work_author_override,
                "identities": repo.list_author_identities(),
                "author_aliases": site.author_aliases,
            },
        )

    async def admin_authorship_post(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        form = await get_form(request)
        site = get_site()
        site.default_author = str(form.get("default_author", "")).strip()
        identities = form.getlist("identity")
        aliases = form.getlist("alias")
        author_aliases: dict[str, str] = {}
        for identity, alias in zip(identities, aliases):
            alias = str(alias).strip()
            if alias:
                author_aliases[str(identity)] = alias
        site.author_aliases = author_aliases
        work_paths = form.getlist("work_path")
        work_modes = form.getlist("work_mode")
        work_overrides = form.getlist("work_override")
        work_author_mode: dict[str, str] = {}
        work_author_override: dict[str, str] = {}
        for path, mode, override in zip(work_paths, work_modes, work_overrides):
            path = str(path)
            if str(mode) == AUTHOR_MODE_EARLIEST:
                work_author_mode[path] = AUTHOR_MODE_EARLIEST
            override = str(override).strip()
            if override:
                work_author_override[path] = override
        site.work_author_mode = work_author_mode
        site.work_author_override = work_author_override
        save_site_config(settings.site_config_path, site)
        return RedirectResponse("/admin/authorship", status_code=303)

    async def admin_site_get(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        site = get_site()
        secrets_data = get_secrets()
        return render(
            request,
            "admin/site.html",
            {
                "site": site,
                "git_username": secrets_data.git_username,
                "git_remote_url": git_remote_url_display(site, request),
                "active_branch": repo.head_branch_name(),
            },
        )

    async def admin_site_post(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        form = await get_form(request)
        site = get_site()
        site.public_url = normalize_public_url(str(form.get("public_url", "")))
        site.site_title = str(form.get("site_title", "")).strip() or DEFAULT_SITE_TITLE
        site.stories_branch = str(form.get("stories_branch", "")).strip()
        site.show_login_link = "show_login_link" in form
        site.expose_unpublished_continuity_titles = (
            "expose_unpublished_continuity_titles" in form
        )
        site.public_history = "public_history" in form
        site.robots_noindex = "robots_noindex" in form
        git_username = str(form.get("git_username", "")).strip() or "git"
        secrets_data = get_secrets()
        secrets_data.git_username = git_username
        save_secrets(settings.secrets_path, secrets_data)
        save_site_config(settings.site_config_path, site)
        sync_repo_branch(site)
        return RedirectResponse("/admin/site", status_code=303)

    async def admin_security_get(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        revealed = ""
        if request.query_params.get("revealed") == "1":
            revealed = str(request.session.pop("revealed_git_password", ""))
        return render(
            request,
            "admin/security.html",
            {
                "revealed_git_password": revealed,
                "message": request.query_params.get("message", ""),
                "error": request.query_params.get("error", ""),
            },
        )

    async def admin_security_post(request: Request) -> Response:
        denied = require_admin(request)
        if denied:
            return denied
        form = await get_form(request)
        action = str(form.get("action", ""))
        secrets_data = get_secrets()

        if action == "regenerate_git_password":
            _, new_password = rotate_git_password(settings.secrets_path, secrets_data)
            request.session["revealed_git_password"] = new_password
            return RedirectResponse("/admin/security?revealed=1", status_code=303)

        if action == "rotate_sessions":
            rotate_session_secret(settings.secrets_path, secrets_data)
            logout_admin(request)
            return RedirectResponse(
                "/admin/security?message=Session+key+rotated.+Restart+the+service+to+invalidate+other+sessions.",
                status_code=303,
            )

        if action == "change_admin_password":
            current = str(form.get("current_password", ""))
            new_password = str(form.get("new_password", ""))
            confirm = str(form.get("new_password_confirm", ""))
            if not secrets_data.admin_password_hash or not verify_password(
                current, secrets_data.admin_password_hash
            ):
                return RedirectResponse(
                    "/admin/security?error=Current+password+is+incorrect.",
                    status_code=303,
                )
            if len(new_password) < 8:
                return RedirectResponse(
                    "/admin/security?error=New+password+must+be+at+least+8+characters.",
                    status_code=303,
                )
            if new_password != confirm:
                return RedirectResponse(
                    "/admin/security?error=New+passwords+do+not+match.",
                    status_code=303,
                )
            set_admin_password(settings.secrets_path, secrets_data, new_password)
            return RedirectResponse(
                "/admin/security?message=Admin+password+updated.",
                status_code=303,
            )

        return RedirectResponse("/admin/security", status_code=303)

    def on_git_receive() -> None:
        repo.invalidate()
        site = get_site()
        if not settings.stories_branch and not site.stories_branch.strip():
            branch = repo.head_branch_name()
            if branch:
                repo._branch = branch
        sync_repo_branch(site)
        work_index.rebuild()

    git_wsgi = AuthenticatedGitApp(
        repo,
        lambda: get_secrets().git_username,
        lambda: get_secrets().git_password,
        on_receive=on_git_receive,
    )

    routes = [
        Route(SETUP_PATH, setup_get, methods=["GET"]),
        Route(SETUP_PATH, setup_post, methods=["POST"]),
        Route("/", index),
        Route("/works/{slug:path}/r/{rev}", work_revision),
        Route("/works/{slug:path}/history/compare", work_history_compare),
        Route("/works/{slug:path}/history", work_history),
        Route("/works/{slug:path}/compare", work_compare_redirect),
        Route("/works/{slug:path}/pdf/{kind}", work_pdf),
        Route("/works/{slug:path}", work_view),
        Route("/login", login_get, methods=["GET"]),
        Route("/login", login_post, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/theme", theme_post, methods=["POST"]),
        Route("/admin", admin_index),
        Route("/admin/site", admin_site_get, methods=["GET"]),
        Route("/admin/site", admin_site_post, methods=["POST"]),
        Route("/admin/security", admin_security_get, methods=["GET"]),
        Route("/admin/security", admin_security_post, methods=["POST"]),
        Route("/admin/publish", admin_publish_get, methods=["GET"]),
        Route("/admin/publish", admin_publish_post, methods=["POST"]),
        Route("/admin/history", admin_history_get, methods=["GET"]),
        Route("/admin/history", admin_history_post, methods=["POST"]),
        Route("/admin/merge", admin_merge_get, methods=["GET"]),
        Route("/admin/merge", admin_merge_post, methods=["POST"]),
        Route("/admin/flags", admin_flags_get, methods=["GET"]),
        Route("/admin/flags", admin_flags_post, methods=["POST"]),
        Route("/admin/metadata", admin_metadata_get, methods=["GET"]),
        Route("/admin/metadata", admin_metadata_post, methods=["POST"]),
        Route("/admin/wip", admin_wip_redirect),
        Route("/admin/continuity", admin_continuity_get, methods=["GET"]),
        Route("/admin/continuity", admin_continuity_post, methods=["POST"]),
        Route("/admin/crossposts", admin_crossposts_get, methods=["GET"]),
        Route("/admin/crossposts", admin_crossposts_post, methods=["POST"]),
        Route("/admin/authorship", admin_authorship_get, methods=["GET"]),
        Route("/admin/authorship", admin_authorship_post, methods=["POST"]),
        Mount(mount_path_for_git(), app=WSGIMiddleware(git_wsgi)),
        Mount("/static", app=StaticFiles(directory=str(settings.static_dir)), name="static"),
    ]

    https_only = settings.https_enabled if settings.https_enabled is not None else False

    starlette_app = Starlette(
        routes=routes,
        middleware=[
            Middleware(
                SessionMiddleware,
                secret_key=settings.session_secret,
                same_site="lax",
                https_only=https_only,
            ),
            Middleware(CSRFMiddleware),
        ],
        exception_handlers={Exception: unhandled_exception},
    )
    starlette_app.state.libmyown = state
    app = SetupRequiredMiddleware(
        starlette_app, is_configured=lambda: get_secrets().is_configured
    )
    app = ForwardedProtoMiddleware(app)
    return app
