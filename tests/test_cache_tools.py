"""Cache maintenance helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from libmyown.cache_tools import (
    clear_pdf_cache,
    format_bytes,
    pdf_cache_stats,
)
from libmyown.git_repo import StoriesRepo


class CacheToolsTests(unittest.TestCase):
    def test_clear_pdf_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            work_dir = cache_dir / "abc123" / "Series_Example.md" / "digital"
            work_dir.mkdir(parents=True)
            pdf = work_dir / "example.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            self.assertEqual(pdf_cache_stats(cache_dir).entries, 1)
            removed = clear_pdf_cache(cache_dir)
            self.assertEqual(removed, 1)
            self.assertEqual(pdf_cache_stats(cache_dir).entries, 0)

    def test_format_bytes(self) -> None:
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(2048), "2.0 KB")


class RuntimeCacheClearTests(unittest.TestCase):
    def test_clear_runtime_caches_invalidates_repo_paths(self) -> None:
        from libmyown.cache_tools import clear_runtime_caches
        import shutil

        root = Path(__file__).resolve().parents[1]
        stories_src = root / "data" / "stories.git"
        if not stories_src.is_dir():
            self.skipTest("fixture stories.git missing")
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "stories.git"
            shutil.copytree(stories_src, repo_path)
            repo = StoriesRepo(repo_path)
            paths = repo.list_markdown_paths()
            self.assertIsNotNone(repo._paths_cache)
            clear_runtime_caches(repo)
            self.assertIsNone(repo._paths_cache)
            self.assertEqual(repo.list_markdown_paths(), paths)


if __name__ == "__main__":
    unittest.main()
