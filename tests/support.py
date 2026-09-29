"""Shared fixtures: a seeded stories.git built once from fixtures/sample-stories."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from starlette.testclient import TestClient

from libmyown.app import create_app
from libmyown.config import Settings
from libmyown.secrets import ensure_secrets
from libmyown.site_config import SiteConfig, save_site_config

ROOT = Path(__file__).resolve().parents[1]
ADMIN_PASSWORD = "admin-pass-123"

_template_repo: Path | None = None


def _seeded_template() -> Path:
    global _template_repo
    if _template_repo is None:
        spec = importlib.util.spec_from_file_location("seed_sample", ROOT / "scripts" / "seed_sample.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        target = Path(tempfile.mkdtemp(prefix="libmyown-seed-")) / "stories.git"
        module.seed_repository(target, ROOT / "fixtures" / "sample-stories")
        _template_repo = target
    return _template_repo


def make_data_dir(tmp: str | Path, site: SiteConfig | None = None) -> Path:
    data_dir = Path(tmp) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_seeded_template(), data_dir / "stories.git")
    save_site_config(data_dir / "site.json", site or SiteConfig(published_directories={"Series"}))
    return data_dir


def make_settings(data_dir: Path, *, pdf_scripts: Path | None = None) -> Settings:
    secrets = ensure_secrets(data_dir / "secrets.json", env_admin_password=ADMIN_PASSWORD)
    return Settings(
        data_dir=data_dir,
        pdf_scripts=pdf_scripts,
        host="127.0.0.1",
        port=8000,
        https_enabled=None,
        secrets=secrets,
    )


def make_client(data_dir: Path, *, pdf_scripts: Path | None = None) -> TestClient:
    return TestClient(create_app(make_settings(data_dir, pdf_scripts=pdf_scripts)))


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found"
    return match.group(1)


def login(client: TestClient, password: str = ADMIN_PASSWORD) -> None:
    csrf = csrf_from(client.get("/login").text)
    response = client.post(
        "/login", data={"csrf_token": csrf, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303, response.text


def git(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "Writer",
            "GIT_AUTHOR_EMAIL": "writer@example.com",
            "GIT_COMMITTER_NAME": "Writer",
            "GIT_COMMITTER_EMAIL": "writer@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )
    return result.stdout.strip()


def has_git() -> bool:
    return shutil.which("git") is not None
