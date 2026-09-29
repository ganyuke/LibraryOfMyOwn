"""Immutable per-HEAD view of the stories branch.

A Snapshot is built once per push (on the post-receive worker thread) and then
shared read-only by every request. It carries the work index (title, word count,
latest revision, earliest author) so list pages never walk git history, and it
memoizes file histories for the lifetime of that HEAD.

The index is persisted to work-index.json so restarts are instant. That file is
a derived cache: any version mismatch simply triggers a rebuild.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Hashable

from dulwich.graph import can_fast_forward
from dulwich.walk import Walker

from libmyown import git_repo
from libmyown.content import parse_work_field_keys, parse_work_summary
from libmyown.fsutil import atomic_write_text
from libmyown.git_repo import FileRevision, path_to_slug
from libmyown.git_store import GitStore
from libmyown.lru import LRUCache

logger = logging.getLogger(__name__)

INDEX_VERSION = 2
CARRY_OVER_MAX_COMMITS = 2000

# (commit sha, path) -> text. Keys are immutable, so this never needs invalidating.
_BLOB_CACHE: LRUCache[str | None] = LRUCache(256)


@dataclass(frozen=True)
class WorkIndexEntry:
    path: str
    title: str
    word_count: int
    revision_sha: str
    revision_short_sha: str
    revision_committed_at: str
    field_keys: tuple[str, ...] = ()
    earliest_author_identity: str = ""

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "title": self.title,
            "word_count": self.word_count,
            "revision_sha": self.revision_sha,
            "revision_short_sha": self.revision_short_sha,
            "revision_committed_at": self.revision_committed_at,
        }
        if self.field_keys:
            payload["field_keys"] = list(self.field_keys)
        if self.earliest_author_identity:
            payload["earliest_author_identity"] = self.earliest_author_identity
        return payload

    @classmethod
    def from_dict(cls, path: str, data: dict[str, object]) -> WorkIndexEntry:
        raw_keys = data.get("field_keys", [])
        field_keys = (
            tuple(str(key) for key in raw_keys) if isinstance(raw_keys, list) else ()
        )
        return cls(
            path=path,
            title=str(data["title"]),
            word_count=int(data["word_count"]),  # type: ignore[arg-type]
            revision_sha=str(data["revision_sha"]),
            revision_short_sha=str(data["revision_short_sha"]),
            revision_committed_at=str(data["revision_committed_at"]),
            field_keys=field_keys,
            earliest_author_identity=str(data.get("earliest_author_identity", "")),
        )

    @property
    def committed_at(self) -> datetime:
        return datetime.fromisoformat(self.revision_committed_at)


class Snapshot:
    def __init__(
        self,
        store: GitStore,
        *,
        head_sha: str | None,
        branch: str | None,
        entries: dict[str, WorkIndexEntry],
    ) -> None:
        self.store = store
        self.head_sha = head_sha
        self.branch = branch
        self.entries = entries
        self.paths: list[str] = sorted(entries)
        self.slug_map: dict[str, str] = {path_to_slug(path): path for path in self.paths}
        self._histories: dict[tuple[str, bool], list[FileRevision]] = {}
        self._memo: dict[Hashable, Any] = {}
        self._lock = threading.Lock()

    # -- lookups ---------------------------------------------------------------

    def resolve_slug(self, slug: str) -> str | None:
        return self.slug_map.get(slug)

    def entry(self, path: str) -> WorkIndexEntry | None:
        return self.entries.get(path)

    def discovered_field_keys(self) -> list[str]:
        keys: set[str] = set()
        for entry in self.entries.values():
            keys.update(entry.field_keys)
        return sorted(keys)

    # -- git reads (memoized for this HEAD) ------------------------------------

    def file_history(self, path: str, *, follow: bool) -> list[FileRevision]:
        if not self.head_sha:
            return []
        key = (path, follow)
        cached = self._histories.get(key)
        if cached is not None:
            return cached
        history = self.store.file_history(self.head_sha, path, follow=follow)
        with self._lock:
            return self._histories.setdefault(key, history)

    def blob_text(self, path: str, sha: str | None = None) -> str | None:
        sha = sha or self.head_sha
        if not sha:
            return None
        return _BLOB_CACHE.get_or_compute((sha, path), lambda: self.store.blob_text(sha, path))

    def historical_paths(self) -> list[str]:
        return self._memoized(
            "historical_paths",
            lambda: self.store.list_historical_markdown_paths(self.head_sha) if self.head_sha else [],
        )

    def author_identities(self) -> list[str]:
        return self._memoized(
            "author_identities",
            lambda: self.store.list_author_identities(self.head_sha) if self.head_sha else [],
        )

    def _memoized(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        if key in self._memo:
            return self._memo[key]
        value = compute()
        with self._lock:
            return self._memo.setdefault(key, value)

    # -- persistence -------------------------------------------------------------

    def to_index_dict(self) -> dict[str, object]:
        return {
            "version": INDEX_VERSION,
            "head_sha": self.head_sha or "",
            "branch": self.branch,
            "entries": {path: entry.to_dict() for path, entry in sorted(self.entries.items())},
        }


def _build_entry(
    snapshot: Snapshot,
    path: str,
    *,
    earliest_author_identity: str | None = None,
) -> WorkIndexEntry | None:
    head_sha = snapshot.head_sha
    if not head_sha:
        return None
    text = snapshot.blob_text(path, head_sha)
    if text is None:
        return None
    summary = parse_work_summary(text, fallback_title=Path(path).stem.replace("-", " "))
    field_keys = tuple(sorted(parse_work_field_keys(text)))
    if earliest_author_identity is None:
        history = snapshot.file_history(path, follow=True)
        earliest_author_identity = history[-1].author_identity if history else ""
    else:
        history = snapshot.store.file_history(head_sha, path, follow=True, max_entries=1)
    if not history:
        return WorkIndexEntry(
            path=path,
            title=summary.title,
            word_count=summary.word_count,
            revision_sha=head_sha,
            revision_short_sha=head_sha[:7],
            revision_committed_at=datetime.now().astimezone().isoformat(),
            field_keys=field_keys,
            earliest_author_identity=earliest_author_identity,
        )
    latest = history[0]
    return WorkIndexEntry(
        path=path,
        title=summary.title,
        word_count=summary.word_count,
        revision_sha=latest.sha,
        revision_short_sha=latest.short_sha,
        revision_committed_at=latest.committed_at.isoformat(),
        field_keys=field_keys,
        earliest_author_identity=earliest_author_identity,
    )


def _changed_paths_since(store: GitStore, old_head: str, new_head: str) -> set[str] | None:
    """Paths touched by old_head..new_head, or None when that is not a plain fast-forward."""
    with store.read() as repo:
        if not can_fast_forward(repo, old_head.encode(), new_head.encode()):
            return None
        changed: set[str] = set()
        walker = Walker(
            repo.object_store,
            include=[new_head.encode()],
            exclude=[old_head.encode()],
            max_entries=CARRY_OVER_MAX_COMMITS,
        )
        count = 0
        for entry in walker:
            count += 1
            for change in git_repo._flat_changes(entry):
                for side in (change.old, change.new):
                    if side is not None and side.path:
                        changed.add(side.path.decode("utf-8", errors="replace"))
        if count >= CARRY_OVER_MAX_COMMITS:
            return None
        return changed


def build_snapshot(store: GitStore, previous: Snapshot | None = None) -> Snapshot:
    head_sha = store.head_sha()
    branch = store.branch
    snapshot = Snapshot(store, head_sha=head_sha, branch=branch, entries={})
    if not head_sha:
        return snapshot
    paths = store.list_markdown_paths(head_sha)

    changed: set[str] | None = None
    if (
        previous is not None
        and previous.head_sha
        and previous.branch == branch
        and previous.entries
    ):
        if previous.head_sha == head_sha:
            changed = set()
        else:
            changed = _changed_paths_since(store, previous.head_sha, head_sha)

    entries: dict[str, WorkIndexEntry] = {}
    for path in paths:
        prior = previous.entries.get(path) if previous is not None and changed is not None else None
        if prior is not None and path not in changed:  # type: ignore[operator]
            entries[path] = prior
            continue
        # A fast-forward never changes where an existing file's history begins.
        earliest = prior.earliest_author_identity if prior and prior.earliest_author_identity else None
        entry = _build_entry(snapshot, path, earliest_author_identity=earliest)
        if entry is not None:
            entries[path] = entry
    snapshot.entries = entries
    snapshot.paths = sorted(entries)
    snapshot.slug_map = {path_to_slug(path): path for path in snapshot.paths}
    return snapshot


def load_snapshot(store: GitStore, index_path: Path) -> Snapshot | None:
    """Reuse work-index.json when it matches the current branch tip exactly."""
    if not index_path.is_file():
        return None
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != INDEX_VERSION:
            return None
        head_sha = store.head_sha()
        branch = data.get("branch") or None
        if not head_sha or data.get("head_sha") != head_sha or branch != store.branch:
            return None
        raw_entries = data.get("entries", {})
        entries = {
            str(path): WorkIndexEntry.from_dict(str(path), entry)
            for path, entry in raw_entries.items()
            if isinstance(entry, dict)
        }
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("ignoring unreadable %s", index_path)
        return None
    return Snapshot(store, head_sha=head_sha, branch=store.branch, entries=entries)


def save_snapshot(snapshot: Snapshot, index_path: Path) -> None:
    atomic_write_text(
        index_path,
        json.dumps(snapshot.to_index_dict(), indent=2, sort_keys=True) + "\n",
    )


class SnapshotHolder:
    """Holds the current Snapshot; replaced atomically, never mutated in place."""

    def __init__(self, store: GitStore, index_path: Path) -> None:
        self._store = store
        self._index_path = index_path
        self._build_lock = threading.Lock()
        self._current: Snapshot | None = None

    @property
    def current(self) -> Snapshot:
        snapshot = self._current
        if snapshot is None:
            snapshot = self.load_or_build()
        return snapshot

    def load_or_build(self) -> Snapshot:
        with self._build_lock:
            if self._current is not None:
                return self._current
            snapshot = load_snapshot(self._store, self._index_path)
            if snapshot is None:
                snapshot = build_snapshot(self._store)
                self._save(snapshot)
            self._current = snapshot
            return snapshot

    def rebuild(self, *, full: bool = False) -> Snapshot:
        with self._build_lock:
            previous = None if full else self._current
            snapshot = build_snapshot(self._store, previous)
            self._save(snapshot)
            self._current = snapshot
            return snapshot

    def _save(self, snapshot: Snapshot) -> None:
        try:
            save_snapshot(snapshot, self._index_path)
        except OSError:
            logger.exception("could not write %s", self._index_path)


def clear_blob_cache() -> None:
    _BLOB_CACHE.clear()
