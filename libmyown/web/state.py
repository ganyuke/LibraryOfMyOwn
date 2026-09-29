from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from starlette.requests import Request
from starlette.templating import Jinja2Templates

from libmyown.config import Settings
from libmyown.git_store import GitStore
from libmyown.pdf import PdfService
from libmyown.secrets import Secrets
from libmyown.service import LibraryService
from libmyown.site_config import ConfigStore, SiteConfig
from libmyown.snapshot import Snapshot, SnapshotHolder
from libmyown.worker import PostReceiveWorker

STALE_CHECK_SECONDS = 2.0


def effective_branch(site: SiteConfig) -> str | None:
    return site.stories_branch.strip() or None


@dataclass
class AppState:
    settings: Settings
    config: ConfigStore
    store: GitStore
    snapshots: SnapshotHolder
    worker: PostReceiveWorker
    pdf: PdfService
    templates: Jinja2Templates
    _last_stale_check: float = 0.0
    _stale_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def secrets(self) -> Secrets:
        return self.settings.secrets

    def site(self) -> SiteConfig:
        return self.config.get()

    def snapshot(self) -> Snapshot:
        """Current snapshot; notices pushes that bypassed git HTTP (e.g. on-disk edits)."""
        snapshot = self.snapshots.current
        now = time.monotonic()
        if now - self._last_stale_check < STALE_CHECK_SECONDS:
            return snapshot
        with self._stale_lock:
            if now - self._last_stale_check < STALE_CHECK_SECONDS:
                return self.snapshots.current
            self._last_stale_check = now
        branch = effective_branch(self.site())
        if branch != snapshot.branch or self.store.head_sha() != snapshot.head_sha:
            self.store.refresh()
            if branch != snapshot.branch or self.store.head_sha() != snapshot.head_sha:
                self.worker.request()
        return self.snapshots.current

    def missing_branch(self) -> str | None:
        """The configured published branch, when it does not exist (the site is then empty)."""
        branch = effective_branch(self.site())
        if branch and not self.store.branch_exists(branch):
            return branch
        return None

    def service(self, site: SiteConfig | None = None) -> LibraryService:
        return LibraryService(self.snapshot(), site or self.site())


def get_state(request: Request) -> AppState:
    return request.app.state.libmyown
