from __future__ import annotations

from dataclasses import dataclass

from archive.service import ArchiveService
from archive.site_config import SiteConfig, StoryContinuity


@dataclass(frozen=True)
class ContinuityRef:
    path: str
    slug: str | None
    title: str
    flags: tuple[str, ...]
    published: bool


@dataclass(frozen=True)
class WorkContinuityNav:
    previous: list[ContinuityRef]
    next: list[ContinuityRef]


@dataclass(frozen=True)
class ContinuityStoryOption:
    path: str
    slug: str
    title: str
    published: bool


def story_title(service: ArchiveService, path: str) -> str:
    view = service.work_at(path)
    if view is None:
        return path.rsplit("/", 1)[-1]
    return view.meta.title


def continuity_story_options(service: ArchiveService) -> list[ContinuityStoryOption]:
    options: list[ContinuityStoryOption] = []
    for path in service.all_paths():
        view = service.work_at(path)
        if view is None:
            continue
        options.append(
            ContinuityStoryOption(
                path=path,
                slug=view.slug,
                title=view.meta.title,
                published=service.is_published(path),
            )
        )
    options.sort(key=lambda item: item.path.lower())
    return options


def _resolve_refs(
    service: ArchiveService,
    site: SiteConfig,
    paths: list[str],
) -> list[ContinuityRef]:
    refs: list[ContinuityRef] = []
    for path in paths:
        view = service.work_at(path)
        if view is None:
            continue
        published = service.is_published(path)
        if not published and not site.expose_unpublished_continuity_titles:
            continue
        refs.append(
            ContinuityRef(
                path=path,
                slug=view.slug if published else None,
                title=view.meta.title,
                flags=service.path_flags(path),
                published=published,
            )
        )
    return refs


def continuity_for_work(
    site: SiteConfig,
    service: ArchiveService,
    path: str,
) -> WorkContinuityNav:
    links = site.story_continuity.get(path, StoryContinuity())
    return WorkContinuityNav(
        previous=_resolve_refs(service, site, links.previous),
        next=_resolve_refs(service, site, links.next),
    )


def continuity_selection(
    site: SiteConfig, path: str
) -> tuple[set[str], set[str]]:
    links = site.story_continuity.get(path, StoryContinuity())
    return set(links.previous), set(links.next)
