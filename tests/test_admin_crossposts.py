"""Admin crosspost save/load."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from libmyown.app import create_app
from libmyown.config import Settings
from libmyown.secrets import ensure_secrets, set_admin_password
from libmyown.site_config import load_site_config, save_site_config


class AdminCrosspostTests(unittest.TestCase):
    def test_save_shows_on_work_page(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            for name in ("secrets.json", "site.json", "work-index.json"):
                src = root / "data" / name
                if src.is_file():
                    (data_dir / name).write_bytes(src.read_bytes())
            stories_src = root / "data" / "stories.git"
            if stories_src.is_dir():
                shutil.copytree(stories_src, data_dir / "stories.git")

            site = load_site_config(data_dir / "site.json")
            site.published_directories = {"Series"}
            save_site_config(data_dir / "site.json", site)

            secrets_path = data_dir / "secrets.json"
            secrets = ensure_secrets(secrets_path, env_admin_password="admin123")
            set_admin_password(secrets_path, secrets, "admin123")

            settings = Settings(
                data_dir=data_dir,
                pdf_scripts=None,
                host="127.0.0.1",
                port=8000,
                https_enabled=None,
                stories_branch=None,
                secrets=secrets,
            )
            client = TestClient(create_app(settings))

            login_page = client.get("/login")
            csrf = re.search(
                r'name="csrf_token" value="([^"]+)"', login_page.text
            ).group(1)
            client.post(
                "/login",
                data={"csrf_token": csrf, "password": "admin123"},
                follow_redirects=True,
            )

            page = client.get("/admin/crossposts?story=series/sample-one")
            csrf = re.search(
                r'name="csrf_token" value="([^"]+)"', page.text
            ).group(1)
            response = client.post(
                "/admin/crossposts",
                data={
                    "csrf_token": csrf,
                    "story_path": "Series/Sample One.md",
                    "crosspost_label": "AO3",
                    "crosspost_url": "42424242",
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            self.assertIn("story=series%2Fsample-one", response.headers["location"])

            saved = json.loads((data_dir / "site.json").read_text())["crossposts"]
            self.assertEqual(
                saved["Series/Sample One.md"],
                [{"label": "AO3", "url": "42424242"}],
            )

            work = client.get("/works/series/sample-one")
            self.assertIn("archiveofourown.org/works/42424242", work.text)


if __name__ == "__main__":
    unittest.main()
