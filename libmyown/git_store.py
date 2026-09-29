"""Owns every dulwich Repo handle in the process.

- Site reads share one long-lived Repo behind an RLock (`read()`); dulwich pack
  reads seek on shared file objects and are not thread-safe.
- Git smart-HTTP requests each get their own Repo (`open_for_http()`), closed by
  the caller when the response finishes. They hold a shared gate so gc never
  deletes a pack out from under a clone or push in flight.
- `refresh()` drops the shared Repo after a push so new packs become visible;
  `gc()` keeps the pack count (and therefore open descriptors) bounded and
  expires unreachable objects.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from dulwich import gc as dulwich_gc
from dulwich import porcelain
from dulwich.repo import Repo

from libmyown import git_repo

logger = logging.getLogger(__name__)

GC_PACK_THRESHOLD = 8
GC_TIMEOUT_SECONDS = 600
GC_PRUNE_EXPIRE = "2.weeks.ago"
GC_PRUNE_GRACE_SECONDS = 14 * 24 * 60 * 60
TEMP_FILE_GRACE_SECONDS = 60 * 60


@dataclass(frozen=True)
class RepoStorageStats:
    packs: int
    loose_objects: int
    bytes: int


@dataclass(frozen=True)
class GcResult:
    before: RepoStorageStats
    after: RepoStorageStats
    seconds: float
    method: str


class _SharedExclusiveGate:
    """Many shared holders or one exclusive holder; exclusive never waits."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._shared = 0
        self._exclusive = False

    def acquire_shared(self) -> None:
        with self._cond:
            while self._exclusive:
                self._cond.wait()
            self._shared += 1

    def release_shared(self) -> None:
        with self._cond:
            self._shared -= 1
            if self._shared == 0:
                self._cond.notify_all()

    def try_acquire_exclusive(self) -> bool:
        with self._cond:
            if self._exclusive or self._shared:
                return False
            self._exclusive = True
            return True

    def release_exclusive(self) -> None:
        with self._cond:
            self._exclusive = False
            self._cond.notify_all()


class GitStore:
    def __init__(self, repo_path: str | Path, *, branch: str | None = None) -> None:
        self.repo_path = Path(repo_path)
        self._branch = branch or None
        self._lock = threading.RLock()
        self._repo: Repo | None = None
        self._gate = _SharedExclusiveGate()
        self._ensure_repo()

    # -- lifecycle -----------------------------------------------------------

    def _ensure_repo(self) -> None:
        if self.repo_path.exists():
            return
        self.repo_path.parent.mkdir(parents=True, exist_ok=True)
        created = porcelain.init(str(self.repo_path), bare=True)
        if created is not None:
            created.close()

    @contextmanager
    def read(self) -> Iterator[Repo]:
        with self._lock:
            if self._repo is None:
                self._ensure_repo()
                self._repo = Repo(str(self.repo_path))
            yield self._repo

    def refresh(self) -> None:
        """Close the shared Repo; the next read reopens it and sees new refs and packs."""
        with self._lock:
            if self._repo is not None:
                self._repo.close()
                self._repo = None

    def close(self) -> None:
        self.refresh()

    # -- branch --------------------------------------------------------------

    @property
    def branch(self) -> str | None:
        return self._branch

    def set_branch(self, branch: str | None) -> bool:
        branch = (branch or "").strip() or None
        with self._lock:
            if branch == self._branch:
                return False
            self._branch = branch
            return True

    # -- convenience reads (each takes the lock once) -------------------------

    def head_sha(self) -> str | None:
        with self.read() as repo:
            return git_repo.resolve_head(repo, self._branch)

    def head_branch_name(self) -> str | None:
        with self.read() as repo:
            return git_repo.head_branch_name(repo, self._branch)

    def list_branch_names(self) -> list[str]:
        with self.read() as repo:
            return git_repo.list_branch_names(repo)

    def list_markdown_paths(self, commit_sha: str) -> list[str]:
        with self.read() as repo:
            return git_repo.list_markdown_paths(repo, commit_sha)

    def blob_text(self, commit_sha: str, path: str) -> str | None:
        with self.read() as repo:
            return git_repo.blob_text(repo, commit_sha, path)

    def file_history(
        self,
        head_sha: str,
        path: str,
        *,
        follow: bool,
        max_entries: int = git_repo.HISTORY_MAX_ENTRIES,
    ) -> list[git_repo.FileRevision]:
        with self.read() as repo:
            return git_repo.file_history(
                repo, head_sha, path, follow=follow, max_entries=max_entries
            )

    def commit_date(self, sha: str):
        with self.read() as repo:
            return git_repo.commit_date(repo, sha)

    def list_historical_markdown_paths(self, head_sha: str) -> list[str]:
        with self.read() as repo:
            return git_repo.list_historical_markdown_paths(repo, head_sha)

    def list_author_identities(self, head_sha: str) -> list[str]:
        with self.read() as repo:
            return git_repo.list_author_identities(repo, head_sha)

    # -- git smart HTTP ------------------------------------------------------

    def open_for_http(self) -> Repo:
        """A private Repo for one git HTTP request. Release it with `release_http`."""
        self._gate.acquire_shared()
        try:
            self._ensure_repo()
            return Repo(str(self.repo_path))
        except BaseException:
            self._gate.release_shared()
            raise

    def release_http(self, repo: Repo) -> None:
        try:
            repo.close()
        finally:
            self._gate.release_shared()

    # -- maintenance ---------------------------------------------------------

    def pack_count(self) -> int:
        pack_dir = self.repo_path / "objects" / "pack"
        if not pack_dir.is_dir():
            return 0
        return sum(1 for _ in pack_dir.glob("*.pack"))

    def storage_stats(self) -> RepoStorageStats:
        objects = self.repo_path / "objects"
        packs = 0
        loose = 0
        size = 0
        if objects.is_dir():
            for path in objects.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    size += path.stat().st_size
                except FileNotFoundError:
                    continue
                if path.parent.name == "pack":
                    packs += path.suffix == ".pack"
                elif len(path.parent.name) == 2:
                    loose += 1
        return RepoStorageStats(packs=packs, loose_objects=loose, bytes=size)

    def needs_gc(self, threshold: int = GC_PACK_THRESHOLD) -> bool:
        return self.pack_count() > threshold

    def gc(self) -> GcResult | None:
        """Full gc unless a clone or push is in flight; returns None when busy (retry later).

        Deletes leftovers from interrupted pushes, then runs `git gc` at idle CPU
        and IO priority (repack everything into one pack, expire unreachable
        objects older than two weeks, pack refs). Falls back to dulwich when the
        git binary is unavailable.
        """
        if not self._gate.try_acquire_exclusive():
            return None
        try:
            with self._lock:
                self.refresh()
                before = self.storage_stats()
                started = time.monotonic()
                self._prune_temp_files()
                method = self._run_gc()
                after = self.storage_stats()
            result = GcResult(
                before=before,
                after=after,
                seconds=time.monotonic() - started,
                method=method,
            )
            logger.info(
                "git gc (%s) on %s: packs %d -> %d, loose objects %d -> %d, "
                "size %d -> %d bytes, %.1fs",
                method,
                self.repo_path,
                before.packs,
                after.packs,
                before.loose_objects,
                after.loose_objects,
                before.bytes,
                after.bytes,
                result.seconds,
            )
            return result
        finally:
            self._gate.release_exclusive()

    def _prune_temp_files(self) -> None:
        # No transfer can be running (exclusive gate), but pushes made directly on
        # disk (file:// remotes, the seed script) bypass the gate, hence the grace period.
        repo = Repo(str(self.repo_path))
        try:
            repo.object_store.prune(grace_period=TEMP_FILE_GRACE_SECONDS)
        except Exception:
            logger.exception("cleaning temporary pack files failed for %s", self.repo_path)
        finally:
            repo.close()

    def _run_gc(self) -> str:
        git = shutil.which("git")
        if git is not None:
            command = [git, "-C", str(self.repo_path), "gc", "--quiet", f"--prune={GC_PRUNE_EXPIRE}"]
            try:
                subprocess.run(
                    _low_priority(command),
                    check=True,
                    capture_output=True,
                    timeout=GC_TIMEOUT_SECONDS,
                )
                return "git"
            except (OSError, subprocess.SubprocessError) as exc:
                stderr = getattr(exc, "stderr", b"") or b""
                logger.warning(
                    "git gc failed for %s: %s %s",
                    self.repo_path,
                    exc,
                    stderr.decode("utf-8", errors="replace").strip(),
                )
        repo = Repo(str(self.repo_path))
        try:
            dulwich_gc.garbage_collect(repo, prune=True, grace_period=GC_PRUNE_GRACE_SECONDS)
        except Exception:
            logger.exception("dulwich gc failed for %s", self.repo_path)
            return "failed"
        finally:
            repo.close()
        return "dulwich"


def _low_priority(command: list[str]) -> list[str]:
    """Run at the lowest CPU and idle IO priority so page loads stay fast on a Pi."""
    prefix: list[str] = []
    ionice = shutil.which("ionice")
    if ionice is not None:
        prefix += [ionice, "-c3"]
    nice = shutil.which("nice")
    if nice is not None:
        prefix += [nice, "-n", "19"]
    return prefix + command
