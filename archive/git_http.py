from __future__ import annotations

import base64
from typing import Callable

from dulwich.server import Backend
from dulwich.web import make_wsgi_chain

from archive.git_repo import StoriesRepo


class SingleRepoBackend(Backend):
    def __init__(self, stories_repo: StoriesRepo) -> None:
        self._stories_repo = stories_repo

    def open_repository(self, path: str):
        return self._stories_repo.open()


def _basic_auth_ok(environ: dict, username: str, password: str) -> bool:
    auth_header = environ.get("HTTP_AUTHORIZATION", "")
    if not auth_header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
        user, pw = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return user == username and pw == password


class AuthenticatedGitApp:
    def __init__(
        self,
        repo: StoriesRepo,
        username: str | Callable[[], str],
        password: str | Callable[[], str],
        on_receive: Callable[[], None] | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._on_receive = on_receive

        backend = SingleRepoBackend(repo)
        self._inner = make_wsgi_chain(backend)

    def _credentials(self) -> tuple[str, str]:
        user = self._username() if callable(self._username) else self._username
        pw = self._password() if callable(self._password) else self._password
        return user, pw

    def __call__(self, environ, start_response):
        username, password = self._credentials()
        if not _basic_auth_ok(environ, username, password):
            start_response(
                "401 Unauthorized",
                [
                    ("WWW-Authenticate", 'Basic realm="Git"'),
                    ("Content-Type", "text/plain"),
                ],
            )
            return [b"Authentication required"]

        method = environ.get("REQUEST_METHOD", "GET")
        path_info = environ.get("PATH_INFO", "")

        result = self._inner(environ, start_response)
        if method == "POST" and path_info.endswith("/git-receive-pack"):
            chunks = list(result)
            if self._on_receive:
                self._on_receive()
            return chunks
        return result


def mount_path_for_git() -> str:
    return "/git/stories.git"
