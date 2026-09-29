from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from libmyown.secrets import Secrets, ensure_secrets, generate_git_password, set_admin_password
from libmyown.site_config import seed_public_url

# Load .env from the project root (parent of libmyown/).
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_PROJECT_ROOT / ".env")


class ConfigurationError(RuntimeError):
    """The instance cannot start safely with the current configuration."""


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    pdf_scripts: Path | None
    host: str
    port: int
    https_enabled: bool | None
    secrets: Secrets
    trusted_proxies: str = "127.0.0.1"
    pdf_cache_max_bytes: int = 512 * 1024 * 1024
    pdf_timeout_seconds: float = 180.0

    @property
    def secrets_path(self) -> Path:
        return self.data_dir / "secrets.json"

    @property
    def stories_repo(self) -> Path:
        return self.data_dir / "stories.git"

    @property
    def site_config_path(self) -> Path:
        return self.data_dir / "site.json"

    @property
    def pdf_cache_dir(self) -> Path:
        return self.data_dir / "pdf-cache"

    @property
    def maintenance_record_path(self) -> Path:
        return self.data_dir / "maintenance.json"

    @property
    def work_index_path(self) -> Path:
        return self.data_dir / "work-index.json"

    @property
    def templates_dir(self) -> Path:
        return Path(__file__).resolve().parent / "templates"

    @property
    def static_dir(self) -> Path:
        return Path(__file__).resolve().parent / "static"

    @property
    def session_secret(self) -> str:
        return self.secrets.session_secret

    @property
    def git_username(self) -> str:
        return self.secrets.git_username

    @property
    def git_password(self) -> str:
        return self.secrets.git_password


def _env_bool(name: str) -> bool | None:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return None
    return value in ("1", "true", "yes", "on")


def _env_number(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number, got {raw!r}") from exc


def require_admin_password(secrets: Secrets) -> None:
    if not secrets.is_configured:
        raise ConfigurationError(
            "No admin password is configured. Set ADMIN_PASSWORD in the environment "
            "(.env) for the first start; it is hashed into secrets.json and can be "
            "removed from .env afterwards."
        )


def generate_admin_password_if_missing(path: Path, secrets: Secrets) -> str | None:
    """First start without ADMIN_PASSWORD: create one and print it once to the service log."""
    if secrets.is_configured:
        return None
    password = generate_git_password()
    set_admin_password(path, secrets, password)
    print(
        "\n"
        "==================== libmyown first start ====================\n"
        f"  Generated admin password: {password}\n"
        "  Log in at /login and change it under Admin -> Security.\n"
        "  It is shown only once and kept only as a hash.\n"
        "==============================================================\n",
        file=sys.stderr,
        flush=True,
    )
    return password


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", "data")).expanduser().resolve()
    pdf_scripts_raw = os.environ.get("PDF_SCRIPTS", "").strip()
    if pdf_scripts_raw:
        pdf_scripts = Path(pdf_scripts_raw).expanduser().resolve()
    else:
        default_scripts = _PROJECT_ROOT / "pdf-scripts"
        pdf_scripts = default_scripts if default_scripts.is_dir() else None

    secrets = ensure_secrets(
        data_dir / "secrets.json",
        env_admin_password=os.environ.get("ADMIN_PASSWORD", "").strip(),
        env_git_password=os.environ.get("GIT_PASSWORD", "").strip(),
        env_git_username=os.environ.get("GIT_USERNAME", "").strip(),
    )
    generate_admin_password_if_missing(data_dir / "secrets.json", secrets)
    site_config_path = data_dir / "site.json"
    seed_public_url(
        site_config_path,
        env_public_url=os.environ.get("PUBLIC_URL", "").strip(),
    )

    return Settings(
        data_dir=data_dir,
        pdf_scripts=pdf_scripts,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        https_enabled=_env_bool("HTTPS_ENABLED"),
        secrets=secrets,
        trusted_proxies=os.environ.get("TRUSTED_PROXIES", "127.0.0.1").strip() or "127.0.0.1",
        pdf_cache_max_bytes=int(_env_number("PDF_CACHE_MAX_MB", 512) * 1024 * 1024),
        pdf_timeout_seconds=_env_number("PDF_BUILD_TIMEOUT", 180),
    )
