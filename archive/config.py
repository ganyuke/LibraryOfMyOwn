from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from archive.secrets import Secrets, ensure_secrets

# Load .env from the project root (parent of archive/).
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    pdf_scripts: Path | None
    host: str
    port: int
    https_enabled: bool | None
    stories_branch: str | None
    secrets: Secrets

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


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", "data")).expanduser().resolve()
    pdf_scripts_raw = os.environ.get("PDF_SCRIPTS", "").strip()
    if pdf_scripts_raw:
        pdf_scripts = Path(pdf_scripts_raw).expanduser().resolve()
    else:
        default_scripts = _PROJECT_ROOT / "pdf-scripts"
        pdf_scripts = default_scripts if default_scripts.is_dir() else None

    stories_branch_raw = os.environ.get("STORIES_BRANCH", "").strip()
    stories_branch = stories_branch_raw or None

    secrets = ensure_secrets(
        data_dir / "secrets.json",
        env_admin_password=os.environ.get("ADMIN_PASSWORD", "").strip(),
        env_git_password=os.environ.get("GIT_PASSWORD", "").strip(),
        env_git_username=os.environ.get("GIT_USERNAME", "").strip(),
    )

    return Settings(
        data_dir=data_dir,
        pdf_scripts=pdf_scripts,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        https_enabled=_env_bool("HTTPS_ENABLED"),
        stories_branch=stories_branch,
        secrets=secrets,
    )
