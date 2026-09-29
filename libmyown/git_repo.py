"""Pure read helpers over an open dulwich Repo.

Nothing here opens, caches or closes repositories; callers get a Repo from
GitStore.read(), which serializes access (dulwich pack reads are not thread-safe).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Iterator

from dulwich.objects import Commit, Tree
from dulwich.repo import Repo
from dulwich.walk import Walker

HISTORY_MAX_ENTRIES = 500
SCAN_MAX_COMMITS = 1000


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


def resolve_head(repo: Repo, branch: str | None) -> str | None:
    """Tip of the configured branch, falling back to HEAD when it does not exist yet."""
    if branch:
        try:
            return repo.refs[f"refs/heads/{branch}".encode()].decode("ascii")
        except KeyError:
            pass
    try:
        return repo.head().decode("ascii")
    except Exception:
        return None


def head_branch_name(repo: Repo, branch: str | None) -> str | None:
    if branch and f"refs/heads/{branch}".encode() in repo.refs:
        return branch
    try:
        ref = repo.refs.follow(b"HEAD")
    except Exception:
        return None
    if isinstance(ref, tuple):
        ref = ref[0]
    if isinstance(ref, list):
        ref = ref[-1] if ref else None
    if isinstance(ref, bytes) and ref.startswith(b"refs/heads/"):
        return ref.removeprefix(b"refs/heads/").decode("ascii")
    return None


def list_branch_names(repo: Repo) -> list[str]:
    return sorted(
        ref.removeprefix(b"refs/heads/").decode("ascii")
        for ref in repo.refs.keys()
        if ref.startswith(b"refs/heads/")
    )


def _commit(repo: Repo, sha: str) -> Commit | None:
    try:
        obj = repo.object_store[sha.encode("ascii")]
    except (KeyError, ValueError, UnicodeEncodeError):
        return None
    return obj if isinstance(obj, Commit) else None


def list_markdown_paths(repo: Repo, commit_sha: str) -> list[str]:
    commit = _commit(repo, commit_sha)
    if commit is None:
        return []
    paths: list[str] = []
    _walk_tree(repo, "", commit.tree, paths)
    return sorted(paths)


def _walk_tree(repo: Repo, prefix: str, tree_sha: bytes, paths: list[str] | set[str]) -> None:
    tree = repo.object_store[tree_sha]
    if not isinstance(tree, Tree):
        return
    for entry in tree.iteritems():
        name = entry.path.decode("utf-8", errors="replace")
        full = f"{prefix}/{name}" if prefix else name
        if entry.mode & 0o170000 == 0o040000:
            _walk_tree(repo, full, entry.sha, paths)
        elif name.endswith(".md"):
            if isinstance(paths, set):
                paths.add(full)
            else:
                paths.append(full)


def blob_text(repo: Repo, commit_sha: str, path: str) -> str | None:
    commit = _commit(repo, commit_sha)
    if commit is None:
        return None
    tree = repo.object_store[commit.tree]
    if not isinstance(tree, Tree):
        return None
    blob_sha = _blob_sha_for_path(repo, tree, path.split("/"))
    if not blob_sha:
        return None
    return repo.object_store[blob_sha].data.decode("utf-8", errors="replace")


def _blob_sha_for_path(repo: Repo, tree: Tree, parts: list[str]) -> bytes | None:
    if not parts:
        return None
    name = parts[0].encode("utf-8")
    try:
        mode, sha = tree[name]
    except KeyError:
        return None
    is_tree = mode & 0o170000 == 0o040000
    if len(parts) == 1:
        return None if is_tree else sha
    if not is_tree:
        return None
    subtree = repo.object_store[sha]
    if not isinstance(subtree, Tree):
        return None
    return _blob_sha_for_path(repo, subtree, parts[1:])


def list_historical_markdown_paths(
    repo: Repo, head_sha: str, max_commits: int = SCAN_MAX_COMMITS
) -> list[str]:
    paths: set[str] = set()
    for entry in Walker(repo.object_store, include=[head_sha.encode()], max_entries=max_commits):
        _walk_tree(repo, "", entry.commit.tree, paths)
    return sorted(paths)


def list_author_identities(
    repo: Repo, head_sha: str, max_commits: int = SCAN_MAX_COMMITS
) -> list[str]:
    identities = {
        commit_author_identity(entry.commit)
        for entry in Walker(repo.object_store, include=[head_sha.encode()], max_entries=max_commits)
    }
    return sorted(identities)


def file_history(
    repo: Repo,
    head_sha: str,
    path: str,
    *,
    follow: bool,
    max_entries: int = HISTORY_MAX_ENTRIES,
) -> list[FileRevision]:
    revisions: list[FileRevision] = []
    current_path = path
    walker = Walker(
        repo.object_store,
        include=[head_sha.encode()],
        paths=[path.encode("utf-8")],
        follow=follow,
        max_entries=max_entries,
    )
    for entry in walker:
        commit = entry.commit
        commit_sha = commit.id.decode("ascii")
        blob_path = current_path
        if blob_text(repo, commit_sha, blob_path) is None:
            blob_path = _blob_path_in_commit(repo, entry, current_path) or current_path
        revisions.append(revision_from_commit(commit, blob_path=blob_path))
        if follow:
            current_path = _prior_path(entry, current_path)
    return revisions


def _blob_path_in_commit(repo: Repo, entry, current_path: str) -> str | None:
    commit_sha = entry.commit.id.decode("ascii")
    for change in _flat_changes(entry):
        old_path = change.old.path.decode() if change.old and change.old.path else None
        new_path = change.new.path.decode() if change.new and change.new.path else None
        if new_path == current_path and blob_text(repo, commit_sha, new_path):
            return new_path
        if old_path == current_path and blob_text(repo, commit_sha, old_path):
            return old_path
    return None


def _prior_path(entry, current_path: str) -> str:
    for change in _flat_changes(entry):
        old_path = change.old.path.decode() if change.old and change.old.path else None
        new_path = change.new.path.decode() if change.new and change.new.path else None
        if new_path == current_path and old_path:
            return old_path
    return current_path


def _flat_changes(entry):
    """Merge commits report one list of changes per parent; flatten them."""
    for change in entry.changes():
        if isinstance(change, list):
            yield from change
        else:
            yield change


def commit_date(repo: Repo, sha: str) -> datetime | None:
    commit = _commit(repo, sha)
    return _commit_date(commit) if commit is not None else None


def commit_author_identity(commit: Commit) -> str:
    return commit.author.decode("utf-8", errors="replace").strip()


def _commit_author(commit: Commit) -> str:
    return commit_author_identity(commit).split(" <", 1)[0].strip()


def revision_from_commit(commit: Commit, *, blob_path: str) -> FileRevision:
    commit_sha = commit.id.decode("ascii")
    return FileRevision(
        sha=commit_sha,
        short_sha=commit_sha[:7],
        author=_commit_author(commit),
        author_identity=commit_author_identity(commit),
        committed_at=_commit_date(commit),
        message=commit.message.decode("utf-8", errors="replace").strip(),
        blob_path=blob_path,
    )


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
