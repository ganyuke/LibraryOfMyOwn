"""Public compare access for merged history revisions."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from libmyown.app import create_app
from libmyown.config import Settings
from libmyown.git_repo import StoriesRepo, path_to_slug
from libmyown.secrets import ensure_secrets
from libmyown.site_config import load_site_config, save_site_config


class MergedHistoryCompareTests(unittest.TestCase):
    def test_public_compare_works_for_merged_history(self) -> None:
        root = Path(__file__).resolve().parents[1]
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
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
        site.history_merges = {"Series/Sample Two.md": ["Series/Sample One.md"]}
        save_site_config(data_dir / "site.json", site)

        secrets_path = data_dir / "secrets.json"
        secrets = ensure_secrets(secrets_path)

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

        dest_slug = path_to_slug("Series/Sample Two.md")
        repo = StoriesRepo(data_dir / "stories.git")
        source_revisions = repo.file_history("Series/Sample One.md", follow=False)
        dest_revisions = repo.file_history("Series/Sample Two.md", follow=True)
        self.assertTrue(source_revisions)
        self.assertTrue(dest_revisions)

        old_rev = source_revisions[-1].short_sha
        new_rev = dest_revisions[0].short_sha

        compare_url = (
            f"/works/{dest_slug}/history/compare?old={old_rev}&new={new_rev}"
        )
        response = client.get(compare_url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Changes from", response.text)
        self.assertNotIn("Log out", response.text)


if __name__ == "__main__":
    unittest.main()
