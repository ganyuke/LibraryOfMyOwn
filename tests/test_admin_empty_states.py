"""Admin panels explain themselves when there is nothing to show instead of vanishing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from libmyown.site_config import SiteConfig, save_site_config

from tests.support import login, make_client, make_data_dir

NO_WORKS = "No works yet. Push some stories and they will show up here."


class EmptyRepositoryTests(unittest.TestCase):
    def test_pages_explain_there_are_no_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            save_site_config(data_dir / "site.json", SiteConfig(flags={}))
            client = make_client(data_dir)
            login(client)
            for path in ("/admin/crossposts", "/admin/continuity", "/admin/history"):
                page = client.get(path)
                self.assertEqual(page.status_code, 200, path)
                self.assertIn(NO_WORKS, page.text, path)
            authorship = client.get("/admin/authorship").text
            self.assertIn('name="exception_path"', authorship)
            self.assertNotIn(NO_WORKS, authorship)
            self.assertIn("No flags yet.", client.get("/admin/flags").text)
            merge = client.get("/admin/merge").text
            self.assertIn("Nothing merged yet.", merge)
            self.assertIn("No redirects yet.", merge)
            self.assertIn("No frontmatter fields found", client.get("/admin/metadata").text)


class AuthorshipExceptionTests(unittest.TestCase):
    def test_form_stays_with_an_empty_list_when_every_work_has_an_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_data_dir(tmp)
            client = make_client(data_dir)
            paths = client.app.state.libmyown.snapshot().paths
            site = SiteConfig(
                published_directories={"Series"},
                work_author_override={path: "Someone" for path in paths},
            )
            save_site_config(data_dir / "site.json", site)
            login(client)
            page = client.get("/admin/authorship").text
            select = page.split('name="exception_path"', 1)[1].split("</select>", 1)[0]
            self.assertIn("Choose…", select)
            self.assertEqual(select.count("<option"), 1)


if __name__ == "__main__":
    unittest.main()
