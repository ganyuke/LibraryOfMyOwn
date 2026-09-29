from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from libmyown.authorship import (
    AUTHOR_MODE_DEFAULT,
    AUTHOR_MODE_EARLIEST,
    display_author,
)
from libmyown.content import (
    WorkMeta,
    extract_work_body,
    format_date_reader,
    format_datetime,
    format_words,
    parse_work_cached,
    revision_tooltip,
)
from libmyown.diff import diff_byte_stats, diff_html, format_compare_byte_summary
from libmyown.git_repo import FileRevision, path_display_prefix, path_to_slug
from libmyown.lru import LRUCache
from libmyown.site_config import SiteConfig
from libmyown.snapshot import Snapshot, WorkIndexEntry

REVISION_RE = re.compile(r"[0-9a-f]{4,40}")
MAX_DIFF_CHARS = 2 * 1024 * 1024

# (old path, old sha, new path, new sha, view) -> (byte summary html, diff html)
_DIFF_CACHE: LRUCache[tuple[str, str]] = LRUCache(64)


class DiffTooLarge(Exception):
    pass


@dataclass(frozen=True)
class PublishedWork:
    path: str
    path_prefix: str | None
    slug: str
    title: str
    author: str
    word_count: int
    words_display: str
    updated_display: str
    updated_tooltip: str
    flags: tuple[str, ...]


@dataclass(frozen=True)
class WorkView:
    path: str
    path_prefix: str | None
    slug: str
    commit_sha: str
    short_sha: str
    author: str
    words_display: str
    updated_display: str
    updated_tooltip: str
    meta: WorkMeta
    flags: tuple[str, ...]
    suppressed: bool
    at_revision: bool = False


@dataclass(frozen=True)
class HistoryEntry:
    path: str
    revision: FileRevision


def clear_diff_cache() -> None:
    _DIFF_CACHE.clear()


class LibraryService:
    """Per-request view over one immutable Snapshot and one SiteConfig."""

    def __init__(self, snapshot: Snapshot, site: SiteConfig) -> None:
        self.snapshot = snapshot
        self.site = site

    # -- authorship ------------------------------------------------------------

    def effective_default_author(self) -> str:
        return self.site.default_author.strip()

    def display_revision_author(self, revision: FileRevision) -> str:
        return display_author(revision.author_identity, self.site.author_aliases)

    def work_author(self, path: str) -> str:
        override = self.site.work_author_override.get(path, "").strip()
        if override:
            return override
        mode = self.site.work_author_mode.get(path) or self.site.default_author_rule
        if mode == AUTHOR_MODE_EARLIEST:
            return self._author_from_earliest_commit(path)
        return self.effective_default_author()

    def _author_from_earliest_commit(self, path: str) -> str:
        identity = ""
        entry = self.snapshot.entry(path)
        if entry is not None and entry.earliest_author_identity and not self.site.history_merges.get(path):
            identity = entry.earliest_author_identity
        else:
            history = self.merged_history(path)
            if history:
                identity = history[-1].revision.author_identity
        if not identity:
            return self.effective_default_author()
        return display_author(identity, self.site.author_aliases)

    # -- paths and publishing ------------------------------------------------------

    def all_paths(self) -> list[str]:
        return self.snapshot.paths

    def canonical_slug(self, slug: str) -> str:
        seen: set[str] = set()
        while slug in self.site.slug_redirects:
            if slug in seen:
                break
            seen.add(slug)
            slug = self.site.slug_redirects[slug]
        return slug

    def resolve_slug(self, slug: str) -> str | None:
        return self.snapshot.resolve_slug(self.canonical_slug(slug))

    def is_published(self, path: str) -> bool:
        if self.is_merge_source(path):
            return False
        if path in self.site.published_paths:
            return True
        for directory in self.site.published_directories:
            prefix = directory.rstrip("/") + "/"
            if path.startswith(prefix) or path == directory.rstrip("/"):
                return True
        return False

    def is_merge_source(self, path: str) -> bool:
        return any(path in sources for sources in self.site.history_merges.values())

    def merge_dest_for(self, path: str) -> str | None:
        for dest, sources in self.site.history_merges.items():
            if path in sources:
                return dest
        return None

    def history_paths(self, canonical_path: str) -> list[str]:
        return [canonical_path, *self.site.history_merges.get(canonical_path, [])]

    def path_flags(self, path: str) -> tuple[str, ...]:
        known = self.site.flags
        return tuple(flag_id for flag_id in self.site.work_flags.get(path, []) if flag_id in known)

    # -- history -----------------------------------------------------------------

    def merged_history(self, canonical_path: str) -> list[HistoryEntry]:
        paths = tuple(self.history_paths(canonical_path))
        return self.snapshot._memoized(("merged", paths), lambda: self._merged_history(paths))

    def _merged_history(self, paths: tuple[str, ...]) -> list[HistoryEntry]:
        entries: list[HistoryEntry] = []
        seen_shas: set[str] = set()
        canonical_path = paths[0]
        for path in paths:
            for revision in self.snapshot.file_history(path, follow=path == canonical_path):
                if revision.sha in seen_shas:
                    continue
                seen_shas.add(revision.sha)
                entries.append(HistoryEntry(path=revision.blob_path or path, revision=revision))
        entries.sort(key=lambda entry: entry.revision.committed_at, reverse=True)
        return entries

    def is_suppressed(self, path: str, sha: str) -> bool:
        return sha in self.site.suppressed_commits.get(path, set())

    def visible_history(self, path: str) -> list[HistoryEntry]:
        return [
            entry
            for entry in self.merged_history(path)
            if not self.is_suppressed(entry.path, entry.revision.sha)
        ]

    def sanitize_suppressed(self, path: str, suppressed: set[str]) -> set[str]:
        history = self.snapshot.file_history(path, follow=True)
        if not history:
            return set()
        latest_sha = history[0].sha
        valid_shas = {revision.sha for revision in history}
        return {sha for sha in suppressed if sha in valid_shas and sha != latest_sha}

    def revision_path(self, canonical_path: str, sha: str) -> str | None:
        for entry in self.merged_history(canonical_path):
            if entry.revision.sha == sha:
                return entry.path
        return None

    def resolve_revision(self, path: str, rev: str, *, admin: bool) -> HistoryEntry | None:
        """The only way a request turns a revision string into content.

        Accepts hex commit ids (4-40 chars) that name exactly one commit in this
        work's merged history on the configured branch. Ref names, other
        branches and unrelated commits never resolve. Anonymous readers also
        need the work published and the revision not suppressed.
        """
        rev = rev.strip().lower()
        if not REVISION_RE.fullmatch(rev):
            return None
        if not admin and not self.is_published(path):
            return None
        matches = [
            entry for entry in self.merged_history(path) if entry.revision.sha.startswith(rev)
        ]
        if len(matches) != 1:
            return None
        entry = matches[0]
        if not admin and self.is_suppressed(entry.path, entry.revision.sha):
            return None
        return entry

    def revision_text(self, entry: HistoryEntry) -> str | None:
        return self.snapshot.blob_text(entry.path, entry.revision.sha)

    def compare(self, old: HistoryEntry, new: HistoryEntry, view: str) -> tuple[str, str]:
        key = (old.path, old.revision.sha, new.path, new.revision.sha, view)

        def compute() -> tuple[str, str]:
            old_body = extract_work_body(self.revision_text(old) or "")
            new_body = extract_work_body(self.revision_text(new) or "")
            if len(old_body) + len(new_body) > MAX_DIFF_CHARS:
                raise DiffTooLarge()
            summary = format_compare_byte_summary(diff_byte_stats(old_body, new_body))
            return summary, diff_html(old_body, new_body, view=view)

        return _DIFF_CACHE.get_or_compute(key, compute)

    # -- work summaries and views --------------------------------------------------

    def _published_work_from_entry(self, entry: WorkIndexEntry) -> PublishedWork:
        committed_at = entry.committed_at
        return PublishedWork(
            path=entry.path,
            path_prefix=path_display_prefix(entry.path),
            slug=path_to_slug(entry.path),
            title=entry.title,
            author=self.work_author(entry.path),
            word_count=entry.word_count,
            words_display=format_words(entry.word_count),
            updated_display=format_date_reader(committed_at),
            updated_tooltip=revision_tooltip(entry.revision_short_sha, format_datetime(committed_at)),
            flags=self.path_flags(entry.path),
        )

    def work_summary(self, path: str) -> PublishedWork | None:
        entry = self.snapshot.entry(path)
        return self._published_work_from_entry(entry) if entry is not None else None

    def published_works(self) -> list[PublishedWork]:
        works = [
            self._published_work_from_entry(entry)
            for path, entry in self.snapshot.entries.items()
            if self.is_published(path)
        ]
        works.sort(key=lambda work: work.path.lower())
        return works

    def related_works(self, path: str) -> list[PublishedWork]:
        parent = Path(path).parent
        works = [
            self._published_work_from_entry(entry)
            for candidate, entry in self.snapshot.entries.items()
            if candidate != path and Path(candidate).parent == parent and self.is_published(candidate)
        ]
        works.sort(key=lambda work: work.path.lower())
        return works

    def work_at(self, path: str, revision: HistoryEntry | None = None) -> WorkView | None:
        """The latest version of a work, or one already-authorized revision of it."""
        fallback_title = Path(path).stem.replace("-", " ")
        if revision is not None:
            sha = revision.revision.sha
            blob_path = revision.path
            text = self.revision_text(revision)
            short_sha = revision.revision.short_sha
            committed_at = revision.revision.committed_at
            updated_display = format_date_reader(committed_at)
            updated_tooltip = revision_tooltip(short_sha, format_datetime(committed_at))
        else:
            entry = self.snapshot.entry(path)
            if entry is None or not self.snapshot.head_sha:
                return None
            sha = entry.revision_sha
            blob_path = path
            text = self.snapshot.blob_text(path, self.snapshot.head_sha)
            short_sha = entry.revision_short_sha
            committed_at = entry.committed_at
            updated_display = format_date_reader(committed_at)
            updated_tooltip = revision_tooltip(short_sha, format_datetime(committed_at))
        if text is None:
            return None
        meta = parse_work_cached(text, path=blob_path, sha=sha, fallback_title=fallback_title)
        return WorkView(
            path=path,
            path_prefix=path_display_prefix(path),
            slug=path_to_slug(path),
            commit_sha=sha,
            short_sha=short_sha,
            author=self.work_author(path),
            words_display=format_words(meta.word_count),
            updated_display=updated_display,
            updated_tooltip=updated_tooltip,
            meta=meta,
            flags=self.path_flags(path),
            suppressed=self.is_suppressed(blob_path, sha),
            at_revision=revision is not None,
        )

    # -- history merges (mutate self.site; call inside ConfigStore.edit) ------------

    def apply_history_merge(self, *, source: str, dest: str) -> str | None:
        paths = set(self.all_paths())
        if dest not in paths:
            return "Destination file not found."
        if source not in paths and not self.snapshot.file_history(source, follow=True):
            return "Source file not found."
        if source == dest:
            return "Source and destination must be different."
        if self.is_merge_source(dest):
            return "Merge into the canonical work instead."
        if source in self.site.history_merges.get(dest, []):
            return "Histories are already merged."

        source_slug = path_to_slug(source)
        dest_slug = path_to_slug(dest)
        was_published = source in self.site.published_paths
        was_flags = list(self.path_flags(source))
        chained_redirects: dict[str, str] = {}
        for old_slug, target_slug in list(self.site.slug_redirects.items()):
            if target_slug == source_slug:
                chained_redirects[old_slug] = target_slug
                self.site.slug_redirects[old_slug] = dest_slug

        sources = [source]
        transferred_meta = self.site.history_merge_meta.pop(source, {})
        sources.extend(self.site.history_merges.pop(source, []))
        merged = self.site.history_merges.setdefault(dest, [])
        dest_meta = self.site.history_merge_meta.setdefault(dest, {})
        for path in sources:
            if path != dest and path not in merged:
                merged.append(path)
            if path == source:
                dest_meta[path] = {
                    "chained_redirects": chained_redirects,
                    "was_published": was_published,
                    "was_flags": was_flags,
                }
            elif path in transferred_meta:
                dest_meta[path] = transferred_meta[path]

        self.site.slug_redirects[source_slug] = dest_slug
        self.site.published_paths.discard(source)
        self.site.work_flags.pop(source, None)
        return None

    def remove_history_merge(self, *, source: str, dest: str) -> str | None:
        merged = self.site.history_merges.get(dest, [])
        if source not in merged:
            return "Merge not found."

        merged.remove(source)
        if merged:
            self.site.history_merges[dest] = merged
        else:
            del self.site.history_merges[dest]

        source_slug = path_to_slug(source)
        dest_slug = path_to_slug(dest)
        if self.site.slug_redirects.get(source_slug) == dest_slug:
            del self.site.slug_redirects[source_slug]

        meta = self.site.history_merge_meta.get(dest, {}).pop(source, {})
        for old_slug, previous_target in meta.get("chained_redirects", {}).items():
            self.site.slug_redirects[old_slug] = previous_target

        if meta.get("was_published"):
            self.site.published_paths.add(source)
        restored_flags = list(meta.get("was_flags", []))
        if not restored_flags and meta.get("was_wip"):
            restored_flags = ["wip"]
        if restored_flags:
            self.site.work_flags[source] = restored_flags

        if dest in self.site.history_merge_meta and not self.site.history_merge_meta[dest]:
            del self.site.history_merge_meta[dest]
        return None
