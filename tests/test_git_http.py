"""Git smart HTTP: authentication, per-request repo ownership, and push housekeeping."""

from __future__ import annotations

import base64
import io
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import uvicorn

from libmyown.app import create_app
from libmyown.git_http import AuthenticatedGitApp
from libmyown.git_store import GC_PACK_THRESHOLD, GitStore

from tests.support import git, has_git, make_client, make_data_dir, make_settings


def _environ(path: str, *, auth: tuple[str, str] | None, query: str = "") -> dict:
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "SERVER_NAME": "test",
        "SERVER_PORT": "80",
        "wsgi.input": io.BytesIO(),
        "wsgi.url_scheme": "http",
    }
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        environ["HTTP_AUTHORIZATION"] = f"Basic {token}"
    return environ


class GitAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client = make_client(make_data_dir(tmp.name))
        self.secrets = self.client.app.state.libmyown.secrets

    def test_every_git_endpoint_requires_the_password(self) -> None:
        paths = [
            "/git/stories.git/info/refs?service=git-upload-pack",
            "/git/stories.git/info/refs",
            "/git/stories.git/HEAD",
            "/git/stories.git/objects/info/packs",
        ]
        bad = [None, ("git", "wrong"), ("nobody", self.secrets.git_password), ("git", "")]
        for path in paths:
            for auth in bad:
                response = self.client.get(path, auth=auth)
                self.assertEqual(response.status_code, 401, (path, auth))
                self.assertNotIn(b"PACK", response.content)
        ok = self.client.get(paths[0], auth=("git", self.secrets.git_password))
        self.assertEqual(ok.status_code, 200)

    def test_empty_password_never_authenticates(self) -> None:
        self.secrets.git_password = ""
        response = self.client.get("/git/stories.git/HEAD", auth=("git", ""))
        self.assertEqual(response.status_code, 401)


class ConcurrentGitRequestTests(unittest.TestCase):
    def test_finishing_one_request_does_not_close_another(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = GitStore(make_data_dir(tmp) / "stories.git")
            self.addCleanup(store.close)
            app = AuthenticatedGitApp(store, lambda user, password: True)
            auth = ("git", "x")

            def run(buffer: list[bytes]):
                def start_response(status, headers, exc_info=None):
                    return buffer.append

                response = app(
                    _environ("/info/refs", auth=auth, query="service=git-upload-pack"),
                    start_response,
                )
                buffer.extend(response)  # body done, but the server has not closed it yet
                return response

            first_body: list[bytes] = []
            first = run(first_body)
            held_by_first = store._gate._shared
            self.assertGreater(held_by_first, 0)
            second = run([])
            self.assertGreater(store._gate._shared, held_by_first)
            second.close()
            # Only the closed request released its repos; the other still holds its own.
            self.assertEqual(store._gate._shared, held_by_first)
            self.assertIn(b"refs/heads/master", b"".join(first_body))
            first.close()
            first.close()
            self.assertEqual(store._gate._shared, 0)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(has_git(), "git binary required")
class EndToEndPushTests(unittest.TestCase):
    """Real `git push`/`git clone` over HTTP against the running app."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.data_dir = make_data_dir(self.tmp)
        settings = make_settings(self.data_dir)
        self.app = create_app(settings)
        self.port = _free_port()
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(config)
        thread = threading.Thread(target=self.server.run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(setattr, self.server, "should_exit", True)
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                self.fail("server did not start")
            time.sleep(0.02)
        password = settings.secrets.git_password
        self.url = f"http://git:{password}@127.0.0.1:{self.port}/git/stories.git"
        self.anon_url = f"http://127.0.0.1:{self.port}/git/stories.git"

    def test_clone_requires_credentials(self) -> None:
        with self.assertRaises(Exception):
            git(self.tmp, "-c", "credential.helper=", "clone", "-q", self.anon_url, "anon")
        self.assertFalse((self.tmp / "anon" / "Series").exists())
        git(self.tmp, "clone", "-q", self.url, "authed")
        self.assertTrue((self.tmp / "authed" / "Series" / "Sample One.md").is_file())

    def test_many_pushes_keep_descriptors_and_packs_bounded(self) -> None:
        fd_dir = Path("/proc/self/fd")
        if not fd_dir.is_dir():
            self.skipTest("file descriptor listing is unavailable")
        work = self.tmp / "work"
        git(self.tmp, "clone", "-q", self.url, "work")
        state = self.app.state.libmyown
        story = work / "Series" / "Sample One.md"

        def fd_count() -> int:
            return len(list(fd_dir.iterdir()))

        before = None
        for index in range(30):
            story.write_text(story.read_text() + f"\nPush {index}.\n")
            git(work, "commit", "-qam", f"push {index}")
            git(work, "push", "-q", "origin", "master")
            if index == 4:
                self._wait_for_head(state, git(work, "rev-parse", "HEAD"))
                before = fd_count()
        head = git(work, "rev-parse", "HEAD")
        self._wait_for_head(state, head)
        time.sleep(0.2)
        assert before is not None
        self.assertLessEqual(fd_count() - before, 4)
        self.assertLessEqual(state.store.pack_count(), GC_PACK_THRESHOLD + 1)
        page = make_client_for(self.app).get("/works/series/sample-one")
        self.assertIn("Push 29.", page.text)

    @staticmethod
    def _wait_for_head(state, head: str) -> None:
        deadline = time.monotonic() + 20
        while state.snapshots.current.head_sha != head:
            if time.monotonic() > deadline:
                raise AssertionError("snapshot never caught up with the push")
            time.sleep(0.05)


def make_client_for(app):
    from starlette.testclient import TestClient

    return TestClient(app)


if __name__ == "__main__":
    unittest.main()
