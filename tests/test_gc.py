"""Repository gc: packing, pruning, temp-file cleanup, scheduling, and the admin trigger."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from libmyown import git_store
from libmyown.git_store import GitStore
from libmyown.snapshot import SnapshotHolder
from libmyown.worker import GC_INTERVAL_SECONDS, PostReceiveWorker

from tests.support import csrf_from, git, has_git, login, make_client, make_data_dir


@unittest.skipUnless(has_git(), "git binary required")
class GcTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data_dir = make_data_dir(self.root)
        self.bare = self.data_dir / "stories.git"
        # Keep every push as its own pack, like dulwich's receive-pack does.
        # Git 2.55 otherwise runs geometric maintenance after each push.
        git(self.bare, "config", "receive.unpackLimit", "1")
        git(self.bare, "config", "receive.autogc", "false")
        self.work = self.root / "work"
        git(self.root, "clone", "-q", str(self.bare), "work")
        self.store = GitStore(self.bare)
        self.addCleanup(self.store.close)

    def _push(self, count: int) -> None:
        story = self.work / "Series" / "Sample One.md"
        for index in range(count):
            story.write_text(story.read_text() + f"\nline {index}\n")
            git(self.work, "commit", "-qam", f"edit {index}")
            git(self.work, "push", "-q", "origin", "master")

    def test_gc_packs_everything_into_one(self) -> None:
        self._push(10)
        self.assertGreater(self.store.pack_count(), git_store.GC_PACK_THRESHOLD)
        self.assertTrue(self.store.needs_gc())
        result = self.store.gc()
        assert result is not None
        self.assertEqual(result.method, "git")
        self.assertEqual(self.store.pack_count(), 1)
        self.assertEqual(result.after.packs, 1)
        self.assertGreater(result.before.packs, result.after.packs)
        self.assertEqual(self.store.list_markdown_paths(self.store.head_sha())[0], "Series/Sample One.md")

    def test_force_pushed_history_is_pruned(self) -> None:
        self._push(1)
        orphan = git(self.work, "rev-parse", "HEAD")
        git(self.work, "reset", "-q", "--hard", "HEAD~1")
        git(self.work, "push", "-q", "--force", "origin", "master")
        # The reflog keeps force-pushed commits for 30 days (git's safety net); expire it
        # so this test checks that gc really deletes unreachable objects.
        git(self.bare, "reflog", "expire", "--expire-unreachable=now", "--all")
        with mock.patch.object(git_store, "GC_PRUNE_EXPIRE", "now"):
            self.assertIsNotNone(self.store.gc())
        with self.assertRaises(Exception):
            git(self.bare, "cat-file", "-e", orphan)

    def test_recent_unreachable_objects_survive_default_grace(self) -> None:
        self._push(1)
        orphan = git(self.work, "rev-parse", "HEAD")
        git(self.work, "reset", "-q", "--hard", "HEAD~1")
        git(self.work, "push", "-q", "--force", "origin", "master")
        self.assertIsNotNone(self.store.gc())
        git(self.bare, "cat-file", "-e", orphan)

    def test_stale_temp_files_removed_fresh_ones_kept(self) -> None:
        pack_dir = self.bare / "objects" / "pack"
        stale_tmp = self.bare / "objects" / "tmp_pack_stale"
        stale_pack = pack_dir / "pack-deadbeef.pack"
        fresh_tmp = self.bare / "objects" / "tmp_pack_fresh"
        for path in (stale_tmp, stale_pack, fresh_tmp):
            path.write_bytes(b"partial upload")
        old = time.time() - 2 * git_store.TEMP_FILE_GRACE_SECONDS
        os.utime(stale_tmp, (old, old))
        os.utime(stale_pack, (old, old))
        self.assertIsNotNone(self.store.gc())
        self.assertFalse(stale_tmp.exists())
        self.assertFalse(stale_pack.exists())
        self.assertTrue(fresh_tmp.exists())

    def test_gc_waits_for_transfers_in_flight(self) -> None:
        self._push(10)
        repo = self.store.open_for_http()
        try:
            self.assertIsNone(self.store.gc())
            self.assertGreater(self.store.pack_count(), 1)
        finally:
            self.store.release_http(repo)
        self.assertIsNotNone(self.store.gc())

    def test_dulwich_fallback_without_git_binary(self) -> None:
        self._push(10)
        real_which = git_store.shutil.which
        with mock.patch.object(
            git_store.shutil, "which", side_effect=lambda name: None if name == "git" else real_which(name)
        ):
            result = self.store.gc()
        assert result is not None
        self.assertEqual(result.method, "dulwich")
        self.assertLess(result.after.packs, result.before.packs)

    def test_gc_runs_at_low_priority(self) -> None:
        with mock.patch.object(git_store.subprocess, "run") as run:
            self.store.gc()
        command = run.call_args.args[0]
        if git_store.shutil.which("nice"):
            self.assertIn("nice", " ".join(command))
        if git_store.shutil.which("ionice"):
            self.assertIn("-c3", command)
        self.assertIn("gc", command)


@unittest.skipUnless(has_git(), "git binary required")
class WorkerScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = make_data_dir(tmp.name)
        self.store = GitStore(self.data_dir / "stories.git")
        self.addCleanup(self.store.close)
        self.record = self.data_dir / "maintenance.json"

    def _worker(self) -> PostReceiveWorker:
        holder = SnapshotHolder(self.store, self.data_dir / "work-index.json")
        return PostReceiveWorker(self.store, holder, record_path=self.record)

    def test_record_survives_restart(self) -> None:
        first = self._worker()
        self.assertIsNone(first.last_gc())
        self.assertIsNotNone(first.run_gc())
        saved = json.loads(self.record.read_text())["last_gc"]
        self.assertEqual(saved["method"], "git")
        self.assertIn("packs", saved["after"])

        restarted = self._worker()
        self.assertEqual(restarted.last_gc(), saved)
        due_in = restarted._next_daily_gc() - time.time()
        self.assertGreater(due_in, GC_INTERVAL_SECONDS - 60)

    def test_busy_gc_is_retried(self) -> None:
        worker = self._worker()
        repo = self.store.open_for_http()
        try:
            self.assertIsNone(worker.run_gc())
            self.assertTrue(worker._gc_retry)
            self.assertLessEqual(worker._seconds_until_due(), 60)
        finally:
            self.store.release_http(repo)
        self.assertIsNotNone(worker.run_gc())
        self.assertFalse(worker._gc_retry)

    def test_failed_gc_backs_off(self) -> None:
        worker = self._worker()
        with mock.patch.object(self.store, "gc", side_effect=RuntimeError("boom")):
            self.assertIsNone(worker.run_gc())
        due_in = worker._next_daily_gc() - time.time()
        self.assertGreater(due_in, 30 * 60)
        self.assertFalse(self.record.exists())


@unittest.skipUnless(has_git(), "git binary required")
class DanglingHeadTests(unittest.TestCase):
    def test_first_push_of_main_into_master_repo_is_served(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            data_dir.mkdir()
            bare = data_dir / "stories.git"
            git(root, "init", "-q", "--bare", "--initial-branch=master", str(bare))
            work = root / "work"
            work.mkdir()
            git(work, "init", "-q", "-b", "main")
            (work / "S").mkdir()
            (work / "S" / "a.md").write_text("---\ntitle: A\n---\ntext\n")
            git(work, "add", ".")
            git(work, "commit", "-qm", "first")
            git(work, "push", "-q", str(bare), "main")

            store = GitStore(bare)
            self.addCleanup(store.close)
            self.assertIsNone(store.head_sha())
            holder = SnapshotHolder(store, data_dir / "work-index.json")
            PostReceiveWorker(store, holder).run_once()
            self.assertEqual((bare / "HEAD").read_text().strip(), "ref: refs/heads/main")
            self.assertEqual(holder.current.paths, ["S/a.md"])
            self.assertIsNone(store.repair_head())

    def test_valid_head_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = GitStore(make_data_dir(tmp) / "stories.git")
            self.addCleanup(store.close)
            self.assertIsNone(store.repair_head())


class MaintenancePageTests(unittest.TestCase):
    def test_manual_gc_from_admin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(make_data_dir(tmp))
            login(client)
            page = client.get("/admin/maintenance")
            self.assertIn("Not tidied up yet", page.text)
            response = client.post(
                "/admin/maintenance",
                data={"csrf_token": csrf_from(page.text), "action": "run_git_gc"},
                follow_redirects=True,
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("Last tidied up", response.text)
            self.assertIn("1 pack", response.text)


if __name__ == "__main__":
    unittest.main()
