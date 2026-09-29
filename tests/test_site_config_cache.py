"""Site config mtime cache."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from libmyown.site_config import (
    SiteConfig,
    clear_site_config_cache,
    default_site_config,
    load_site_config,
    save_site_config,
    seed_public_url,
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

    def test_seed_public_url_writes_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "site.json"
            seed_public_url(path, env_public_url="https://example.com")
            site = load_site_config(path)
            self.assertEqual(site.public_url, "https://example.com")

    def test_seed_public_url_ignored_when_already_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "site.json"
            save_site_config(
                path,
                SiteConfig(public_url="https://existing.example"),
            )
            seed_public_url(path, env_public_url="https://example.com")
            site = load_site_config(path)
            self.assertEqual(site.public_url, "https://existing.example")

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
