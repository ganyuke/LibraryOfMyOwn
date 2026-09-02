from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from archive.authorship import (
    AUTHOR_MODE_DEFAULT,
    AUTHOR_MODE_EARLIEST,
    display_author,
)
from archive.content import (
    WorkMeta,
    format_date_reader,
    format_datetime,
    format_words,
    parse_work,
    parse_work_summary,
    revision_tooltip,
)
from archive.git_repo import FileRevision, StoriesRepo, path_display_prefix, path_to_slug, slug_to_path
from archive.site_config import SiteConfig
from archive.work_index import WorkIndexEntry, WorkIndexStore


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


@dataclass(frozen=True)
class HistoryEntry:
    path: str
    revision: FileRevision


class ArchiveService:
    def __init__(
        self,
        repo: StoriesRepo,
        site: SiteConfig,
        work_index: WorkIndexStore | None = None,
    ) -> None:
        self.repo = repo
        self.site = site
        self.work_index = work_index

    def effective_default_author(self) -> str:
        return self.site.default_author.strip()

    def display_revision_author(self, revision: FileRevision) -> str:
        return display_author(revision.author_identity, self.site.author_aliases)

    def work_author(self, path: str) -> str:
        override = self.site.work_author_override.get(path, "").strip()
        if override:
            return override
        mode = self.site.work_author_mode.get(path, AUTHOR_MODE_DEFAULT)
        if mode != AUTHOR_MODE_EARLIEST:
            return self.effective_default_author()
        history = self.merged_history(path)
        if not history:
            return self.effective_default_author()
        earliest = min(history, key=lambda entry: entry.revision.committed_at)
        return display_author(
            earliest.revision.author_identity,
            self.site.author_aliases,
        )

    def all_paths(self) -> list[str]:
        return self.repo.list_markdown_paths()

    def canonical_slug(self, slug: str) -> str:
        seen: set[str] = set()
        while slug in self.site.slug_redirects:
            if slug in seen:
                break
            seen.add(slug)
            slug = self.site.slug_redirects[slug]
        return slug

    def resolve_slug(self, slug: str) -> str | None:
        slug = self.canonical_slug(slug)
        return slug_to_path(slug, self.all_paths())

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
        for sources in self.site.history_merges.values():
            if path in sources:
                return True
        return False

    def merge_dest_for(self, path: str) -> str | None:
        for dest, sources in self.site.history_merges.items():
            if path in sources:
                return dest
        return None

    def history_paths(self, canonical_path: str) -> list[str]:
        paths = [canonical_path]
        paths.extend(self.site.history_merges.get(canonical_path, []))
        return paths

    def merged_history(self, canonical_path: str) -> list[HistoryEntry]:
        entries: list[HistoryEntry] = []
        seen_shas: set[str] = set()
        for path in self.history_paths(canonical_path):
            follow = path == canonical_path
            for revision in self.repo.file_history(path, follow=follow):
                if revision.sha in seen_shas:
                    continue
                seen_shas.add(revision.sha)
                entries.append(
                    HistoryEntry(
                        path=revision.blob_path or path,
                        revision=revision,
                    )
                )
        entries.sort(key=lambda entry: entry.revision.committed_at, reverse=True)
        return entries

    def revision_path(self, canonical_path: str, sha: str) -> str | None:
        for entry in self.merged_history(canonical_path):
            if entry.revision.sha == sha:
                return entry.path
        return None

    def revision_blob_text(self, canonical_path: str, sha: str) -> str | None:
        blob_path = self.revision_path(canonical_path, sha)
        if blob_path is None:
            return None
        return self.repo.get_blob_text(blob_path, sha)

    def path_flags(self, path: str) -> tuple[str, ...]:
        known = set(self.site.flags)
        return tuple(
            flag_id
            for flag_id in self.site.work_flags.get(path, [])
            if flag_id in known
        )

    def _revision_labels(
        self, revision: FileRevision | None
    ) -> tuple[str, str, str]:
        if revision is None:
            return "", "", ""
        committed_exact = format_datetime(revision.committed_at)
        return (
            format_date_reader(revision.committed_at),
            revision_tooltip(revision.short_sha, committed_exact),
            revision.short_sha,
        )

    def _revision_labels_from_entry(
        self, entry: WorkIndexEntry
    ) -> tuple[str, str, str]:
        from datetime import datetime

        committed_at = datetime.fromisoformat(entry.revision_committed_at)
        committed_exact = format_datetime(committed_at)
        return (
            format_date_reader(committed_at),
            revision_tooltip(entry.revision_short_sha, committed_exact),
            entry.revision_short_sha,
        )

    def _published_work_from_entry(
        self, entry: WorkIndexEntry, path: str | None = None
    ) -> PublishedWork:
        from datetime import datetime

        committed_at = datetime.fromisoformat(entry.revision_committed_at)
        committed_exact = format_datetime(committed_at)
        work_path = path or entry.path
        return PublishedWork(
            path=work_path,
            path_prefix=path_display_prefix(work_path),
            slug=path_to_slug(work_path),
            title=entry.title,
            author=self.work_author(work_path),
            word_count=entry.word_count,
            words_display=format_words(entry.word_count),
            updated_display=format_date_reader(committed_at),
            updated_tooltip=revision_tooltip(entry.revision_short_sha, committed_exact),
            flags=self.path_flags(work_path),
        )

    def work_summary(self, path: str) -> PublishedWork | None:
        if self.work_index is not None:
            entry = self.work_index.get_entry(path)
            if entry is not None:
                return self._published_work_from_entry(entry, path)
        return self._work_summary_uncached(path)

    def _work_summary_uncached(self, path: str) -> PublishedWork | None:
        sha = self.repo.head_sha()
        if not sha:
            return None
        text = self.repo.get_blob_text(path, sha)
        if text is None:
            return None
        summary = parse_work_summary(text, fallback_title=Path(path).stem.replace("-", " "))
        updated_display, updated_tooltip, _ = self._revision_labels(
            self.repo.latest_revision(path, follow=True)
        )
        return PublishedWork(
            path=path,
            path_prefix=path_display_prefix(path),
            slug=path_to_slug(path),
            title=summary.title,
            author=self.work_author(path),
            word_count=summary.word_count,
            words_display=format_words(summary.word_count),
            updated_display=updated_display,
            updated_tooltip=updated_tooltip,
            flags=self.path_flags(path),
        )

    def apply_history_merge(self, *, source: str, dest: str) -> str | None:
        paths = set(self.all_paths())
        if dest not in paths:
            return "Destination file not found."
        if source not in paths and not self.repo.path_has_history(source):
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

    def published_works(self) -> list[PublishedWork]:
        works: list[PublishedWork] = []
        for path in self.all_paths():
            if not self.is_published(path):
                continue
            summary = self.work_summary(path)
            if summary is not None:
                works.append(summary)
        works.sort(key=lambda w: w.path.lower())
        return works

    def related_works(self, path: str) -> list[PublishedWork]:
        parent = Path(path).parent
        works: list[PublishedWork] = []
        for candidate in self.all_paths():
            if Path(candidate).parent != parent or candidate == path:
                continue
            if not self.is_published(candidate):
                continue
            summary = self.work_summary(candidate)
            if summary is not None:
                works.append(summary)
        works.sort(key=lambda w: w.path.lower())
        return works

    def work_at(self, path: str, commit_sha: str | None = None) -> WorkView | None:
        sha = self.repo.resolve_sha(commit_sha) if commit_sha else self.repo.head_sha()
        if not sha:
            return None
        blob_path = path
        if commit_sha:
            resolved = self.revision_path(path, sha)
            if resolved is None:
                return None
            blob_path = resolved
        text = self.repo.get_blob_text(blob_path, sha)
        if text is None:
            return None
        if commit_sha:
            history = self.merged_history(path)
            revision = next(
                (entry.revision for entry in history if entry.revision.sha == sha),
                None,
            )
            short_sha = revision.short_sha if revision else sha[:7]
            updated_display, updated_tooltip, _ = self._revision_labels(revision)
        else:
            index_entry = self.work_index.get_entry(path) if self.work_index else None
            if index_entry is not None:
                updated_display, updated_tooltip, short_sha = self._revision_labels_from_entry(
                    index_entry
                )
            else:
                revision = self.repo.latest_revision(path, follow=True)
                updated_display, updated_tooltip, short_sha = self._revision_labels(revision)
        meta = parse_work(text, fallback_title=Path(path).stem.replace("-", " "))
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
        )

    def is_suppressed(self, path: str, sha: str) -> bool:
        return sha in self.site.suppressed_commits.get(path, set())

    def sanitize_suppressed(self, path: str, suppressed: set[str]) -> set[str]:
        history = self.repo.file_history(path, follow=True)
        if not history:
            return set()
        latest_sha = history[0].sha
        valid_shas = {revision.sha for revision in history}
        return {
            sha for sha in suppressed if sha in valid_shas and sha != latest_sha
        }

    def visible_history(self, path: str) -> list[HistoryEntry]:
        return [
            entry
            for entry in self.merged_history(path)
            if not self.is_suppressed(entry.path, entry.revision.sha)
        ]

    def can_view_revision(self, path: str, sha: str, *, admin: bool) -> bool:
        if self.repo.get_blob_text(path, sha) is None:
            if admin:
                return self.revision_path(path, sha) is not None
            return False
        if admin:
            return True
        if not self.is_published(path):
            return False
        blob_path = self.revision_path(path, sha) or path
        return not self.is_suppressed(blob_path, sha)
