from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from libmyown.git_repo import path_to_slug
from libmyown.service import LibraryService
from libmyown.site_config import SiteConfig, StoryContinuity


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


def _story_title(service: LibraryService, path: str) -> str:
    fallback = Path(path).stem.replace("-", " ")
    if service.work_index is None:
        return fallback
    entry = service.work_index.get_entry(path)
    return entry.title if entry else fallback


def continuity_story_options(service: LibraryService) -> list[ContinuityStoryOption]:
    paths = service.all_paths()
    index_entries = (
        service.work_index.get().entries if service.work_index is not None else {}
    )
    options: list[ContinuityStoryOption] = []
    for path in paths:
        slug = path_to_slug(path)
        if not slug:
            continue
        entry = index_entries.get(path)
        title = entry.title if entry else _story_title(service, path)
        options.append(
            ContinuityStoryOption(
                path=path,
                slug=slug,
                title=title,
                published=service.is_published(path),
            )
        )
    options.sort(key=lambda item: item.path.lower())
    return options


def _resolve_refs(
    service: LibraryService,
    site: SiteConfig,
    paths: list[str],
) -> list[ContinuityRef]:
    refs: list[ContinuityRef] = []
    for path in paths:
        slug = path_to_slug(path)
        if not slug:
            continue
        published = service.is_published(path)
        if not published and not site.expose_unpublished_continuity_titles:
            continue
        refs.append(
            ContinuityRef(
                path=path,
                slug=slug if published else None,
                title=_story_title(service, path),
                flags=service.path_flags(path),
                published=published,
            )
        )
    return refs


def continuity_for_work(
    site: SiteConfig,
    service: LibraryService,
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
