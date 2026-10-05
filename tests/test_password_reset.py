import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.application import Settings
from app.auth import hash_password
from app.mailer import MailSettings
from app.webapp import RegionalRssWebApplication
from test_application import ROOT, call_app


class PasswordResetTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_password = "old-password-long-enough"
        self.app = RegionalRssWebApplication(Settings(
            sources_dir=ROOT / "sources",
            cache_path=Path(self.temp.name) / "cache.sqlite3",
            accounts_path=Path(self.temp.name) / "accounts.sqlite3",
            public_base_url="https://rss.example.com",
            user_agent="test",
            timeout_seconds=1,
            max_response_bytes=10000,
            allow_private_hosts=False,
            site_title="RegionalRSS",
            session_secret="password-reset-test-secret-more-than-32-chars",
            allow_registration=True,
            mail=MailSettings(
                host="smtp.example.com",
                from_address="rss@example.com",
                username="smtp-user",
                password="smtp-secret",
            ),
        ))
        self.app.accounts.create(
            "Alice", hash_password(self.old_password), email="alice@example.com"
        )
        with patch("app.account_store.time.time", return_value=1000):
            self.app.accounts.confirm_email(
                self.app.accounts.issue_verification("Alice")
            )
        self.cookie = self.app.user_sessions.create_cookie(
            "Alice", secure=True
        ).split(";", 1)[0]
        patcher = patch.object(self.app.mailer, "send_password_reset")
        self.sender = patcher.start()
        self.addCleanup(patcher.stop)

    def request_reset(self, email="alice@example.com"):
        return call_app(
            self.app,
            "/password-forgot",
            method="POST",
            form={"email": email},
            forwarded_proto="https",
        )

    def test_login_links_to_password_reset(self):
        status, headers, body = call_app(self.app, "/login")
        self.assertEqual("200 OK", status)
        self.assertIn(b'href="/password-forgot"', body)
        self.assertEqual("no-store", headers["Cache-Control"])

    def test_request_does_not_reveal_whether_account_exists(self):
        known = self.request_reset()
        self.assertEqual("200 OK", known[0])
        self.assertIn("Falls zu dieser Adresse".encode(), known[2])
        self.sender.assert_called_once()
        self.sender.reset_mock()

        unknown = self.request_reset("nobody@example.com")
        self.assertEqual("200 OK", unknown[0])
        self.assertIn("Falls zu dieser Adresse".encode(), unknown[2])
        self.sender.assert_not_called()
        self.assertNotIn(b"nobody@example.com", unknown[2])

    def test_unverified_address_cannot_reset_password(self):
        self.app.accounts.create(
            "Bob", hash_password("bob-password-long-enough"), email="bob@example.com"
        )
        self.sender.reset_mock()
        status, _, body = self.request_reset("bob@example.com")
        self.assertEqual("200 OK", status)
        self.assertIn("Falls zu dieser Adresse".encode(), body)
        self.sender.assert_not_called()

    def test_reset_changes_password_revokes_sessions_and_consumes_token(self):
        self.assertEqual("200 OK", self.request_reset()[0])
        recipient, token = self.sender.call_args.args
        self.assertEqual("alice@example.com", recipient)
        self.assertTrue(self.app.accounts.password_reset_valid(token))

        status, _, body = call_app(self.app, f"/password-reset?token={token}")
        self.assertEqual("200 OK", status)
        self.assertIn(b"Neues Passwort", body)

        new_password = "new-password-even-longer"
        status, _, body = call_app(
            self.app,
            "/password-reset",
            method="POST",
            form={
                "token": token,
                "password": new_password,
                "password_confirm": new_password,
            },
        )
        self.assertEqual("200 OK", status)
        self.assertIn("Passwort geändert".encode(), body)
        self.assertFalse(self.app.accounts.password_reset_valid(token))

        # Existing sessions were revoked.
        self.assertEqual(
            "/login",
            call_app(self.app, "/settings", cookie=self.cookie)[1]["Location"],
        )
        self.assertEqual(
            "401 Unauthorized",
            call_app(
                self.app,
                "/login",
                method="POST",
                form={"username": "Alice", "password": self.old_password},
            )[0],
        )
        status, headers, _ = call_app(
            self.app,
            "/login",
            method="POST",
            form={"username": "Alice", "password": new_password},
        )
        self.assertEqual("303 See Other", status)
        self.assertEqual("/my-feeds", headers["Location"])

    def test_invalid_mismatch_and_expired_tokens_are_rejected(self):
        self.assertEqual("200 OK", self.request_reset()[0])
        token = self.sender.call_args.args[1]
        status, _, body = call_app(
            self.app,
            "/password-reset",
            method="POST",
            form={"token": token, "password": "one-long-password", "password_confirm": "different-password"},
        )
        self.assertEqual("400 Bad Request", status)
        self.assertIn("stimmen nicht".encode(), body)
        self.assertTrue(self.app.accounts.password_reset_valid(token))

        with patch("app.account_store.time.time", return_value=int(time.time()) + 31 * 60):
            status, _, body = call_app(self.app, f"/password-reset?token={token}")
        self.assertEqual("400 Bad Request", status)
        self.assertIn(b"Reset-Link", body)


if __name__ == "__main__":
    unittest.main()
