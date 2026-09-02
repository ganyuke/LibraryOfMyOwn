from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from libmyown.content import parse_work_summary
from libmyown.git_repo import StoriesRepo


@dataclass(frozen=True)
class WorkIndexEntry:
    path: str
    title: str
    word_count: int
    revision_sha: str
    revision_short_sha: str
    revision_committed_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "word_count": self.word_count,
            "revision_sha": self.revision_sha,
            "revision_short_sha": self.revision_short_sha,
            "revision_committed_at": self.revision_committed_at,
        }

    @classmethod
    def from_dict(cls, path: str, data: dict[str, object]) -> WorkIndexEntry:
        return cls(
            path=path,
            title=str(data["title"]),
            word_count=int(data["word_count"]),
            revision_sha=str(data["revision_sha"]),
            revision_short_sha=str(data["revision_short_sha"]),
            revision_committed_at=str(data["revision_committed_at"]),
        )


@dataclass
class WorkIndex:
    head_sha: str
    branch: str | None
    entries: dict[str, WorkIndexEntry]

    def to_dict(self) -> dict[str, object]:
        return {
            "head_sha": self.head_sha,
            "branch": self.branch,
            "entries": {
                path: entry.to_dict() for path, entry in sorted(self.entries.items())
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> WorkIndex:
        raw_entries = data.get("entries", {})
        entries: dict[str, WorkIndexEntry] = {}
        if isinstance(raw_entries, dict):
            for path, entry_data in raw_entries.items():
                if isinstance(entry_data, dict):
                    entries[str(path)] = WorkIndexEntry.from_dict(str(path), entry_data)
        branch = data.get("branch")
        return cls(
            head_sha=str(data["head_sha"]),
            branch=str(branch) if branch else None,
            entries=entries,
        )


def _build_entry(repo: StoriesRepo, path: str) -> WorkIndexEntry | None:
    head_sha = repo.head_sha()
    if not head_sha:
        return None
    text = repo.get_blob_text(path, head_sha)
    if text is None:
        return None
    summary = parse_work_summary(text, fallback_title=Path(path).stem.replace("-", " "))
    revision = repo.latest_revision(path, follow=True)
    if revision is None:
        return WorkIndexEntry(
            path=path,
            title=summary.title,
            word_count=summary.word_count,
            revision_sha=head_sha,
            revision_short_sha=head_sha[:7],
            revision_committed_at=datetime.now().astimezone().isoformat(),
        )
    return WorkIndexEntry(
        path=path,
        title=summary.title,
        word_count=summary.word_count,
        revision_sha=revision.sha,
        revision_short_sha=revision.short_sha,
        revision_committed_at=revision.committed_at.isoformat(),
    )


def rebuild_work_index(repo: StoriesRepo) -> WorkIndex:
    head_sha = repo.head_sha() or ""
    entries: dict[str, WorkIndexEntry] = {}
    for path in repo.list_markdown_paths():
        entry = _build_entry(repo, path)
        if entry is not None:
            entries[path] = entry
    return WorkIndex(head_sha=head_sha, branch=repo._branch, entries=entries)


def load_work_index(path: Path) -> WorkIndex | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return WorkIndex.from_dict(data)


def save_work_index(path: Path, index: WorkIndex) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(index.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class WorkIndexStore:
    def __init__(self, path: Path, repo: StoriesRepo) -> None:
        self._path = path
        self._repo = repo
        self._cached: WorkIndex | None = None

    def invalidate(self) -> None:
        self._cached = None

    def rebuild(self) -> WorkIndex:
        index = rebuild_work_index(self._repo)
        save_work_index(self._path, index)
        self._cached = index
        return index

    def get(self) -> WorkIndex:
        head_sha = self._repo.head_sha()
        branch = self._repo._branch
        if self._cached and self._cached.head_sha == head_sha and self._cached.branch == branch:
            return self._cached
        loaded = load_work_index(self._path)
        if loaded and loaded.head_sha == head_sha and loaded.branch == branch:
            self._cached = loaded
            return loaded
        return self.rebuild()

    def get_entry(self, path: str) -> WorkIndexEntry | None:
        index = self.get()
        entry = index.entries.get(path)
        if entry is not None:
            return entry
        if path not in self._repo.list_markdown_paths():
            return None
        built = _build_entry(self._repo, path)
        if built is None:
            return None
        index.entries[path] = built
        save_work_index(self._path, index)
        self._cached = index
        return built
