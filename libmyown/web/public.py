from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from libmyown.auth import is_admin
from libmyown.content import format_rev_date
from libmyown.continuity import continuity_for_work
from libmyown.pdf import PdfBusy
from libmyown.service import DiffTooLarge, LibraryService, WorkView
from libmyown.site_config import HOME_LABEL, SiteConfig, StoryContinuity
from libmyown.web.common import message, not_found, redirect, render
from libmyown.web.state import AppState, get_state

logger = logging.getLogger(__name__)

DIFF_VIEWS = ("immersive", "split", "unified")


def _work_page_context(
    state: AppState, service: LibraryService, site: SiteConfig, path: str, view: WorkView
) -> dict:
    links = site.story_continuity.get(path, StoryContinuity())
    linked_paths = set(links.previous) | set(links.next)
    breadcrumb_items: list[tuple[str, str | None]] = [
        (HOME_LABEL, "/"),
        (view.meta.title, f"/works/{view.slug}"),
    ]
    if view.at_revision:
        breadcrumb_items.append((view.short_sha, None))
    return {
        "work": view,
        "related_works": [
            work for work in service.related_works(path) if work.path not in linked_paths
        ],
        "continuity": continuity_for_work(site, service, path),
        "pdf_options": state.pdf.options(),
        "work_blurb": view.meta.blurb(site.blurb_fields),
        "meta_rows": view.meta.display_rows(site.blurb_fields, site.field_order or None),
        "crossposts": site.crossposts_for(path),
        "breadcrumb_items": breadcrumb_items,
    }


def _slug_redirect(request: Request, service: LibraryService, slug: str) -> Response | None:
    canonical = service.canonical_slug(slug)
    if canonical == slug:
        return None
    suffix = request.url.path.removeprefix(f"/works/{slug}")
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/works/{canonical}{suffix}{query}", status_code=301)


def _published_path(request: Request, service: LibraryService) -> tuple[str | None, Response | None]:
    slug = request.path_params["slug"]
    redirected = _slug_redirect(request, service, slug)
    if redirected:
        return None, redirected
    path = service.resolve_slug(slug)
    if not path or not service.is_published(path):
        return None, not_found()
    return path, None


def robots_txt(request: Request) -> FileResponse:
    return FileResponse(
        get_state(request).settings.static_dir / "robots.txt",
        media_type="text/plain; charset=utf-8",
    )


def index(request: Request) -> Response:
    service = get_state(request).service()
    return render(request, "index.html", {"works": service.published_works()})


def work_view(request: Request) -> Response:
    state = get_state(request)
    site = state.site()
    service = state.service(site)
    path, early = _published_path(request, service)
    if early:
        return early
    view = service.work_at(path)
    if view is None:
        return not_found()
    return render(request, "work.html", _work_page_context(state, service, site, path, view))


def work_revision(request: Request) -> Response:
    state = get_state(request)
    site = state.site()
    service = state.service(site)
    slug = request.path_params["slug"]
    redirected = _slug_redirect(request, service, slug)
    if redirected:
        return redirected
    path = service.resolve_slug(slug)
    if not path:
        return not_found()
    admin = is_admin(request)
    revision = service.resolve_revision(path, request.path_params["rev"], admin=admin)
    if revision is None:
        return message(request, "Revision unavailable", "This revision is not available.", 404)
    view = service.work_at(path, revision)
    if view is None:
        return not_found()
    return render(request, "work.html", _work_page_context(state, service, site, path, view))


def work_history(request: Request) -> Response:
    state = get_state(request)
    site = state.site()
    service = state.service(site)
    path, early = _published_path(request, service)
    if early:
        return early
    history = service.visible_history(path)
    old_rev = request.query_params.get("old", "")
    new_rev = request.query_params.get("new", "")
    if not old_rev and not new_rev and len(history) >= 2:
        new_rev = history[0].revision.short_sha
        old_rev = history[1].revision.short_sha
    view = service.work_summary(path)
    if view is None:
        return not_found()
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


def work_history_compare(request: Request) -> Response:
    state = get_state(request)
    service = state.service()
    path, early = _published_path(request, service)
    if early:
        return early
    slug = request.path_params["slug"]
    old_rev = request.query_params.get("old", "")
    new_rev = request.query_params.get("new", "")
    if not old_rev or not new_rev:
        return RedirectResponse(f"/works/{slug}/history", status_code=303)
    diff_view = request.query_params.get("diff_view", "immersive")
    if diff_view not in DIFF_VIEWS:
        diff_view = "immersive"
    admin = is_admin(request)
    old = service.resolve_revision(path, old_rev, admin=admin)
    new = service.resolve_revision(path, new_rev, admin=admin)
    if old is None or new is None:
        return not_found()
    try:
        byte_summary, diff_html = service.compare(old, new, diff_view)
    except DiffTooLarge:
        return message(request, "Too large", "These revisions are too large to compare.", 413)
    view = service.work_summary(path)
    if view is None:
        return not_found()
    return render(
        request,
        "compare.html",
        {
            "work": view,
            "service": service,
            "old_rev": old.revision.short_sha,
            "new_rev": new.revision.short_sha,
            "diff_view": diff_view,
            "diff_html": diff_html,
            "byte_summary": byte_summary,
        },
    )


def work_compare_redirect(request: Request) -> Response:
    slug = request.path_params["slug"]
    redirected = _slug_redirect(request, get_state(request).service(), slug)
    if redirected:
        return redirected
    return redirect(
        f"/works/{slug}/history/compare",
        status_code=301,
        old=request.query_params.get("old", ""),
        new=request.query_params.get("new", ""),
    )


def work_pdf(request: Request) -> Response:
    state = get_state(request)
    script_id = request.path_params["kind"]
    if script_id not in state.pdf.option_ids():
        return not_found()
    site = state.site()
    service = state.service(site)
    path, early = _published_path(request, service)
    if early:
        return early
    rev = request.query_params.get("rev")
    if rev:
        revision = service.resolve_revision(path, rev, admin=is_admin(request))
        if revision is None:
            return not_found()
        commit_sha = revision.revision.sha
        committed_at = revision.revision.committed_at
        text = service.revision_text(revision)
    else:
        entry = service.snapshot.entry(path)
        if entry is None:
            return not_found()
        commit_sha = entry.revision_sha
        committed_at = entry.committed_at
        text = service.snapshot.blob_text(path)
    if text is None:
        return not_found()
    try:
        pdf_path = state.pdf.get(
            markdown_text=text,
            script_id=script_id,
            commit_sha=commit_sha,
            path=path,
            author=service.work_author(path),
            rev_label=f"Rev: {format_rev_date(committed_at)}",
            blurb_fields=site.blurb_fields,
        )
    except PdfBusy:
        return HTMLResponse(
            "The PDF builder is busy. Please try again in a minute.",
            status_code=503,
            headers={"Retry-After": "30"},
        )
    except FileNotFoundError:
        return not_found()
    except Exception:
        logger.exception("PDF generation failed for %s", path)
        return HTMLResponse("PDF generation failed.", status_code=500)
    return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_path.name)


routes = [
    Route("/", index),
    Route("/robots.txt", robots_txt),
    Route("/works/{slug:path}/r/{rev}", work_revision),
    Route("/works/{slug:path}/history/compare", work_history_compare),
    Route("/works/{slug:path}/history", work_history),
    Route("/works/{slug:path}/compare", work_compare_redirect),
    Route("/works/{slug:path}/pdf/{kind}", work_pdf),
    Route("/works/{slug:path}", work_view),
]
