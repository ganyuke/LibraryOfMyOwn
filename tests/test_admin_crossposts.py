"""Admin crosspost save/load."""

from __future__ import annotations

import json
import tempfile
import unittest

from tests.support import csrf_from, login, make_client, make_data_dir


class AdminCrosspostTests(unittest.TestCase):
    def test_save_shows_on_work_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_data_dir(tmp)
            client = make_client(data_dir)
            login(client)

            page = client.get("/admin/crossposts?story=series/sample-one")
            response = client.post(
                "/admin/crossposts",
                data={
                    "csrf_token": csrf_from(page.text),
                    "story_path": "Series/Sample One.md",
                    "crosspost_label": "AO3",
                    "crosspost_url": "42424242",
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            self.assertIn("story=series%2Fsample-one", response.headers["location"])

            saved = json.loads((data_dir / "site.json").read_text())["crossposts"]
            self.assertEqual(saved["Series/Sample One.md"], [{"label": "AO3", "url": "42424242"}])

            work = client.get("/works/series/sample-one")
            self.assertIn("archiveofourown.org/works/42424242", work.text)


if __name__ == "__main__":
    unittest.main()
