from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archive.secrets import (
    ensure_secrets,
    generate_session_secret,
    hash_password,
    load_secrets,
    set_admin_password,
    verify_password,
)


class SecretsTests(unittest.TestCase):
    def test_password_hash_roundtrip(self) -> None:
        stored = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", stored))
        self.assertFalse(verify_password("wrong", stored))

    def test_ensure_secrets_generates_session_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.json"
            secrets = ensure_secrets(path)
            self.assertTrue(secrets.session_secret)
            self.assertTrue(secrets.git_password)
            self.assertIsNone(secrets.admin_password_hash)
            self.assertEqual(len(generate_session_secret()), len(secrets.session_secret))

    def test_bootstrap_admin_password_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.json"
            secrets = ensure_secrets(path, env_admin_password="bootstrap-me")
            self.assertIsNotNone(secrets.admin_password_hash)
            self.assertTrue(verify_password("bootstrap-me", secrets.admin_password_hash))

    def test_set_admin_password_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.json"
            secrets = ensure_secrets(path)
            set_admin_password(path, secrets, "new-secret")
            reloaded = load_secrets(path)
            assert reloaded is not None
            self.assertTrue(verify_password("new-secret", reloaded.admin_password_hash))


if __name__ == "__main__":
    unittest.main()
