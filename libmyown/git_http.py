from __future__ import annotations

import base64
from typing import Callable

from dulwich.repo import Repo
from dulwich.server import Backend
from dulwich.web import make_wsgi_chain

from libmyown.git_repo import StoriesRepo


class SingleRepoBackend(Backend):
    def __init__(self, stories_repo: StoriesRepo) -> None:
        self._stories_repo = stories_repo
        self._detached: list[Repo] = []

    def open_repository(self, path: str):
        repo = self._stories_repo.open_detached()
        self._detached.append(repo)
        return repo

    def close_detached(self) -> None:
        while self._detached:
            self._detached.pop().close()


class _CloseWhenDone:
    def __init__(self, iterable, close) -> None:
        self._iterator = iter(iterable)
        self._iterable = iterable
        self._close = close
        self._closed = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self._iterator)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        closer = getattr(self._iterable, "close", None)
        if closer is not None:
            closer()
        self._close()


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

        self._backend = SingleRepoBackend(repo)
        self._inner = make_wsgi_chain(self._backend)

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

        try:
            result = self._inner(environ, start_response)
        except BaseException:
            self._backend.close_detached()
            raise
        if method == "POST" and path_info.endswith("/git-receive-pack"):
            try:
                chunks = list(result)
            finally:
                try:
                    closer = getattr(result, "close", None)
                    if closer is not None:
                        closer()
                finally:
                    self._backend.close_detached()
            if self._on_receive:
                self._on_receive()
            return chunks
        return _CloseWhenDone(result, self._backend.close_detached)


def mount_path_for_git() -> str:
    return "/git/stories.git"
