from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator

from dulwich import porcelain
from dulwich.objects import Commit, Tree
from dulwich.repo import Repo
from dulwich.walk import Walker


@dataclass(frozen=True)
class FileRevision:
    sha: str
    short_sha: str
    author: str
    author_identity: str
    committed_at: datetime
    message: str
    blob_path: str | None = None


@dataclass(frozen=True)
class WorkFile:
    path: str
    slug: str


class StoriesRepo:
    def __init__(self, repo_path: str | Path, *, branch: str | None = None) -> None:
        self.repo_path = Path(repo_path)
        self._branch = branch
        self._generation = 0
        self._history_cache: dict[tuple[str, bool, str | None], list[FileRevision]] = {}
        self._latest_cache: dict[tuple[str, bool, str | None], FileRevision | None] = {}
        self._paths_cache: tuple[str, list[str]] | None = None
        self._slug_map_cache: tuple[str, dict[str, str]] | None = None
        self._ensure_repo()

    @property
    def generation(self) -> int:
        return self._generation

    def _ensure_repo(self) -> None:
        if not self.repo_path.exists():
            self.repo_path.parent.mkdir(parents=True, exist_ok=True)
            porcelain.init(str(self.repo_path), bare=True)

    def open(self) -> Repo:
        return Repo(str(self.repo_path))

    def invalidate(self) -> None:
        self._generation += 1
        self._history_cache.clear()
        self._latest_cache.clear()
        self._paths_cache = None
        self._slug_map_cache = None
        from libmyown.content import clear_parsed_work_cache
        from libmyown.service import clear_merged_history_cache

        clear_parsed_work_cache()
        clear_merged_history_cache()

    def head_sha(self) -> str | None:
        repo = self.open()
        if self._branch:
            try:
                return repo.refs[f"refs/heads/{self._branch}".encode()].decode("ascii")
            except KeyError:
                pass
        try:
            return repo.head().decode("ascii")
        except Exception:
            return None

    def head_branch_name(self) -> str | None:
        repo = self.open()
        if self._branch:
            ref_name = f"refs/heads/{self._branch}".encode()
            if ref_name in repo.refs:
                return self._branch
        try:
            ref = repo.refs.follow(b"HEAD")
        except Exception:
            return None
        if isinstance(ref, tuple):
            ref = ref[0]
        if isinstance(ref, bytes) and ref.startswith(b"refs/heads/"):
            return ref.removeprefix(b"refs/heads/").decode("ascii")
        return None

    def list_branch_names(self) -> list[str]:
        repo = self.open()
        names: list[str] = []
        for ref in repo.refs.keys():
            if ref.startswith(b"refs/heads/"):
                names.append(ref.removeprefix(b"refs/heads/").decode("ascii"))
        return sorted(names)

    def list_markdown_paths(self, commit_sha: str | None = None) -> list[str]:
        sha = commit_sha or self.head_sha()
        if not sha:
            return []
        if commit_sha is None and self._paths_cache is not None:
            cached_sha, cached_paths = self._paths_cache
            if cached_sha == sha:
                return cached_paths
        repo = self.open()
        commit = repo[sha.encode("ascii")]
        if not isinstance(commit, Commit):
            return []
        paths: list[str] = []
        tree = repo[commit.tree]
        if isinstance(tree, Tree):
            self._walk_tree(repo, "", commit.tree, paths)
        paths = sorted(paths)
        if commit_sha is None:
            self._paths_cache = (sha, paths)
            self._slug_map_cache = None
        return paths

    def slug_map(self, commit_sha: str | None = None) -> dict[str, str]:
        sha = commit_sha or self.head_sha() or ""
        if commit_sha is None and self._slug_map_cache is not None:
            cached_sha, cached_map = self._slug_map_cache
            if cached_sha == sha:
                return cached_map
        by_slug = {path_to_slug(path): path for path in self.list_markdown_paths(commit_sha)}
        if commit_sha is None:
            self._slug_map_cache = (sha, by_slug)
        return by_slug

    def resolve_path_slug(self, slug: str, commit_sha: str | None = None) -> str | None:
        return self.slug_map(commit_sha).get(slug)

    def _walk_tree(
        self, repo: Repo, prefix: str, tree_sha: bytes, paths: list[str]
    ) -> None:
        tree = repo[tree_sha]
        if not isinstance(tree, Tree):
            return
        for entry in tree.iteritems():
            name = entry.path.decode("utf-8")
            full = f"{prefix}/{name}" if prefix else name
            obj = repo[entry.sha]
            if isinstance(obj, Tree):
                self._walk_tree(repo, full, entry.sha, paths)
            elif name.endswith(".md"):
                paths.append(full)

    def get_blob_text(self, path: str, commit_sha: str | None = None) -> str | None:
        sha = commit_sha or self.head_sha()
        if not sha:
            return None
        repo = self.open()
        commit = repo[sha.encode("ascii")]
        if not isinstance(commit, Commit):
            return None
        tree = repo[commit.tree]
        if not isinstance(tree, Tree):
            return None
        blob_sha = self._blob_sha_for_path(repo, tree, path.split("/"))
        if not blob_sha:
            return None
        blob = repo[blob_sha]
        return blob.data.decode("utf-8", errors="replace")

    def _blob_sha_for_path(
        self, repo: Repo, tree: Tree, parts: list[str]
    ) -> bytes | None:
        if not parts:
            return None
        name = parts[0]
        for entry in tree.iteritems():
            entry_name = entry.path.decode("utf-8")
            if entry_name != name:
                continue
            obj = repo[entry.sha]
            if isinstance(obj, Tree):
                if len(parts) == 1:
                    return None
                return self._blob_sha_for_path(repo, obj, parts[1:])
            if len(parts) == 1:
                return entry.sha
            return None
        return None

    def list_historical_markdown_paths(self, max_commits: int = 1000) -> list[str]:
        sha = self.head_sha()
        if not sha:
            return []
        repo = self.open()
        paths: set[str] = set()
        for entry in Walker(repo, include=[sha.encode()], max_entries=max_commits):
            commit = entry.commit
            if commit is None:
                continue
            self._collect_md_paths(repo, commit.tree, paths)
        return sorted(paths)

    def _collect_md_paths(
        self, repo: Repo, tree_sha: bytes, paths: set[str], prefix: str = ""
    ) -> None:
        tree = repo[tree_sha]
        if not isinstance(tree, Tree):
            return
        for entry in tree.iteritems():
            name = entry.path.decode("utf-8")
            full = f"{prefix}/{name}" if prefix else name
            obj = repo[entry.sha]
            if isinstance(obj, Tree):
                self._collect_md_paths(repo, entry.sha, paths, full)
            elif name.endswith(".md"):
                paths.add(full)

    def latest_revision(self, path: str, *, follow: bool = True) -> FileRevision | None:
        sha = self.head_sha()
        if not sha:
            return None
        cache_key = (path, follow, sha)
        if cache_key in self._latest_cache:
            return self._latest_cache[cache_key]
        full = self._history_cache.get(cache_key)
        if full:
            latest = full[0]
            self._latest_cache[cache_key] = latest
            return latest
        repo = self.open()
        for entry in Walker(
            repo,
            include=[sha.encode()],
            paths=[path.encode("utf-8")],
            follow=follow,
            max_entries=1,
        ):
            commit = entry.commit
            if commit is None:
                break
            commit_sha = commit.id.decode("ascii")
            blob_path = path
            if self.get_blob_text(blob_path, commit_sha) is None:
                blob_path = self._blob_path_in_commit(entry, path) or path
            latest = self._revision_from_commit(commit, blob_path=blob_path)
            self._latest_cache[cache_key] = latest
            return latest
        self._latest_cache[cache_key] = None
        return None

    def file_history(self, path: str, *, follow: bool = False) -> list[FileRevision]:
        sha = self.head_sha()
        if not sha:
            return []
        cache_key = (path, follow, sha)
        cached = self._history_cache.get(cache_key)
        if cached is not None:
            return cached
        repo = self.open()
        revisions: list[FileRevision] = []
        current_path = path
        for entry in Walker(
            repo,
            include=[sha.encode()],
            paths=[path.encode("utf-8")],
            follow=follow,
            max_entries=500,
        ):
            commit = entry.commit
            if commit is None:
                continue
            commit_sha = commit.id.decode("ascii")
            blob_path = current_path
            if self.get_blob_text(blob_path, commit_sha) is None:
                blob_path = self._blob_path_in_commit(entry, current_path) or current_path
            revisions.append(
                self._revision_from_commit(commit, blob_path=blob_path)
            )
            if follow:
                current_path = self._prior_path(entry, current_path)
        self._history_cache[cache_key] = revisions
        return revisions

    def _blob_path_in_commit(self, entry, current_path: str) -> str | None:
        for change in entry.changes():
            old_path = change.old.path.decode() if change.old else None
            new_path = change.new.path.decode() if change.new else None
            if new_path == current_path and self.get_blob_text(
                new_path, entry.commit.id.decode("ascii")
            ):
                return new_path
            if old_path == current_path and self.get_blob_text(
                old_path, entry.commit.id.decode("ascii")
            ):
                return old_path
        return None

    @staticmethod
    def _prior_path(entry, current_path: str) -> str:
        for change in entry.changes():
            old_path = change.old.path.decode() if change.old else None
            new_path = change.new.path.decode() if change.new else None
            if new_path == current_path and old_path:
                return old_path
        return current_path

    def path_has_history(self, path: str) -> bool:
        return self.latest_revision(path) is not None

    def commit_date(self, sha: str) -> datetime | None:
        repo = self.open()
        try:
            commit = repo[sha.encode("ascii")]
        except KeyError:
            return None
        return self._commit_date(commit)

    def resolve_sha(self, partial: str) -> str | None:
        if not partial:
            return self.head_sha()
        repo = self.open()
        try:
            if partial.encode("ascii") in repo:
                return partial
        except Exception:
            pass
        matches: list[str] = []
        tip = self.head_sha()
        include = [tip.encode()] if tip else []
        for entry in Walker(repo, include=include, max_entries=500):
            sha = entry.commit.id.decode("ascii")
            if sha.startswith(partial):
                matches.append(sha)
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def _commit_author_identity(commit: Commit) -> str:
        return commit.author.decode("utf-8", errors="replace").strip()

    @staticmethod
    def _commit_author(commit: Commit) -> str:
        author = StoriesRepo._commit_author_identity(commit)
        return author.split(" <", 1)[0].strip()

    def list_author_identities(self, max_commits: int = 1000) -> list[str]:
        sha = self.head_sha()
        if not sha:
            return []
        repo = self.open()
        identities: set[str] = set()
        for entry in Walker(repo, include=[sha.encode()], max_entries=max_commits):
            commit = entry.commit
            if commit is None:
                continue
            identities.add(self._commit_author_identity(commit))
        return sorted(identities)

    def _revision_from_commit(
        self, commit: Commit, *, blob_path: str
    ) -> FileRevision:
        commit_sha = commit.id.decode("ascii")
        identity = self._commit_author_identity(commit)
        return FileRevision(
            sha=commit_sha,
            short_sha=commit_sha[:7],
            author=self._commit_author(commit),
            author_identity=identity,
            committed_at=self._commit_date(commit),
            message=commit.message.decode("utf-8", errors="replace").strip(),
            blob_path=blob_path,
        )

    @staticmethod
    def _commit_date(commit: Commit) -> datetime:
        return datetime.fromtimestamp(commit.commit_time, tz=timezone.utc)


def path_display_prefix(path: str) -> str | None:
    parent = PurePosixPath(path).parent
    if not parent.parts:
        return None
    return parent.as_posix() + "/"


def path_to_slug(path: str) -> str:
    posix = PurePosixPath(path)
    parent = "/".join(part.lower() for part in posix.parent.parts if part)
    stem = re.sub(r"[^a-z0-9]+", "-", posix.stem.lower()).strip("-")
    return f"{parent}/{stem}" if parent else stem


def slug_to_path(slug: str, paths: list[str]) -> str | None:
    by_slug = {path_to_slug(path): path for path in paths}
    return by_slug.get(slug)


def iter_directory_prefixes(directory: str) -> Iterator[str]:
    normalized = directory.rstrip("/") + "/"
    yield normalized
