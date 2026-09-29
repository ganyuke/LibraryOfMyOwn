"""Single background thread for repository housekeeping.

- After a push: refresh the repo, rebuild the snapshot, and gc if packs piled up.
- Daily: a full gc even when the pack count is low, so force-pushed history
  and interrupted uploads do not linger. The last run is recorded in
  maintenance.json so restarts do not reset the schedule.
- On request from Admin -> Maintenance.

Requests coalesce: ten pushes in a row cause at most two rebuilds. Pushes never
wait for any of this; the site keeps serving the previous snapshot meanwhile.
A gc that finds a clone or push in flight is retried a minute later.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from libmyown.fsutil import atomic_write_text
from libmyown.git_store import GcResult, GitStore
from libmyown.snapshot import SnapshotHolder

logger = logging.getLogger(__name__)

GC_INTERVAL_SECONDS = 24 * 60 * 60
GC_RETRY_SECONDS = 60.0
FIRST_GC_DELAY_SECONDS = 10 * 60
GC_FAILURE_BACKOFF_SECONDS = 60 * 60


class PostReceiveWorker:
    def __init__(
        self,
        store: GitStore,
        snapshots: SnapshotHolder,
        *,
        resolve_branch: Callable[[], str | None] = lambda: None,
        record_path: Path | None = None,
    ) -> None:
        self._store = store
        self._snapshots = snapshots
        self._resolve_branch = resolve_branch
        self._record_path = record_path
        self._cond = threading.Condition()
        self._pending = False
        self._pending_full = False
        self._gc_requested = False
        self._gc_retry = False
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._gc_lock = threading.Lock()
        record = self.last_gc()
        self._last_gc_at: float | None = _parse_time(record.get("at")) if record else None
        self._started_at = time.time()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        with self._cond:
            if self._thread is not None:
                return
            self._stopping = False
            self._thread = threading.Thread(
                target=self._loop, name="libmyown-maintenance", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        with self._cond:
            thread = self._thread
            self._stopping = True
            self._cond.notify_all()
        if thread is not None:
            thread.join(timeout)
        with self._cond:
            self._thread = None

    # -- requests ------------------------------------------------------------

    def request(self, *, full: bool = False) -> None:
        """Schedule a refresh; runs inline when the worker thread is not running."""
        with self._cond:
            running = self._thread is not None
            if running:
                self._pending = True
                self._pending_full = self._pending_full or full
                self._cond.notify_all()
        if not running:
            self.run_once(full=full)

    def request_gc(self) -> None:
        """Schedule a full gc now; runs inline when the worker thread is not running."""
        with self._cond:
            running = self._thread is not None
            if running:
                self._gc_requested = True
                self._cond.notify_all()
        if not running:
            self.run_gc()

    # -- work ----------------------------------------------------------------

    def run_once(self, *, full: bool = False) -> None:
        self._store.refresh()
        self._store.repair_head()
        self._store.set_branch(self._resolve_branch())
        self._snapshots.rebuild(full=full)
        if self._store.needs_gc():
            self.run_gc()

    def run_gc(self) -> GcResult | None:
        with self._gc_lock:
            try:
                result = self._store.gc()
            except Exception:
                logger.exception("git gc failed; retrying in an hour")
                # Push the next daily run an hour out instead of retrying in a tight loop.
                self._last_gc_at = time.time() - GC_INTERVAL_SECONDS + GC_FAILURE_BACKOFF_SECONDS
                with self._cond:
                    self._gc_retry = False
                return None
            else:
                busy = result is None
            with self._cond:
                self._gc_retry = busy
            if busy:
                logger.info("git gc deferred: a clone or push is in progress")
                return None
            assert result is not None
            self._last_gc_at = time.time()
            self._write_record(result)
            return result

    # -- record ----------------------------------------------------------------

    def last_gc(self) -> dict[str, Any] | None:
        if self._record_path is None or not self._record_path.is_file():
            return None
        try:
            data = json.loads(self._record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        gc = data.get("last_gc") if isinstance(data, dict) else None
        return gc if isinstance(gc, dict) else None

    def _write_record(self, result: GcResult) -> None:
        if self._record_path is None:
            return
        payload = {
            "last_gc": {
                "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "method": result.method,
                "seconds": round(result.seconds, 2),
                "before": asdict(result.before),
                "after": asdict(result.after),
            }
        }
        try:
            atomic_write_text(self._record_path, json.dumps(payload, indent=2) + "\n")
        except OSError:
            logger.exception("could not write %s", self._record_path)

    # -- scheduling ------------------------------------------------------------

    def _next_daily_gc(self) -> float:
        if self._last_gc_at is None:
            return self._started_at + FIRST_GC_DELAY_SECONDS
        return self._last_gc_at + GC_INTERVAL_SECONDS

    def _seconds_until_due(self) -> float:
        wait = self._next_daily_gc() - time.time()
        if self._gc_retry:
            wait = min(wait, GC_RETRY_SECONDS)
        return max(wait, 0.0)

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not (self._pending or self._gc_requested or self._stopping):
                    timeout = self._seconds_until_due()
                    if timeout <= 0 or not self._cond.wait(timeout):
                        break
                if self._stopping:
                    return
                pending, full = self._pending, self._pending_full
                gc_requested, retry = self._gc_requested, self._gc_retry
                self._pending = self._pending_full = self._gc_requested = False
            try:
                if pending:
                    self.run_once(full=full)
                if gc_requested or retry or time.time() >= self._next_daily_gc():
                    self.run_gc()
            except Exception:
                logger.exception("maintenance pass failed")


def _parse_time(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None
