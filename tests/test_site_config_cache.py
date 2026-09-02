"""Site config mtime cache."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from libmyown.site_config import (
    clear_site_config_cache,
    default_site_config,
    load_site_config,
    save_site_config,
)


class SiteConfigCacheTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_site_config_cache()

    def test_load_site_config_reuses_cached_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "site.json"
            save_site_config(path, default_site_config())
            first = load_site_config(path)
            second = load_site_config(path)
            self.assertIs(first, second)

    def test_rewrite_reloads_site_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "site.json"
            save_site_config(path, default_site_config())
            first = load_site_config(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["site_title"] = "Changed"
            path.write_text(json.dumps(data) + "\n", encoding="utf-8")
            clear_site_config_cache()
            second = load_site_config(path)
            self.assertIsNot(first, second)
            self.assertEqual(second.site_title, "Changed")


if __name__ == "__main__":
    unittest.main()
