"""The latest view is labelled with the file's last revision, not the branch tip."""

from __future__ import annotations

import tempfile
import unittest

from libmyown.git_store import GitStore
from libmyown.service import LibraryService
from libmyown.site_config import SiteConfig
from libmyown.snapshot import build_snapshot

from tests.support import make_data_dir


class WorkDownloadRevTests(unittest.TestCase):
    def test_latest_view_uses_file_revision_not_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_data_dir(tmp)
            store = GitStore(data_dir / "stories.git")
            self.addCleanup(store.close)
            snapshot = build_snapshot(store)
            service = LibraryService(snapshot, SiteConfig(published_directories={"Series"}))
            path = "Series/The Long Draft.md"
            entry = snapshot.entry(path)
            assert entry is not None and snapshot.head_sha
            self.assertNotEqual(entry.revision_sha, snapshot.head_sha)

            view = service.work_at(path)
            assert view is not None
            self.assertFalse(view.at_revision)
            self.assertEqual(view.commit_sha, entry.revision_sha)
            self.assertEqual(view.short_sha, entry.revision_short_sha)
            self.assertIsNotNone(service.revision_path(path, view.commit_sha))
            self.assertIsNone(service.revision_path(path, snapshot.head_sha))
            revision = service.resolve_revision(path, view.commit_sha, admin=False)
            assert revision is not None
            self.assertIsNotNone(service.revision_text(revision))


if __name__ == "__main__":
    unittest.main()
