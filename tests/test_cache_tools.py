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


class CacheToolsTests(unittest.TestCase):
    def test_clear_pdf_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            work_dir = cache_dir / "abc123" / "Series_Example.md" / "digital"
            work_dir.mkdir(parents=True)
            pdf = work_dir / "example.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            self.assertEqual(pdf_cache_stats(cache_dir).pdf_count, 1)
            removed = clear_pdf_cache(cache_dir)
            self.assertEqual(removed, 1)
            self.assertEqual(pdf_cache_stats(cache_dir).pdf_count, 0)

    def test_format_bytes(self) -> None:
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(2048), "2.0 KB")


class RuntimeCacheClearTests(unittest.TestCase):
    def test_clear_runtime_caches_rebuilds_snapshot(self) -> None:
        from libmyown.cache_tools import clear_runtime_caches
        from libmyown.git_store import GitStore
        from libmyown.snapshot import SnapshotHolder
        from tests.support import make_data_dir

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_data_dir(tmp)
            store = GitStore(data_dir / "stories.git")
            self.addCleanup(store.close)
            holder = SnapshotHolder(store, data_dir / "work-index.json")
            before = holder.load_or_build()
            clear_runtime_caches(store, holder)
            after = holder.current
            self.assertIsNot(before, after)
            self.assertEqual(before.entries, after.entries)


if __name__ == "__main__":
    unittest.main()
