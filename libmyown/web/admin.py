from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from libmyown.auth import logout_admin
from libmyown.authorship import (
    AUTHOR_MODE_DEFAULT,
    AUTHOR_MODE_EARLIEST,
    is_redundant_exception,
    list_work_exceptions,
)
from libmyown.cache_tools import clear_pdf_cache, clear_runtime_caches, format_bytes, pdf_cache_stats
from libmyown.content import format_datetime, parse_field_name_list
from libmyown.continuity import continuity_selection, continuity_story_options
from libmyown.git_repo import path_to_slug
from libmyown.request_url import normalize_public_url, request_origin
from libmyown.secrets import (
    revoke_admin_sessions,
    rotate_git_password,
    save_secrets,
    set_admin_password,
    verify_password,
)
from libmyown.service import LibraryService
from libmyown.site_config import (
    AbortEdit,
    Crosspost,
    DEFAULT_SITE_TITLE,
    FlagDef,
    SiteConfig,
    StoryContinuity,
    normalize_flag_color,
    normalize_flag_id,
)
from libmyown.web.common import admin_page, flash, form_post, redirect, render
from libmyown.web.state import AppState, effective_branch, get_state

MIN_PASSWORD_LENGTH = 8


def _resolve_story(service: LibraryService, story_param: str) -> tuple[str, str | None]:
    if not story_param:
        return "", None
    path = service.resolve_slug(story_param)
    if path:
        return path_to_slug(path) or story_param, path
    if story_param in service.all_paths():
        return path_to_slug(story_param) or "", story_param
    return story_param, None


def _story_redirect(base: str, story_path: str) -> RedirectResponse:
    return redirect(base, story=path_to_slug(story_path) if story_path else "")


def _git_remote_url(state: AppState, site: SiteConfig, request: Request) -> str:
    host = request_origin(request, site).removeprefix("https://").removeprefix("http://")
    return f"https://{state.secrets.git_username}@{host}/git/stories.git"


# -- index -------------------------------------------------------------------------


@admin_page
def admin_index(request: Request) -> Response:
    return render(request, "admin/index.html", {})


def admin_wip_redirect(request: Request) -> Response:
    return RedirectResponse("/admin/flags", status_code=301)


# -- publish -----------------------------------------------------------------------


@admin_page
def admin_publish_get(request: Request) -> Response:
    state = get_state(request)
    site = state.site()
    paths = state.snapshot().paths
    return render(
        request,
        "admin/publish.html",
        {
            "paths": paths,
            "directories": sorted({str(Path(p).parent) for p in paths if "/" in p}),
            "published_paths": site.published_paths,
            "published_directories": site.published_directories,
        },
    )


@form_post(admin=True)
def admin_publish_post(request: Request, form: FormData) -> Response:
    with get_state(request).config.edit() as site:
        site.published_paths = {str(p) for p in form.getlist("path")}
        site.published_directories = {str(d) for d in form.getlist("directory")}
    return RedirectResponse("/admin/publish", status_code=303)


# -- history suppression -------------------------------------------------------------


@admin_page
def admin_history_get(request: Request) -> Response:
    state = get_state(request)
    site = state.site()
    service = state.service(site)
    paths = service.all_paths()
    selected = request.query_params.get("path", paths[0] if paths else "")
    history = service.snapshot.file_history(selected, follow=True) if selected else []
    return render(
        request,
        "admin/history.html",
        {
            "paths": paths,
            "selected_path": selected,
            "history": history,
            "suppressed": service.sanitize_suppressed(
                selected, site.suppressed_commits.get(selected, set())
            ),
            "latest_sha": history[0].sha if history else "",
        },
    )


@form_post(admin=True)
def admin_history_post(request: Request, form: FormData) -> Response:
    state = get_state(request)
    path = str(form.get("path", ""))
    if path:
        with state.config.edit() as site:
            suppressed = state.service(site).sanitize_suppressed(
                path, {str(sha) for sha in form.getlist("suppressed")}
            )
            if suppressed:
                site.suppressed_commits[path] = suppressed
            else:
                site.suppressed_commits.pop(path, None)
    return redirect("/admin/history", path=path)


# -- history merges ------------------------------------------------------------------


def _merge_context(state: AppState, site: SiteConfig) -> dict:
    snapshot = state.snapshot()
    current = set(snapshot.paths)
    return {
        "paths": snapshot.paths,
        "historical_paths": [p for p in snapshot.historical_paths() if p not in current],
        "history_merges": site.history_merges,
        "slug_redirects": site.slug_redirects,
    }


@admin_page
def admin_merge_get(request: Request) -> Response:
    state = get_state(request)
    return render(request, "admin/merge.html", _merge_context(state, state.site()))


@form_post(admin=True)
def admin_merge_post(request: Request, form: FormData) -> Response:
    state = get_state(request)
    action = str(form.get("action", "merge"))
    source = str(form.get("source", ""))
    dest = str(form.get("dest", ""))
    try:
        with state.config.edit() as site:
            service = state.service(site)
            if action == "unmerge":
                error = service.remove_history_merge(source=source, dest=dest)
            else:
                error = service.apply_history_merge(source=source, dest=dest)
            if error:
                raise AbortEdit(error)
    except AbortEdit as exc:
        context = _merge_context(state, state.site())
        context.update({"error": str(exc), "source": source, "dest": dest})
        return render(request, "admin/merge.html", context, status_code=400)
    return RedirectResponse("/admin/merge", status_code=303)


# -- flags -------------------------------------------------------------------------------


@admin_page
def admin_flags_get(request: Request) -> Response:
    state = get_state(request)
    site = state.site()
    flag_ids = sorted(site.flags)
    selected_flag = request.query_params.get("flag", "")
    if selected_flag not in site.flags:
        selected_flag = flag_ids[0] if flag_ids else ""
    return render(
        request,
        "admin/flags.html",
        {
            "paths": state.snapshot().paths,
            "flags": site.flags,
            "work_flags": site.work_flags,
            "selected_flag": selected_flag,
        },
    )


@form_post(admin=True)
def admin_flags_post(request: Request, form: FormData) -> Response:
    state = get_state(request)
    action = str(form.get("action", "save"))

    if action == "add_flag":
        flag_id = normalize_flag_id(str(form.get("new_flag_id", "")))
        if not flag_id:
            flash(request, "Invalid flag id.", error=True)
            return redirect("/admin/flags")
        label = str(form.get("new_flag_label", "")).strip() or flag_id.replace("-", " ").title()
        color = normalize_flag_color(str(form.get("new_flag_color", "")))
        try:
            with state.config.edit() as site:
                if flag_id in site.flags:
                    raise AbortEdit()
                site.flags[flag_id] = FlagDef(label=label, color=color)
        except AbortEdit:
            flash(request, "Flag already exists.", error=True)
            return redirect("/admin/flags")
        return redirect("/admin/flags", flag=flag_id)

    if action == "remove_flag":
        flag_id = str(form.get("flag_id", ""))
        with state.config.edit() as site:
            if flag_id in site.flags:
                del site.flags[flag_id]
                for path, path_flags in list(site.work_flags.items()):
                    remaining_flags = [f for f in path_flags if f != flag_id]
                    if remaining_flags:
                        site.work_flags[path] = remaining_flags
                    else:
                        del site.work_flags[path]
            remaining = sorted(site.flags)
        return redirect("/admin/flags", flag=remaining[0] if remaining else "")

    paths = state.snapshot().paths
    selected_flag = str(form.get("selected_flag", ""))
    with state.config.edit() as site:
        for flag_id, flag in list(site.flags.items()):
            label = str(form.get(f"label-{flag_id}", flag.label)).strip()
            color = normalize_flag_color(str(form.get(f"color-{flag_id}", flag.color)))
            if label:
                site.flags[flag_id] = FlagDef(label=label, color=color)
        work_flags = {
            path: [flag_id for flag_id in path_flags if flag_id in site.flags]
            for path, path_flags in site.work_flags.items()
        }
        work_flags = {path: flags for path, flags in work_flags.items() if flags}
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
                else:
                    work_flags.pop(path, None)
        site.work_flags = work_flags
        flag_param = selected_flag if selected_flag in site.flags else ""
    return redirect("/admin/flags", flag=flag_param)


# -- metadata ------------------------------------------------------------------------------


@admin_page
def admin_metadata_get(request: Request) -> Response:
    state = get_state(request)
    site = state.site()
    return render(
        request,
        "admin/metadata.html",
        {
            "blurb_fields": site.blurb_fields,
            "field_order": site.field_order,
            "discovered_fields": state.snapshot().discovered_field_keys(),
        },
    )


@form_post(admin=True)
def admin_metadata_post(request: Request, form: FormData) -> Response:
    with get_state(request).config.edit() as site:
        site.blurb_fields = parse_field_name_list(str(form.get("blurb_fields", ""))) or ["summary"]
        site.field_order = parse_field_name_list(str(form.get("field_order", "")))
    return RedirectResponse("/admin/metadata", status_code=303)


# -- continuity ------------------------------------------------------------------------------


@admin_page
def admin_continuity_get(request: Request) -> Response:
    state = get_state(request)
    site = state.site()
    service = state.service(site)
    selected_slug, selected_path = _resolve_story(service, request.query_params.get("story", ""))
    selected_previous: set[str] = set()
    selected_next: set[str] = set()
    if selected_path:
        selected_previous, selected_next = continuity_selection(site, selected_path)
    return render(
        request,
        "admin/continuity.html",
        {
            "stories": continuity_story_options(service),
            "selected_slug": selected_slug,
            "selected_path": selected_path,
            "selected_previous": selected_previous,
            "selected_next": selected_next,
        },
    )


@form_post(admin=True)
def admin_continuity_post(request: Request, form: FormData) -> Response:
    state = get_state(request)
    story_path = str(form.get("story_path", ""))
    valid_paths = set(state.snapshot().paths)
    if story_path in valid_paths:
        previous = [
            str(p) for p in form.getlist("previous") if p in valid_paths and p != story_path
        ]
        next_paths = [str(p) for p in form.getlist("next") if p in valid_paths and p != story_path]
        with state.config.edit() as site:
            if previous or next_paths:
                site.story_continuity[story_path] = StoryContinuity(
                    previous=previous, next=next_paths
                )
            else:
                site.story_continuity.pop(story_path, None)
    return _story_redirect("/admin/continuity", story_path)


# -- crossposts --------------------------------------------------------------------------------


@admin_page
def admin_crossposts_get(request: Request) -> Response:
    state = get_state(request)
    site = state.site()
    service = state.service(site)
    stories = continuity_story_options(service)
    selected_slug, selected_path = _resolve_story(service, request.query_params.get("story", ""))
    selected_story = None
    if stories:
        if selected_path:
            selected_story = next((s for s in stories if s.path == selected_path), None)
        if selected_story is None:
            selected_story = stories[0]
            selected_slug = selected_story.slug
            selected_path = selected_story.path
    crosspost_rows: list[Crosspost | None] = []
    add_row_url = ""
    fewer_row_url = ""
    if selected_path:
        saved = list(site.crossposts.get(selected_path, ()))
        min_slots = len(saved) + 1
        slots_param = request.query_params.get("slots", "").strip()
        slots = max(int(slots_param), min_slots) if slots_param.isdigit() else min_slots
        slots = min(slots, min_slots + 50)
        crosspost_rows = [saved[i] if i < len(saved) else None for i in range(slots)]
        add_row_url = f"/admin/crossposts?{urlencode({'story': selected_slug, 'slots': slots + 1})}"
        if slots > min_slots:
            fewer_row_url = (
                f"/admin/crossposts?{urlencode({'story': selected_slug, 'slots': slots - 1})}"
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


@form_post(admin=True)
def admin_crossposts_post(request: Request, form: FormData) -> Response:
    state = get_state(request)
    story_path = str(form.get("story_path", ""))
    if story_path in set(state.snapshot().paths):
        items = [
            Crosspost(label=str(label).strip(), url=str(url).strip())
            for label, url in zip(form.getlist("crosspost_label"), form.getlist("crosspost_url"))
            if str(label).strip() and str(url).strip()
        ]
        with state.config.edit() as site:
            if items:
                site.crossposts[story_path] = items
            else:
                site.crossposts.pop(story_path, None)
    return _story_redirect("/admin/crossposts", story_path)


# -- authorship ---------------------------------------------------------------------------------


@admin_page
def admin_authorship_get(request: Request) -> Response:
    state = get_state(request)
    site = state.site()
    snapshot = state.snapshot()
    exceptions = list_work_exceptions(site.work_author_mode, site.work_author_override)
    exception_paths = {item["path"] for item in exceptions}
    return render(
        request,
        "admin/authorship.html",
        {
            "default_author": site.default_author,
            "default_author_rule": site.default_author_rule,
            "work_paths": sorted(snapshot.paths, key=str.lower),
            "exception_paths": exception_paths,
            "exceptions": exceptions,
            "identities": snapshot.author_identities(),
            "author_aliases": site.author_aliases,
        },
    )


@form_post(admin=True)
def admin_authorship_post(request: Request, form: FormData) -> Response:
    state = get_state(request)
    action = str(form.get("action", "save"))

    if action == "add_exception":
        path = str(form.get("exception_path", "")).strip()
        mode = str(form.get("exception_mode", "")).strip()
        custom = str(form.get("exception_custom", "")).strip()
        if path in set(state.snapshot().paths):
            with state.config.edit() as site:
                site.work_author_mode.pop(path, None)
                site.work_author_override.pop(path, None)
                if mode == "custom" and custom:
                    site.work_author_override[path] = custom
                elif mode in (AUTHOR_MODE_DEFAULT, AUTHOR_MODE_EARLIEST):
                    if not is_redundant_exception(mode, site.default_author_rule):
                        site.work_author_mode[path] = mode
        return RedirectResponse("/admin/authorship", status_code=303)

    if action == "remove_exception":
        path = str(form.get("exception_path", "")).strip()
        with state.config.edit() as site:
            site.work_author_mode.pop(path, None)
            site.work_author_override.pop(path, None)
        return RedirectResponse("/admin/authorship", status_code=303)

    rule = str(form.get("default_author_rule", AUTHOR_MODE_EARLIEST)).strip()
    with state.config.edit() as site:
        site.default_author_rule = (
            AUTHOR_MODE_EARLIEST if rule == AUTHOR_MODE_EARLIEST else AUTHOR_MODE_DEFAULT
        )
        site.default_author = str(form.get("default_author", "")).strip()
        site.author_aliases = {
            str(identity): str(alias).strip()
            for identity, alias in zip(form.getlist("identity"), form.getlist("alias"))
            if str(alias).strip()
        }
        for path in list(site.work_author_mode):
            if is_redundant_exception(site.work_author_mode[path], site.default_author_rule):
                del site.work_author_mode[path]
    return RedirectResponse("/admin/authorship", status_code=303)


# -- site settings ---------------------------------------------------------------------------------


@admin_page
def admin_site_get(request: Request) -> Response:
    state = get_state(request)
    site = state.site()
    branches = state.store.list_branch_names()
    missing = state.missing_branch()
    selected = effective_branch(site) or state.store.default_branch() or ""
    serving = None
    snapshot = state.snapshot()
    if snapshot.head_sha and not missing:
        committed_at = state.store.commit_date(snapshot.head_sha)
        serving = {
            "branch": selected,
            "short_sha": snapshot.head_sha[:7],
            "date": format_datetime(committed_at) if committed_at else "",
        }
    return render(
        request,
        "admin/site.html",
        {
            "site": site,
            "git_username": state.secrets.git_username,
            "git_remote_url": _git_remote_url(state, site, request),
            "branches": branches,
            "selected_branch": selected,
            "serving": serving,
        },
    )


@form_post(admin=True)
def admin_site_post(request: Request, form: FormData) -> Response:
    state = get_state(request)
    git_username = str(form.get("git_username", "")).strip() or "git"
    branch = str(form.get("stories_branch", "")).strip()
    current = effective_branch(state.site()) or ""
    # Only existing branches can be chosen; an unchanged (possibly missing) value is kept as is.
    if branch != current and not state.store.set_default_branch(branch):
        flash(request, f"Branch {branch!r} does not exist.", error=True)
        return redirect("/admin/site")
    with state.config.edit() as site:
        site.public_url = normalize_public_url(str(form.get("public_url", "")))
        site.site_title = str(form.get("site_title", "")).strip() or DEFAULT_SITE_TITLE
        site.stories_branch = branch
        site.show_login_link = "show_login_link" in form
        site.expose_unpublished_continuity_titles = "expose_unpublished_continuity_titles" in form
        site.public_history = "public_history" in form
        site.robots_noindex = "robots_noindex" in form
        site.git_username = git_username
    if state.secrets.git_username != git_username:
        state.secrets.git_username = git_username
        save_secrets(state.settings.secrets_path, state.secrets)
    if (branch or None) != state.store.branch:
        state.worker.request()
    flash(request, "Settings saved.")
    return redirect("/admin/site")


# -- security ----------------------------------------------------------------------------------------


def _security_page(request: Request, *, revealed_git_password: str = "") -> Response:
    return render(
        request, "admin/security.html", {"revealed_git_password": revealed_git_password}
    )


@admin_page
def admin_security_get(request: Request) -> Response:
    return _security_page(request)


@form_post(admin=True)
def admin_security_post(request: Request, form: FormData) -> Response:
    state = get_state(request)
    secrets_path = state.settings.secrets_path
    action = str(form.get("action", ""))

    if action == "regenerate_git_password":
        _, new_password = rotate_git_password(secrets_path, state.secrets)
        # Rendered directly (never stored in the signed-but-readable session cookie).
        return _security_page(request, revealed_git_password=new_password)

    if action == "rotate_sessions":
        revoke_admin_sessions(secrets_path, state.secrets)
        logout_admin(request)
        return redirect("/login")

    if action == "change_admin_password":
        current = str(form.get("current_password", ""))
        new_password = str(form.get("new_password", ""))
        confirm = str(form.get("new_password_confirm", ""))
        stored = state.secrets.admin_password_hash
        if not stored or not verify_password(current, stored):
            flash(request, "Current password is incorrect.", error=True)
        elif len(new_password) < MIN_PASSWORD_LENGTH:
            flash(
                request,
                f"New password must be at least {MIN_PASSWORD_LENGTH} characters.",
                error=True,
            )
        elif new_password != confirm:
            flash(request, "New passwords do not match.", error=True)
        else:
            set_admin_password(secrets_path, state.secrets, new_password)
            flash(request, "Admin password updated.")
        return redirect("/admin/security")

    return RedirectResponse("/admin/security", status_code=303)


# -- maintenance -----------------------------------------------------------------------------------


def _last_gc_summary(record: dict | None) -> dict | None:
    if not record:
        return None
    before = record.get("before") or {}
    after = record.get("after") or {}
    return {
        "at": str(record.get("at", "")).replace("T", " ").replace("+00:00", " UTC"),
        "method": record.get("method", ""),
        "seconds": record.get("seconds", ""),
        "before_size": format_bytes(int(before.get("bytes", 0))),
        "after_size": format_bytes(int(after.get("bytes", 0))),
    }


@admin_page
def admin_maintenance_get(request: Request) -> Response:
    state = get_state(request)
    stats = pdf_cache_stats(state.settings.pdf_cache_dir)
    repo_stats = state.store.storage_stats()
    return render(
        request,
        "admin/maintenance.html",
        {
            "pdf_cache_count": stats.pdf_count,
            "pdf_cache_size": format_bytes(stats.bytes_on_disk),
            "repo_packs": repo_stats.packs,
            "repo_loose": repo_stats.loose_objects,
            "repo_size": format_bytes(repo_stats.bytes),
            "last_gc": _last_gc_summary(state.worker.last_gc()),
        },
    )


@form_post(admin=True)
def admin_maintenance_post(request: Request, form: FormData) -> Response:
    state = get_state(request)
    action = str(form.get("action", ""))
    if action == "clear_pdf_cache":
        clear_pdf_cache(state.settings.pdf_cache_dir)
        text = "PDF cache cleared."
    elif action == "clear_runtime_caches":
        clear_runtime_caches(state.store, state.snapshots)
        text = "Runtime caches cleared."
    elif action == "run_git_gc":
        state.worker.request_gc()
        text = "Collecting all your garbage in the background. Reload in a moment to see the result."
    else:
        return RedirectResponse("/admin/maintenance", status_code=303)
    flash(request, text)
    return redirect("/admin/maintenance")


routes = [
    Route("/admin", admin_index),
    Route("/admin/site", admin_site_get, methods=["GET"]),
    Route("/admin/site", admin_site_post, methods=["POST"]),
    Route("/admin/security", admin_security_get, methods=["GET"]),
    Route("/admin/security", admin_security_post, methods=["POST"]),
    Route("/admin/maintenance", admin_maintenance_get, methods=["GET"]),
    Route("/admin/maintenance", admin_maintenance_post, methods=["POST"]),
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
]
