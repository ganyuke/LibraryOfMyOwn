"""Revision URLs must never reach content outside the work's visible history."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from libmyown.secrets import ensure_secrets
from libmyown.site_config import SiteConfig, save_site_config

from tests.support import (
    ADMIN_PASSWORD,
    csrf_from,
    git,
    has_git,
    login,
    make_client,
)

DUMP_SCRIPT = '''
label = "Dump"
def build(input_md, output_pdf, work_dir, **kwargs):
    output_pdf.write_bytes(b"%PDF-dump\\n" + input_md.read_bytes())
'''


@unittest.skipUnless(has_git(), "git binary required")
class RevisionAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        work = root / "work"
        work.mkdir()
        git(work, "init", "-q", "-b", "main")
        (work / "S").mkdir()
        story = work / "S" / "story.md"
        story.write_text("---\ntitle: Story\n---\nfirst public text\n")
        git(work, "add", ".")
        git(work, "commit", "-qm", "first")
        self.first = git(work, "rev-parse", "HEAD")
        story.write_text("---\ntitle: Story\n---\nsecond public text\n")
        git(work, "commit", "-qam", "second")
        self.second = git(work, "rev-parse", "HEAD")
        git(work, "branch", "published")
        story.write_text("---\ntitle: Story\n---\nSECRET DRAFT\n")
        git(work, "commit", "-qam", "draft")
        self.draft = git(work, "rev-parse", "HEAD")

        data_dir = root / "data"
        data_dir.mkdir()
        git(root, "clone", "-q", "--bare", str(work), str(data_dir / "stories.git"))
        self.site_path = data_dir / "site.json"
        save_site_config(
            self.site_path,
            SiteConfig(stories_branch="published", published_directories={"S"}),
        )
        ensure_secrets(data_dir / "secrets.json", env_admin_password=ADMIN_PASSWORD)
        scripts = root / "scripts"
        scripts.mkdir()
        (scripts / "dump.py").write_text(DUMP_SCRIPT)
        self.data_dir = data_dir
        self.client = make_client(data_dir, pdf_scripts=scripts)

    def assertNoLeak(self, url: str) -> None:
        response = self.client.get(url)
        self.assertNotIn(b"SECRET", response.content, url)
        self.assertEqual(response.status_code, 404, url)

    def test_latest_view_uses_configured_branch(self) -> None:
        response = self.client.get("/works/s/story")
        self.assertEqual(response.status_code, 200)
        self.assertIn("second public text", response.text)

    def test_refs_and_other_branches_do_not_resolve(self) -> None:
        for rev in ("HEAD", "main", "refs%2Fheads%2Fmain", self.draft, self.draft[:7]):
            self.assertNoLeak(f"/works/s/story/r/{rev}")
            self.assertNoLeak(f"/works/s/story/pdf/dump?rev={rev}")
            self.assertNoLeak(f"/works/s/story/history/compare?old={self.first[:7]}&new={rev}")

    def test_pdf_of_visible_revisions_still_works(self) -> None:
        latest = self.client.get("/works/s/story/pdf/dump")
        self.assertEqual(latest.status_code, 200)
        self.assertIn(b"second public text", latest.content)
        older = self.client.get(f"/works/s/story/pdf/dump?rev={self.first[:7]}")
        self.assertEqual(older.status_code, 200)
        self.assertIn(b"first public text", older.content)

    def test_suppressed_revision_hidden_from_public_only(self) -> None:
        data = json.loads(self.site_path.read_text())
        data["suppressed_commits"] = {"S/story.md": [self.first]}
        self.site_path.write_text(json.dumps(data))
        self.assertEqual(self.client.get(f"/works/s/story/r/{self.first[:7]}").status_code, 404)
        self.assertEqual(
            self.client.get(f"/works/s/story/pdf/dump?rev={self.first}").status_code, 404
        )
        login(self.client)
        self.assertEqual(self.client.get(f"/works/s/story/r/{self.first[:7]}").status_code, 200)

    def test_unpublished_work_revisions_hidden(self) -> None:
        save_site_config(self.site_path, SiteConfig(stories_branch="published"))
        self.assertEqual(self.client.get(f"/works/s/story/r/{self.second[:7]}").status_code, 404)
        self.assertEqual(self.client.get("/works/s/story/pdf/dump").status_code, 404)


@unittest.skipUnless(has_git(), "git binary required")
class MergeCommitHistoryTests(unittest.TestCase):
    def test_history_through_a_merge_commit_renders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            git(work, "init", "-q", "-b", "master")
            (work / "S").mkdir()
            (work / "S" / "a.md").write_text("---\ntitle: A\n---\none\n")
            git(work, "add", ".")
            git(work, "commit", "-qm", "base")
            git(work, "checkout", "-qb", "side")
            (work / "S" / "a.md").write_text("---\ntitle: A\n---\none\ntwo\n")
            git(work, "commit", "-qam", "side edit")
            git(work, "checkout", "-q", "master")
            (work / "S" / "b.md").write_text("---\ntitle: B\n---\nother\n")
            git(work, "add", ".")
            git(work, "commit", "-qm", "main edit")
            git(work, "merge", "-q", "--no-ff", "-m", "merge side", "side")
            data_dir = root / "data"
            data_dir.mkdir()
            git(root, "clone", "-q", "--bare", str(work), str(data_dir / "stories.git"))
            save_site_config(data_dir / "site.json", SiteConfig(published_directories={"S"}))
            ensure_secrets(data_dir / "secrets.json", env_admin_password=ADMIN_PASSWORD)
            client = make_client(data_dir)
            self.assertEqual(client.get("/").status_code, 200)
            history = client.get("/works/s/a/history")
            self.assertEqual(history.status_code, 200, history.text)
            self.assertIn("side edit", history.text)
            csrf_from(client.get("/login").text)


if __name__ == "__main__":
    unittest.main()
