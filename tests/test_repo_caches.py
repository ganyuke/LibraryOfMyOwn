"""StoriesRepo path and slug caches."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from libmyown.git_repo import StoriesRepo, path_to_slug


class RepoPathCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        stories_src = root / "data" / "stories.git"
        if not stories_src.is_dir():
            self.skipTest("fixture stories.git missing")
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        data_dir = Path(tmp) / "data"
        shutil.copytree(stories_src, data_dir / "stories.git")
        self.repo = StoriesRepo(data_dir / "stories.git")

    def test_list_markdown_paths_reuses_cache(self) -> None:
        first = self.repo.list_markdown_paths()
        second = self.repo.list_markdown_paths()
        self.assertIs(first, second)

    def test_invalidate_clears_path_and_slug_caches(self) -> None:
        paths = self.repo.list_markdown_paths()
        slug_map = self.repo.slug_map()
        self.assertTrue(paths)
        self.assertTrue(slug_map)
        self.repo.invalidate()
        self.assertIsNone(self.repo._paths_cache)
        self.assertIsNone(self.repo._slug_map_cache)
        reloaded = self.repo.list_markdown_paths()
        self.assertEqual(reloaded, paths)

    def test_resolve_path_slug(self) -> None:
        paths = self.repo.list_markdown_paths()
        slug = path_to_slug(paths[0])
        self.assertEqual(self.repo.resolve_path_slug(slug), paths[0])


if __name__ == "__main__":
    unittest.main()
