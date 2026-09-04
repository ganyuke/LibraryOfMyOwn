"""PDF download revision must match file history, not repo HEAD."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from libmyown.git_repo import StoriesRepo
from libmyown.service import LibraryService
from libmyown.site_config import SiteConfig, site_config_mtime
from libmyown.work_index import WorkIndexStore


class WorkDownloadRevTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        stories_src = root / "data" / "stories.git"
        if not stories_src.is_dir():
            self.skipTest("fixture stories.git missing")
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        data_dir = Path(tmp) / "data"
        shutil.copytree(stories_src, data_dir / "stories.git")
        site_path = data_dir / "site.json"
        site_path.write_text('{"published_directories": ["Series"]}', encoding="utf-8")
        self.repo = StoriesRepo(data_dir / "stories.git")
        self.site = SiteConfig(published_directories={"Series"})
        self.service = LibraryService(
            self.repo,
            self.site,
            WorkIndexStore(data_dir / "work-index.json", self.repo),
            site_mtime=site_config_mtime(site_path),
        )

    def test_latest_view_uses_file_revision_not_head(self) -> None:
        path = "Series/The Long Draft.md"
        head = self.repo.head_sha()
        latest = self.repo.latest_revision(path, follow=True)
        self.assertIsNotNone(head)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertNotEqual(latest.sha, head)

        view = self.service.work_at(path)
        self.assertIsNotNone(view)
        assert view is not None
        self.assertFalse(view.at_revision)
        self.assertEqual(view.commit_sha, latest.sha)
        self.assertEqual(view.short_sha, latest.short_sha)
        self.assertIsNotNone(self.service.revision_path(path, view.commit_sha))
        self.assertIsNone(self.service.revision_path(path, head))
        self.assertIsNotNone(self.service.revision_blob_text(path, view.commit_sha))


if __name__ == "__main__":
    unittest.main()
