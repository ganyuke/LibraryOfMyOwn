"""Password-protected git smart HTTP for the single stories repository.

Every request builds its own dulwich backend, so the Repo it opens belongs to
that request alone and is closed when the WSGI response is closed. Nothing in
dulwich runs before authentication succeeds.
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Callable

from dulwich.repo import Repo
from dulwich.server import Backend
from dulwich.web import make_wsgi_chain

from libmyown.git_store import GitStore

GIT_MOUNT_PATH = "/git/stories.git"

logger = logging.getLogger("libmyown.auth")


class _RequestBackend(Backend):
    def __init__(self, store: GitStore) -> None:
        self._store = store
        self._repos: list[Repo] = []

    def open_repository(self, path: str):
        repo = self._store.open_for_http()
        self._repos.append(repo)
        return repo

    def release(self) -> None:
        while self._repos:
            self._store.release_http(self._repos.pop())


class _ReleasingResponse:
    """WSGI iterable that releases the request's repo exactly once, however it ends."""

    def __init__(self, result, on_close: Callable[[], None]) -> None:
        self._result = result
        self._iterator = iter(result)
        self._on_close = on_close
        self._closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iterator)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            closer = getattr(self._result, "close", None)
            if closer is not None:
                closer()
        finally:
            self._on_close()


def parse_basic_auth(environ: dict) -> tuple[str, str] | None:
    header = environ.get("HTTP_AUTHORIZATION", "")
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    user, sep, password = decoded.partition(":")
    if not sep:
        return None
    return user, password


def _unauthorized(start_response):
    start_response(
        "401 Unauthorized",
        [
            ("WWW-Authenticate", 'Basic realm="Git", charset="UTF-8"'),
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Cache-Control", "no-store"),
        ],
    )
    return [b"Authentication required"]


class AuthenticatedGitApp:
    def __init__(
        self,
        store: GitStore,
        check_credentials: Callable[[str, str], bool],
        on_receive: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self._check_credentials = check_credentials
        self._on_receive = on_receive

    def __call__(self, environ, start_response):
        credentials = parse_basic_auth(environ)
        if credentials is None or not self._check_credentials(*credentials):
            # Git clients first try with no credentials, then with the URL's username and
            # an empty password, before asking the credential helper. Only log real guesses.
            if credentials is not None and credentials[1]:
                logger.warning(
                    "authentication failure: git from %s", environ.get("REMOTE_ADDR", "unknown")
                )
            return _unauthorized(start_response)

        is_receive = (
            environ.get("REQUEST_METHOD") == "POST"
            and environ.get("PATH_INFO", "").endswith("/git-receive-pack")
        )
        backend = _RequestBackend(self._store)

        def finish() -> None:
            backend.release()
            if is_receive and self._on_receive is not None:
                self._on_receive()

        try:
            result = make_wsgi_chain(backend)(environ, start_response)
        except BaseException:
            finish()
            raise
        return _ReleasingResponse(result, finish)


def mount_path_for_git() -> str:
    return GIT_MOUNT_PATH
