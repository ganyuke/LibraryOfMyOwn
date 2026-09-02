"""Merged history service cache."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from libmyown.git_repo import StoriesRepo
from libmyown.service import LibraryService, clear_merged_history_cache
from libmyown.site_config import SiteConfig, site_config_mtime


class MergedHistoryCacheTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_merged_history_cache()

    def test_revision_path_reuses_merged_history_cache(self) -> None:
        root = Path(__file__).resolve().parents[1]
        stories_src = root / "data" / "stories.git"
        if not stories_src.is_dir():
            self.skipTest("fixture stories.git missing")
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        data_dir = Path(tmp) / "data"
        shutil.copytree(stories_src, data_dir / "stories.git")
        site_path = data_dir / "site.json"
        site_path.write_text("{}", encoding="utf-8")

        repo = StoriesRepo(data_dir / "stories.git")
        site = SiteConfig(
            published_directories={"Series"},
            history_merges={"Series/Sample Two.md": ["Series/Sample One.md"]},
        )
        service = LibraryService(
            repo,
            site,
            site_mtime=site_config_mtime(site_path),
        )
        history = service.merged_history("Series/Sample Two.md")
        self.assertTrue(history)
        sha = history[0].revision.sha
        first = service.revision_path("Series/Sample Two.md", sha)
        second = service.revision_path("Series/Sample Two.md", sha)
        self.assertEqual(first, second)
        self.assertTrue(first)


if __name__ == "__main__":
    unittest.main()
