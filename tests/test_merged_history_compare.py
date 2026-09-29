"""Public compare access for merged history revisions."""

from __future__ import annotations

import tempfile
import unittest

from libmyown.git_repo import path_to_slug
from libmyown.site_config import SiteConfig

from tests.support import make_client, make_data_dir


class MergedHistoryCompareTests(unittest.TestCase):
    def test_public_compare_works_for_merged_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = SiteConfig(
                published_directories={"Series"},
                history_merges={"Series/Sample Two.md": ["Series/Sample One.md"]},
            )
            data_dir = make_data_dir(tmp, site)
            client = make_client(data_dir)
            snapshot = client.app.state.libmyown.snapshot()
            source_revisions = snapshot.file_history("Series/Sample One.md", follow=False)
            dest_revisions = snapshot.file_history("Series/Sample Two.md", follow=True)
            self.assertTrue(source_revisions)
            self.assertTrue(dest_revisions)

            dest_slug = path_to_slug("Series/Sample Two.md")
            old_rev = source_revisions[-1].short_sha
            new_rev = dest_revisions[0].short_sha
            response = client.get(f"/works/{dest_slug}/history/compare?old={old_rev}&new={new_rev}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("Changes from", response.text)
            self.assertNotIn("Log out", response.text)


if __name__ == "__main__":
    unittest.main()
