from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


@dataclass
class Secrets:
    session_secret: str
    admin_password_hash: str | None
    git_password: str
    git_username: str = "git"
    created_at: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.admin_password_hash)

    def git_remote_url(self, origin: str) -> str:
        return f"{origin.rstrip('/')}/git/stories.git"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return "scrypt$" + base64.b64encode(salt + digest).decode("ascii")


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash.startswith("scrypt$"):
        return False
    try:
        raw = base64.b64decode(stored_hash.split("$", 1)[1])
    except (ValueError, IndexError):
        return False
    if len(raw) != 16 + SCRYPT_DKLEN:
        return False
    salt, expected = raw[:16], raw[16:]
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return hmac.compare_digest(digest, expected)


def generate_git_password() -> str:
    return secrets.token_urlsafe(24)


def generate_session_secret() -> str:
    return secrets.token_urlsafe(32)


def _write_secrets(path: Path, secrets_data: Secrets) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_secret": secrets_data.session_secret,
        "admin_password_hash": secrets_data.admin_password_hash,
        "git_password": secrets_data.git_password,
        "git_username": secrets_data.git_username,
        "created_at": secrets_data.created_at or _now_iso(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_secrets(path: Path) -> Secrets | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return Secrets(
        session_secret=str(data.get("session_secret", "")),
        admin_password_hash=data.get("admin_password_hash"),
        git_password=str(data.get("git_password", "")),
        git_username=str(data.get("git_username", "git") or "git"),
        created_at=str(data.get("created_at", "")),
    )


def save_secrets(path: Path, secrets_data: Secrets) -> None:
    if not secrets_data.created_at:
        secrets_data.created_at = _now_iso()
    _write_secrets(path, secrets_data)


def ensure_secrets(
    path: Path,
    *,
    env_admin_password: str = "",
    env_git_password: str = "",
    env_git_username: str = "",
) -> Secrets:
    existing = load_secrets(path)
    if existing is not None:
        updated = False
        if not existing.session_secret:
            existing.session_secret = generate_session_secret()
            updated = True
        if not existing.git_password:
            existing.git_password = env_git_password or generate_git_password()
            updated = True
        if env_git_username and existing.git_username != env_git_username:
            existing.git_username = env_git_username
            updated = True
        if updated:
            save_secrets(path, existing)
        return existing

    admin_hash: str | None = None
    if env_admin_password:
        admin_hash = hash_password(env_admin_password)

    git_password = env_git_password or generate_git_password()
    git_username = env_git_username or "git"

    created = Secrets(
        session_secret=generate_session_secret(),
        admin_password_hash=admin_hash,
        git_password=git_password,
        git_username=git_username,
        created_at=_now_iso(),
    )
    save_secrets(path, created)
    return created


def set_admin_password(path: Path, secrets_data: Secrets, password: str) -> Secrets:
    secrets_data.admin_password_hash = hash_password(password)
    save_secrets(path, secrets_data)
    return secrets_data


def rotate_git_password(path: Path, secrets_data: Secrets) -> tuple[Secrets, str]:
    secrets_data.git_password = generate_git_password()
    save_secrets(path, secrets_data)
    return secrets_data, secrets_data.git_password


def rotate_session_secret(path: Path, secrets_data: Secrets) -> Secrets:
    secrets_data.session_secret = generate_session_secret()
    save_secrets(path, secrets_data)
    return secrets_data
