"""Startup requirements, sessions, headers, redirects, and on-disk safety."""

from __future__ import annotations

import base64
import copy
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from libmyown.app import create_app
from libmyown.config import ConfigurationError, Settings
from libmyown.fsutil import atomic_write_text
from libmyown.secrets import ensure_secrets, load_secrets, save_secrets
from libmyown.site_config import SiteConfig, load_site_config, save_site_config
from starlette.testclient import TestClient

from tests.support import ROOT, csrf_from, login, make_client, make_data_dir


class StartupTests(unittest.TestCase):
    def test_refuses_to_start_without_admin_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            secrets = ensure_secrets(data_dir / "secrets.json")
            settings = Settings(
                data_dir=data_dir, pdf_scripts=None, host="x", port=1,
                https_enabled=None, secrets=secrets,
            )
            with self.assertRaises(ConfigurationError):
                create_app(settings)

    def test_missing_admin_password_is_generated_and_printed_once(self) -> None:
        import contextlib
        import io

        from libmyown.config import load_settings
        from libmyown.secrets import verify_password

        with tempfile.TemporaryDirectory() as tmp:
            env = {"DATA_DIR": tmp, "ADMIN_PASSWORD": "", "PDF_SCRIPTS": ""}
            stderr = io.StringIO()
            with mock.patch.dict(os.environ, env), contextlib.redirect_stderr(stderr):
                settings = load_settings()
            printed = stderr.getvalue()
            self.assertIn("Generated admin password: ", printed)
            password = printed.split("Generated admin password: ", 1)[1].split()[0]
            self.assertTrue(verify_password(password, settings.secrets.admin_password_hash))
            self.assertNotIn(password, (Path(tmp) / "secrets.json").read_text())

            stderr = io.StringIO()
            with mock.patch.dict(os.environ, env), contextlib.redirect_stderr(stderr):
                load_settings()
            self.assertNotIn("Generated admin password", stderr.getvalue())

    def test_admin_password_env_fills_existing_unconfigured_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.json"
            first = ensure_secrets(path)
            self.assertFalse(first.is_configured)
            second = ensure_secrets(path, env_admin_password="long-enough")
            self.assertTrue(second.is_configured)
            self.assertEqual(second.session_secret, first.session_secret)

    def test_setup_route_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(make_data_dir(tmp))
            self.assertEqual(client.get("/setup").status_code, 404)


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = make_data_dir(tmp.name)
        self.client = make_client(self.data_dir)

    def test_log_everyone_out_takes_effect_without_restart(self) -> None:
        other = TestClient(self.client.app)
        login(self.client)
        login(other)
        self.assertEqual(other.get("/admin", follow_redirects=False).status_code, 200)
        page = self.client.get("/admin/security")
        self.client.post(
            "/admin/security",
            data={"csrf_token": csrf_from(page.text), "action": "rotate_sessions"},
            follow_redirects=False,
        )
        response = other.get("/admin", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")
        self.assertEqual(load_secrets(self.data_dir / "secrets.json").session_epoch, 1)

    def test_public_url_change_sets_cookie_security_without_restart(self) -> None:
        def session_cookie() -> str:
            response = TestClient(self.client.app).get("/login")
            return next(v for v in response.headers.get_list("set-cookie") if v.startswith("session="))

        self.assertNotIn("secure", session_cookie().lower())
        site = copy.deepcopy(load_site_config(self.data_dir / "site.json"))
        site.public_url = "https://stories.example"
        save_site_config(self.data_dir / "site.json", site)
        self.assertIn("secure", session_cookie().lower())

    def test_regenerated_git_password_never_enters_the_cookie(self) -> None:
        login(self.client)
        page = self.client.get("/admin/security")
        response = self.client.post(
            "/admin/security",
            data={"csrf_token": csrf_from(page.text), "action": "regenerate_git_password"},
        )
        new_password = load_secrets(self.data_dir / "secrets.json").git_password
        self.assertIn(new_password, response.text)
        cookie = self.client.cookies.get("session", "")
        payload = cookie.split(".")[0]
        decoded = base64.b64decode(payload + "=" * (-len(payload) % 4))
        self.assertNotIn(new_password.encode(), decoded)

    def test_wrong_password_rejected(self) -> None:
        csrf = csrf_from(self.client.get("/login").text)
        response = self.client.post("/login", data={"csrf_token": csrf, "password": "nope"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get("/admin", follow_redirects=False).status_code, 303)

    def test_notices_come_from_session_not_url(self) -> None:
        login(self.client)
        spoofed = self.client.get("/admin/security?error=Your+account+is+locked&message=Call+us")
        self.assertNotIn("Your account is locked", spoofed.text)
        self.assertNotIn("Call us", spoofed.text)
        page = self.client.get("/admin/security")
        response = self.client.post(
            "/admin/security",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "change_admin_password",
                "current_password": "wrong",
                "new_password": "long-enough-1",
                "new_password_confirm": "long-enough-1",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.headers["location"], "/admin/security")
        shown = self.client.get("/admin/security")
        self.assertIn("Current password is incorrect.", shown.text)
        self.assertNotIn("Current password is incorrect.", self.client.get("/admin/security").text)

    def test_failed_logins_are_logged(self) -> None:
        csrf = csrf_from(self.client.get("/login").text)
        with self.assertLogs("libmyown.auth", level="WARNING") as logs:
            self.client.post("/login", data={"csrf_token": csrf, "password": "nope"})
            self.client.get("/git/stories.git/HEAD", auth=("git", "nope"))
            # git's own empty-password probe before asking the credential helper
            self.client.get("/git/stories.git/HEAD", auth=("git", ""))
        self.assertEqual(len(logs.records), 2)
        self.assertTrue(all("authentication failure" in r.getMessage() for r in logs.records))

    def test_csrf_required(self) -> None:
        response = self.client.post("/theme", data={"theme": "dark", "next": "/"})
        self.assertEqual(response.status_code, 403)


class HeaderAndRedirectTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client = make_client(make_data_dir(tmp.name))

    def test_security_headers(self) -> None:
        response = self.client.get("/")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertNotIn("<script>", response.text)
        self.assertEqual(self.client.get("/login").headers["cache-control"], "no-store")

    def test_theme_redirect_stays_on_site(self) -> None:
        csrf = csrf_from(self.client.get("/").text)
        for target in ("//evil.example", "/\\evil.example", "https://evil.example"):
            response = self.client.post(
                "/theme",
                data={"theme": "dark", "next": target, "csrf_token": csrf},
                follow_redirects=False,
            )
            self.assertEqual(response.headers["location"], "/", target)

    def test_oversized_form_rejected(self) -> None:
        response = self.client.post("/login", content=b"a=" + b"x" * (2 * 1024 * 1024),
                                    headers={"content-type": "application/x-www-form-urlencoded"})
        self.assertEqual(response.status_code, 413)


class DiskSafetyTests(unittest.TestCase):
    def test_atomic_write_keeps_old_file_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "site.json"
            path.write_text("old", encoding="utf-8")
            with mock.patch("libmyown.fsutil.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    atomic_write_text(path, "new")
            self.assertEqual(path.read_text(encoding="utf-8"), "old")
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["site.json"])

    def test_secrets_file_is_private_from_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.json"
            ensure_secrets(path, env_admin_password="long-enough")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class CompatibilityTests(unittest.TestCase):
    def test_site_json_roundtrip_is_lossless(self) -> None:
        candidates = [ROOT / "data" / "site.json.example", ROOT / "data" / "site.json"]
        for source in candidates:
            if not source.is_file():
                continue
            original = json.loads(source.read_text(encoding="utf-8"))
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "site.json"
                save_site_config(path, SiteConfig.from_dict(original))
                resaved = json.loads(path.read_text(encoding="utf-8"))
                reloaded = SiteConfig.from_dict(resaved)
                self.assertEqual(reloaded, load_site_config(path))
                self.assertEqual(resaved, SiteConfig.from_dict(resaved).to_dict(), source)

    def test_secrets_without_session_epoch_load_and_save_unchanged(self) -> None:
        legacy = {
            "session_secret": "s",
            "admin_password_hash": None,
            "git_password": "g",
            "git_username": "git",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")
            loaded = load_secrets(path)
            assert loaded is not None
            self.assertEqual(loaded.session_epoch, 0)
            save_secrets(path, loaded)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), legacy)


if __name__ == "__main__":
    unittest.main()
