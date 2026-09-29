"""PdfService: shared builds, hard timeouts, atomic cache files, bounded cache size."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from libmyown.pdf import PdfService

COUNTING_SCRIPT = '''
import time
from pathlib import Path
label = "Count"
def build(input_md, output_pdf, work_dir, **kwargs):
    marker = Path(__file__).with_suffix(".count")
    with marker.open("a") as handle:
        handle.write("x")
    time.sleep(0.5)
    output_pdf.write_bytes(b"%PDF-" + input_md.read_bytes() + b"x" * 4000)
'''

SLOW_SCRIPT = '''
import time
label = "Slow"
def build(input_md, output_pdf, work_dir, **kwargs):
    time.sleep(30)
'''


class PdfServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.scripts = root / "scripts"
        self.scripts.mkdir()
        (self.scripts / "count.py").write_text(COUNTING_SCRIPT)
        (self.scripts / "slow.py").write_text(SLOW_SCRIPT)
        self.cache = root / "cache"

    def _get(self, service: PdfService, script: str, sha: str = "a" * 40) -> Path:
        return service.get(
            markdown_text="---\ntitle: T\n---\nbody\n",
            script_id=script,
            commit_sha=sha,
            path="S/t.md",
        )

    def test_concurrent_requests_share_one_build(self) -> None:
        service = PdfService(self.scripts, self.cache, max_concurrent=2)
        results: list[Path] = []
        threads = [
            threading.Thread(target=lambda: results.append(self._get(service, "count")))
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(results)), 1)
        self.assertEqual((self.scripts / "count.count").read_text(), "x")
        self.assertTrue(results[0].read_bytes().startswith(b"%PDF-"))
        self.assertFalse(any((self.cache / ".tmp").iterdir()))

    def test_timeout_kills_the_build(self) -> None:
        service = PdfService(self.scripts, self.cache, timeout_seconds=1)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self._get(service, "slow")
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(list(self.cache.rglob("*.pdf")), [])

    def test_cache_is_pruned_to_size(self) -> None:
        service = PdfService(self.scripts, self.cache, max_cache_bytes=6000)
        first = self._get(service, "count", sha="a" * 40)
        time.sleep(0.05)
        second = self._get(service, "count", sha="b" * 40)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())

    def test_unknown_script_is_rejected(self) -> None:
        service = PdfService(self.scripts, self.cache)
        with self.assertRaises(FileNotFoundError):
            self._get(service, "../../etc/passwd")


if __name__ == "__main__":
    unittest.main()
