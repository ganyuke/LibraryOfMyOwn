"""GitStore reads, snapshot index, and per-snapshot history memoization."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from libmyown.git_repo import path_to_slug
from libmyown.git_store import GitStore
from libmyown.service import LibraryService
from libmyown.site_config import SiteConfig
from libmyown.snapshot import INDEX_VERSION, SnapshotHolder, build_snapshot, load_snapshot

from tests.support import make_data_dir


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = make_data_dir(tmp.name)
        self.store = GitStore(self.data_dir / "stories.git")
        self.addCleanup(self.store.close)
        self.index_path = self.data_dir / "work-index.json"

    def test_paths_and_slugs(self) -> None:
        snapshot = build_snapshot(self.store)
        self.assertIn("Series/The Long Draft.md", snapshot.paths)
        slug = path_to_slug(snapshot.paths[0])
        self.assertEqual(snapshot.resolve_slug(slug), snapshot.paths[0])

    def test_history_is_memoized_per_snapshot(self) -> None:
        snapshot = build_snapshot(self.store)
        first = snapshot.file_history("Series/The Long Draft.md", follow=True)
        second = snapshot.file_history("Series/The Long Draft.md", follow=True)
        self.assertIs(first, second)
        self.assertGreater(len(first), 5)

    def test_index_roundtrip_skips_rebuild(self) -> None:
        holder = SnapshotHolder(self.store, self.index_path)
        built = holder.load_or_build()
        self.assertTrue(self.index_path.is_file())
        loaded = load_snapshot(self.store, self.index_path)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.entries, built.entries)
        self.assertTrue(all(e.earliest_author_identity for e in loaded.entries.values()))

    def test_old_index_format_is_rebuilt(self) -> None:
        self.index_path.write_text('{"head_sha": "x", "entries": {}}', encoding="utf-8")
        self.assertIsNone(load_snapshot(self.store, self.index_path))
        snapshot = SnapshotHolder(self.store, self.index_path).load_or_build()
        self.assertTrue(snapshot.entries)
        self.assertIn(f'"version": {INDEX_VERSION}', self.index_path.read_text())

    def test_merged_history_memoized_and_revision_path(self) -> None:
        snapshot = build_snapshot(self.store)
        site = SiteConfig(
            published_directories={"Series"},
            history_merges={"Series/Sample Two.md": ["Series/Sample One.md"]},
        )
        service = LibraryService(snapshot, site)
        history = service.merged_history("Series/Sample Two.md")
        self.assertIs(history, LibraryService(snapshot, site).merged_history("Series/Sample Two.md"))
        sha = history[0].revision.sha
        self.assertEqual(service.revision_path("Series/Sample Two.md", sha), "Series/Sample Two.md")

    def test_repeated_refresh_cycles_do_not_leak_descriptors(self) -> None:
        fd_dir = Path("/proc/self/fd")
        if not fd_dir.is_dir():
            self.skipTest("file descriptor listing is unavailable")
        holder = SnapshotHolder(self.store, self.index_path)
        holder.load_or_build()

        def fd_count() -> int:
            return len(list(fd_dir.iterdir()))

        before = fd_count()
        for _ in range(25):
            self.store.refresh()
            snapshot = holder.rebuild(full=True)
            snapshot.file_history("Series/The Long Draft.md", follow=True)
        self.store.refresh()
        self.assertLessEqual(fd_count() - before, 2)


if __name__ == "__main__":
    unittest.main()
