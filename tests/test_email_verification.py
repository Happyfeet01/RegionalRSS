from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import hashlib
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from app.account_store import AccountStore, VERIFICATION_SECONDS, VerificationThrottled
from app.application import RegionalRssApplication, Settings
from app.auth import hash_password
from app.mailer import MailDeliveryError, MailSettings
from test_application import call_app, ROOT


class AccountVerificationTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "accounts.sqlite3"
        self.accounts = AccountStore(self.path)

    def create(self, name="tester", email="tester@example.com"):
        self.assertTrue(self.accounts.create(name, "dummy-test-hash", email=email))

    def test_email_required_normalized_and_unique(self):
        with self.assertRaises(ValueError):
            self.accounts.create("empty", "hash", email="")
        self.create(email="Tester@Example.COM")
        self.assertEqual("tester@example.com", self.accounts.get("TESTER").email)
        self.assertFalse(self.accounts.create("another", "hash", email="tester@example.com"))
        self.assertFalse(self.accounts.get("tester").active)

    def test_single_use_hash_only_and_no_activation_on_lookup(self):
        self.create()
        token = self.accounts.issue_verification("tester")
        with sqlite3.connect(self.path) as db:
            stored = db.execute("SELECT token_hash FROM email_verifications").fetchone()[0]
        self.assertNotEqual(token, stored)
        self.assertEqual(hashlib.sha256(token.encode()).hexdigest(), stored)
        self.assertTrue(self.accounts.verification_valid(token))
        self.assertFalse(self.accounts.get("tester").active)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(self.accounts.confirm_email, [token, token]))
        self.assertEqual([False, True], sorted(outcomes))
        self.assertTrue(self.accounts.get("tester").active)
        self.assertFalse(self.accounts.verification_valid(token))

    def test_expiry_resend_and_all_tokens_consumed_together(self):
        self.create()
        with patch("app.account_store.time.time", return_value=1000):
            old = self.accounts.issue_verification("tester")
            with self.assertRaises(VerificationThrottled):
                self.accounts.issue_verification("tester")
        with patch("app.account_store.time.time", return_value=1060):
            new = self.accounts.issue_verification("tester")
        with patch("app.account_store.time.time", return_value=1000 + VERIFICATION_SECONDS):
            self.assertFalse(self.accounts.confirm_email(old))
            self.assertTrue(self.accounts.confirm_email(new))
        self.assertFalse(self.accounts.confirm_email(new))

    def test_confirming_any_valid_link_invalidates_all_other_links(self):
        self.create()
        with patch("app.account_store.time.time", return_value=1000):
            first = self.accounts.issue_verification("tester")
        with patch("app.account_store.time.time", return_value=1060):
            second = self.accounts.issue_verification("tester")
            self.assertTrue(self.accounts.confirm_email(first))
            self.assertFalse(self.accounts.confirm_email(second))

    def test_rate_limits_persist_across_workers(self):
        with patch("app.account_store.time.time", return_value=1000):
            self.assertTrue(self.accounts.take_rate_limit("key", limit=1, seconds=60))
            second_worker = AccountStore(self.path)
            self.assertFalse(second_worker.take_rate_limit("key", limit=1, seconds=60))
        with patch("app.account_store.time.time", return_value=1060):
            self.assertTrue(self.accounts.take_rate_limit("key", limit=1, seconds=60))

    def test_migration_preserves_legacy_accounts_and_pending_state(self):
        legacy = Path(self.temp.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.executescript('''
                CREATE TABLE accounts(username TEXT PRIMARY KEY COLLATE NOCASE,
                    password_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
                INSERT INTO accounts VALUES ('Lars', 'existing-hash', 123);
                CREATE TABLE source_owners(source_id TEXT PRIMARY KEY, username TEXT NOT NULL,
                    created_at INTEGER NOT NULL);
                INSERT INTO source_owners VALUES ('existing-feed', 'Lars', 456);
            ''')
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(AccountStore, [legacy, legacy]))
        migrated = AccountStore(legacy)
        self.assertTrue(migrated.get("Lars").active)
        self.assertIsNone(migrated.get("Lars").email_verified_at)
        self.assertEqual("existing-hash", migrated.password_hash("Lars"))
        self.assertEqual("Lars", migrated.owner("existing-feed"))
        migrated.create("new-user", "new-hash", email="new@example.com")
        migrated = AccountStore(legacy)
        self.assertFalse(migrated.get("new-user").active)


class EmailRegistrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "a-secure-user-password"
        cls.password_hash = hash_password(cls.password)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = RegionalRssApplication(Settings(
            sources_dir=ROOT / "sources", cache_path=Path(self.temp.name) / "cache.sqlite3",
            public_base_url="https://feeds.example.com", user_agent="test", timeout_seconds=1,
            max_response_bytes=10000, allow_private_hosts=False, site_title="test",
            admin_username="admin", admin_password_hash=self.password_hash,
            session_secret="test-session-secret-with-more-than-32-characters",
            allow_registration=True,
            mail=MailSettings(host="smtp.example.com", from_address="rss@example.com"),
        ))
        patcher = patch.object(self.app.mailer, "send_verification")
        self.sender = patcher.start()
        self.addCleanup(patcher.stop)

    def register(self, **overrides):
        form = {"username": "Tester", "email": "tester@example.com", "password": self.password,
                "password_confirm": self.password}
        form.update(overrides)
        return call_app(self.app, "/register", method="POST", form=form, forwarded_proto="https")

    def test_entire_flow_no_feed_access_until_confirmed_and_no_email_in_directory(self):
        for path in ("/login", "/register"):
            self.assertEqual("200 OK", call_app(self.app, path)[0])
        status, headers, _ = self.register()
        self.assertEqual("303 See Other", status)
        self.assertEqual("/verify-email?sent=1", headers["Location"])
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        csrf = self.app.user_sessions.csrf_token("Tester")
        token = self.sender.call_args.args[1]
        self.app._discover_source = Mock(side_effect=AssertionError("Must not fetch for pending users"))
        for path, method in (("/my-feeds", "GET"), ("/my-feeds/new", "GET"),
                             ("/my-feeds/create", "POST"), ("/my-feeds/existing-feed/delete", "POST")):
            status, headers, _ = call_app(self.app, path, cookie=cookie, method=method, form={"csrf": csrf})
            self.assertEqual("303 See Other", status)
            self.assertEqual("/verify-email", headers["Location"])
        self.app._discover_source.assert_not_called()
        self.assertEqual("200 OK", call_app(self.app, "/verify-email", cookie=cookie)[0])
        link = f"/verify-email?token={token}"
        status, headers, body = call_app(self.app, link)
        self.assertEqual("200 OK", status)
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual("no-referrer", headers["Referrer-Policy"])
        self.assertIn("noindex", headers["X-Robots-Tag"])
        self.assertIn(token.encode(), body)
        self.assertFalse(self.app.accounts.get("Tester").active)
        status, headers, _ = call_app(self.app, "/verify-email", method="POST", form={"token": token})
        self.assertEqual("303 See Other", status)
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual("200 OK", call_app(self.app, headers["Location"])[0])
        self.assertEqual("200 OK", call_app(self.app, "/my-feeds", cookie=cookie)[0])
        self.assertEqual("200 OK", call_app(self.app, "/my-feeds/new", cookie=cookie)[0])
        self.assertNotIn(b"tester@example.com", call_app(self.app, "/")[2])
        self.assertEqual("400 Bad Request", call_app(self.app, link)[0])
        self.assertEqual("400 Bad Request", call_app(self.app, "/verify-email", method="POST", form={"token": token})[0])

    def test_resend_requires_auth_csrf_and_cooldown_and_expired_links_can_recover(self):
        with patch("app.account_store.time.time", return_value=1000):
            _, headers, _ = self.register()
        # Sessions share the time module: use a current cookie to check resend.
        cookie = self.app.user_sessions.create_cookie("Tester", secure=True).split(";", 1)[0]
        old = self.sender.call_args.args[1]
        path = "/verify-email/resend"
        self.assertEqual("405 Method Not Allowed", call_app(self.app, path)[0])
        self.assertEqual("303 See Other", call_app(self.app, path, method="POST")[0])
        self.assertEqual("403 Forbidden", call_app(self.app, path, method="POST", cookie=cookie)[0])
        form = {"csrf": self.app.user_sessions.csrf_token("Tester")}
        with patch("app.account_store.time.time", return_value=1001):
            self.assertEqual("429 Too Many Requests", call_app(self.app, path, method="POST", cookie=cookie, form=form)[0])
        with patch("app.account_store.time.time", return_value=1000 + VERIFICATION_SECONDS):
            self.assertEqual("400 Bad Request", call_app(self.app, f"/verify-email?token={old}")[0])
            status, headers, _ = call_app(self.app, "/login", method="POST",
                form={"username": "tester", "password": self.password})
            self.assertEqual("/verify-email", headers["Location"])
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            self.assertEqual("200 OK", call_app(self.app, path, method="POST", cookie=cookie, form=form)[0])
            new = self.sender.call_args.args[1]
            self.assertNotEqual(old, new)
            self.assertEqual("303 See Other", call_app(self.app, "/verify-email", method="POST", form={"token": new})[0])

    def test_smtp_failure_keeps_account_pending_and_allows_retry(self):
        self.sender.side_effect = MailDeliveryError("test failure")
        with patch("app.account_store.time.time", return_value=1000):
            status, headers, body = self.register()
        self.assertEqual("503 Service Unavailable", status)
        self.assertIn(b"vorgemerkt", body)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        bad_token = self.sender.call_args.args[1]
        self.assertFalse(self.app.accounts.get("Tester").active)
        self.assertFalse(self.app.accounts.verification_valid(bad_token))
        self.sender.side_effect = None
        with patch("app.account_store.time.time", return_value=1060):
            status, _, _ = call_app(self.app, "/verify-email/resend", method="POST", cookie=cookie,
                form={"csrf": self.app.user_sessions.csrf_token("Tester")})
        self.assertEqual("200 OK", status)

    def test_bad_email_password_duplicate_and_wrong_login_render_forms(self):
        for email in ("", "not-an-email", "x@example.com\r\nBcc: attacker@example.com"):
            status, _, body = self.register(email=email)
            self.assertEqual("400 Bad Request", status)
            self.assertIn(b'name="email"', body)
        self.assertEqual("400 Bad Request", self.register(password_confirm="different")[0])
        self.sender.assert_not_called()
        self.register()
        self.assertEqual("400 Bad Request", self.register(username="Different", email="TESTER@EXAMPLE.COM")[0])
        self.assertEqual(1, self.sender.call_count)
        status, _, _ = call_app(self.app, "/login", method="POST", form={"username": "Tester", "password": "wrong"})
        self.assertEqual("401 Unauthorized", status)

    def test_missing_smtp_blocks_new_registration_but_keeps_site_and_admin_working(self):
        self.app.mailer.settings = MailSettings()
        self.assertEqual("503 Service Unavailable", call_app(self.app, "/register")[0])
        self.assertEqual("503 Service Unavailable", self.register()[0])
        self.assertIsNone(self.app.accounts.get("Tester"))
        self.assertEqual("200 OK", call_app(self.app, "/")[0])
        self.assertEqual("200 OK", call_app(self.app, "/admin/login")[0])
        self.sender.assert_not_called()

    def test_disabled_registration_and_abuse_throttle(self):
        self.app.settings = replace(self.app.settings, allow_registration=False)
        self.assertEqual("403 Forbidden", self.register()[0])
        self.app.settings = replace(self.app.settings, allow_registration=True)
        for _ in range(10):
            self.assertEqual("400 Bad Request", self.register(email="invalid")[0])
        self.assertEqual("429 Too Many Requests", self.register()[0])
        self.sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
