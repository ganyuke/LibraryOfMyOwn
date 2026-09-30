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


class _TwoBranchRepo(unittest.TestCase):
    """`published` holds the public text; `main` has a newer, secret draft."""

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



@unittest.skipUnless(has_git(), "git binary required")
class RevisionAccessTests(_TwoBranchRepo):
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


@unittest.skipUnless(has_git(), "git binary required")
class PublishedBranchSettingTests(_TwoBranchRepo):
    """Admin -> Site settings: pick the published branch from the branches that exist."""

    def _site_form(self) -> dict:
        page = self.client.get("/admin/site").text
        return {"csrf_token": csrf_from(page), "site_title": "T", "git_username": "git"}

    def test_dropdown_lists_branches_and_what_is_served(self) -> None:
        login(self.client)
        page = self.client.get("/admin/site").text
        self.assertIn('<option value="main">main</option>', page)
        self.assertIn('<option value="published" selected>published</option>', page)
        self.assertIn(
            f"Serving <strong>published</strong> to the public at <code>{self.second[:7]}</code>", page
        )

    def test_choosing_a_branch_publishes_it_and_moves_head(self) -> None:
        login(self.client)
        form = self._site_form() | {"stories_branch": "main"}
        response = self.client.post("/admin/site", data=form, follow_redirects=True)
        self.assertIn("Settings saved.", response.text)
        self.assertEqual(json.loads(self.site_path.read_text())["stories_branch"], "main")
        head = (self.data_dir / "stories.git" / "HEAD").read_text().strip()
        self.assertEqual(head, "ref: refs/heads/main")
        self.assertIn("SECRET DRAFT", self.client.get("/works/s/story").text)

    def test_unknown_branch_is_rejected(self) -> None:
        login(self.client)
        form = self._site_form() | {"stories_branch": "nope"}
        response = self.client.post("/admin/site", data=form, follow_redirects=True)
        self.assertIn("The branch nope can&#39;t be found.", response.text)
        self.assertEqual(json.loads(self.site_path.read_text())["stories_branch"], "published")

    def test_missing_branch_shows_nothing_and_warns_admin(self) -> None:
        save_site_config(
            self.site_path, SiteConfig(stories_branch="gone", published_directories={"S"})
        )
        self.assertNoLeak("/works/s/story")
        self.assertNotIn(b"Story", self.client.get("/").content)
        self.assertNotIn(b"be found", self.client.get("/").content)
        login(self.client)
        admin = self.client.get("/admin").text
        self.assertIn("published branch <strong>gone</strong> can't be found", admin)
        self.assertIn('<option value="gone" selected>gone (missing)</option>', self.client.get("/admin/site").text)
