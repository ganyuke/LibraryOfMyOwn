#!/usr/bin/env python3
"""Quick smoke test for LibraryOfMyOwn."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("GIT_PASSWORD", "test-git-password")
os.environ.setdefault("ORIGIN", "http://127.0.0.1:8000")

from starlette.testclient import TestClient

from archive.app import create_app
from archive.config import load_settings
from archive.git_repo import StoriesRepo, path_to_slug
from archive.pdf import discover_pdf_options
from archive.secrets import ensure_secrets, set_admin_password
from archive.site_config import SiteConfig, load_site_config, save_site_config


def parse_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise RuntimeError("csrf token not found in page")
    return match.group(1)


def main() -> int:
    settings = load_settings()
    secrets_path = settings.secrets_path
    secrets_backup = secrets_path.read_bytes() if secrets_path.is_file() else None
    if secrets_path.is_file():
        secrets_path.unlink()

    secrets = ensure_secrets(
        secrets_path,
        env_admin_password=os.environ["ADMIN_PASSWORD"],
        env_git_password=os.environ["GIT_PASSWORD"],
    )
    if not secrets.is_configured:
        set_admin_password(secrets_path, secrets, os.environ["ADMIN_PASSWORD"])

    settings = load_settings()
    app = create_app(settings)
    client = TestClient(app)

    admin_password = os.environ["ADMIN_PASSWORD"]
    git_password = settings.secrets.git_password

    checks = [
        ("/", 200, "Library of My Own"),
        ("/works/series/the-long-draft", 200, "The Long Draft"),
        ("/works/series/the-long-draft/history", 200, "History"),
        ("/works/series/the-long-draft/history", 200, "Compare selected revisions"),
    ]

    for path, code, needle in checks:
        r = client.get(path)
        if r.status_code != code or needle not in r.text:
            print(f"FAIL {path}: {r.status_code}")
            return 1
        print(f"OK {path}")

    repo = StoriesRepo(settings.stories_repo, branch=settings.stories_branch)
    history = repo.file_history("Series/The Long Draft.md")
    if len(history) >= 2:
        old_rev = history[1].short_sha
        r = client.get(f"/works/series/the-long-draft/r/{old_rev}")
        if r.status_code != 200:
            print(f"FAIL revision view: {r.status_code}")
            return 1
        if f'<span aria-current="page">{old_rev}</span>' not in r.text:
            print("FAIL revision breadcrumb")
            return 1
        print(f"OK revision breadcrumb {old_rev}")
        new_rev = history[0].short_sha
        r = client.get(
            f"/works/series/the-long-draft/history/compare?old={old_rev}&new={new_rev}"
        )
        if r.status_code != 200 or "Changes from" not in r.text:
            print(f"FAIL compare page: {r.status_code}")
            return 1
        print("OK compare page")

    work_page = client.get("/works/series/the-long-draft")
    pdf_options = discover_pdf_options(settings.pdf_scripts)
    if pdf_options:
        if pdf_options[0].id != "digital":
            print(f"FAIL expected digital first, got {pdf_options[0].id}")
            return 1
        for opt in pdf_options:
            if opt.label not in work_page.text:
                print(f"FAIL missing PDF label: {opt.label}")
                return 1
        first = pdf_options[0]
        r = client.get(f"/works/series/the-long-draft/pdf/{first.id}")
        if r.status_code != 200 or "pdf" not in r.headers.get("content-type", ""):
            print(f"FAIL pdf {first.id}: {r.status_code}")
            return 1
        print(f"OK pdf {first.id}")
    elif "Download" in work_page.text:
        print("FAIL Download row shown without PDF_SCRIPTS")
        return 1
    else:
        print("OK no PDF options (PDF_SCRIPTS unset)")

    r = client.get("/login")
    csrf = parse_csrf(r.text)
    client.post(
        "/login",
        data={"password": admin_password, "csrf_token": csrf},
        follow_redirects=True,
    )
    r = client.get("/admin")
    if r.status_code != 200:
        print("FAIL /admin")
        return 1
    print("OK /admin")

    r = client.get(
        "/git/stories.git/info/refs?service=git-upload-pack",
        auth=(settings.git_username, git_password),
    )
    if r.status_code != 200:
        print("FAIL git auth")
        return 1
    print("OK git auth")

    r = client.get("/git/stories.git/info/refs")
    if r.status_code != 401:
        print("FAIL git anon (expected 401)")
        return 1
    print("OK git anon blocked")

    r = client.get("/")
    csrf = parse_csrf(r.text)
    r = client.post(
        "/theme",
        data={"theme": "dark", "next": "/", "csrf_token": csrf},
        follow_redirects=False,
    )
    if r.status_code != 303:
        print("FAIL theme redirect")
        return 1
    if client.cookies.get("archive_theme") != "dark":
        print("FAIL theme cookie")
        return 1
    r = client.get("/")
    if 'data-theme="dark"' not in r.text:
        print("FAIL dark theme markup")
        return 1
    print("OK theme toggle")

    r = client.post("/theme", data={"theme": "dark", "next": "/"})
    if r.status_code != 403:
        print(f"FAIL csrf required: {r.status_code}")
        return 1
    print("OK csrf required")

    r = client.get("/admin/site")
    if r.status_code != 200 or "Public URL" not in r.text:
        print("FAIL /admin/site")
        return 1
    print("OK /admin/site")

    site_path = settings.site_config_path
    site_backup = SiteConfig.from_dict(load_site_config(site_path).to_dict())
    try:
        r = client.get("/admin/merge")
        csrf = parse_csrf(r.text)
        r = client.post(
            "/admin/merge",
            data={
                "action": "merge",
                "source": "Series/Sample One.md",
                "dest": "Series/Sample Two.md",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        if r.status_code != 303:
            print(f"FAIL history merge post: {r.status_code}")
            return 1

        source_slug = path_to_slug("Series/Sample One.md")
        dest_slug = path_to_slug("Series/Sample Two.md")
        r = client.get(f"/works/{source_slug}/history", follow_redirects=False)
        if r.status_code != 301 or f"/works/{dest_slug}/history" not in r.headers.get("location", ""):
            print("FAIL history merge redirect")
            return 1

        repo = StoriesRepo(settings.stories_repo, branch=settings.stories_branch)
        expected_revisions = len(
            {
                rev.sha
                for path in ("Series/Sample One.md", "Series/Sample Two.md")
                for rev in repo.file_history(path)
            }
        )
        r = client.get(f"/works/{dest_slug}/history")
        if r.status_code != 200:
            print(f"FAIL merged history page: {r.status_code}")
            return 1
        if r.text.count('class="history-radio"') // 2 != expected_revisions:
            print("FAIL merged history revision count")
            return 1
        r = client.get("/")
        if f'/works/{source_slug}"' in r.text:
            print("FAIL merged source still on index")
            return 1

        r = client.get("/admin/merge")
        csrf = parse_csrf(r.text)
        r = client.post(
            "/admin/merge",
            data={
                "action": "unmerge",
                "source": "Series/Sample One.md",
                "dest": "Series/Sample Two.md",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        if r.status_code != 303:
            print(f"FAIL history unmerge post: {r.status_code}")
            return 1
        site = load_site_config(site_path)
        if "Series/Sample One.md" in site.history_merges.get("Series/Sample Two.md", []):
            print("FAIL merge still present after unmerge")
            return 1
        r = client.get(f"/works/{source_slug}", follow_redirects=False)
        if r.status_code == 301:
            print("FAIL source slug still redirects after unmerge")
            return 1
        print("OK history merge")
    finally:
        save_site_config(site_path, site_backup)
        if secrets_backup is not None:
            secrets_path.write_bytes(secrets_backup)
        elif secrets_path.is_file():
            secrets_path.unlink()

    print("All smoke tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
