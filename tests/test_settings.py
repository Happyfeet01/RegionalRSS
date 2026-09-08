import base64
import hashlib
import sqlite3
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.account_store import AccountStore
from app.application import RegionalRssApplication, Settings
from app.auth import hash_password, verify_password
from app.mailer import MailDeliveryError, MailSettings
from test_application import ROOT, call_app


class SettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "my-current-test-password"
        cls.password_hash = hash_password(cls.password)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = RegionalRssApplication(Settings(
            sources_dir=ROOT / "sources", cache_path=Path(self.temp.name) / "cache.sqlite3",
            public_base_url="https://rss.example.com", user_agent="test", timeout_seconds=1,
            max_response_bytes=10000, allow_private_hosts=False, site_title="RegionalRSS",
            admin_username="admin", admin_password_hash=self.password_hash,
            session_secret="settings-test-secret-more-than-32-characters",
            allow_registration=True,
            mail=MailSettings(host="smtp.example.com", from_address="rss@example.com",
                              username="smtp-user", password="private-smtp-password"),
        ))
        self.app.accounts.create("Alice", self.password_hash, email="alice@example.com")
        with patch("app.account_store.time.time", return_value=1000):
            self.app.accounts.confirm_email(self.app.accounts.issue_verification("Alice"))
        self.app.accounts.create("Bob", self.password_hash, email="bob@example.com")
        self.cookie = self.app.user_sessions.create_cookie("Alice", secure=True).split(";", 1)[0]
        patcher = patch.object(self.app.mailer, "send_verification")
        self.sender = patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, action="email", **overrides):
        form = {"csrf": self.app.user_sessions.csrf_token("Alice"), "action": action,
                "current_password": self.password, "email": "new@example.com"}
        form.update(overrides)
        return call_app(self.app, "/settings", method="POST", cookie=self.cookie, form=form,
                        forwarded_proto="https")

    def test_navigation_and_own_profile_are_private(self):
        self.assertEqual("/login", call_app(self.app, "/settings")[1]["Location"])
        for path in ("/my-feeds", "/my-feeds/new", "/settings?username=Bob"):
            status, headers, body = call_app(self.app, path, cookie=self.cookie)
            self.assertEqual("200 OK", status)
            self.assertIn(b'href="/settings"', body)
            self.assertIn(b'href="/my-feeds"', body)
            self.assertEqual("no-store", headers["Cache-Control"])
            self.assertIn("noindex", headers["X-Robots-Tag"])
            self.assertNotIn(b"bob@example.com", body)
        profile = call_app(self.app, "/settings", cookie=self.cookie)[2]
        self.assertIn(b"alice@example.com", profile)
        self.assertIn("Bestätigt".encode(), profile)
        self.assertNotIn(b"private-smtp-password", profile)
        self.assertNotIn(self.password_hash.encode(), profile)
        public = call_app(self.app, "/", cookie=self.cookie)[2]
        self.assertIn(b'href="/settings"', public)
        self.assertNotIn(b"alice@example.com", public)
        self.assertNotIn(b"bob@example.com", public)

    def test_admin_pages_require_admin_session_and_hide_credentials(self):
        admin_cookie = self.app.sessions.create_cookie(secure=True).split(";", 1)[0]
        self.app.accounts.claim("flieden-aktuelles", "Alice")
        for path in ("/admin/accounts", "/admin/settings"):
            for cookie in (None, self.cookie):
                status, headers, body = call_app(self.app, path, cookie=cookie)
                self.assertEqual("/admin/login", headers["Location"])
                self.assertNotIn(b"bob@example.com", body)
            status, headers, body = call_app(self.app, path, cookie=admin_cookie)
            self.assertEqual("200 OK", status)
            self.assertEqual("no-store", headers["Cache-Control"])
            self.assertIn("noindex", headers["X-Robots-Tag"])
            self.assertIn(b'href="/admin/accounts"', body)
            self.assertIn(b'href="/admin/settings"', body)
            for secret in (self.password, self.password_hash, "private-smtp-password", self.app.settings.session_secret):
                self.assertNotIn(secret.encode(), body)
            self.assertEqual("405 Method Not Allowed", call_app(self.app, path, method="POST", cookie=admin_cookie)[0])
        directory = call_app(self.app, "/admin/accounts", cookie=admin_cookie)[2]
        self.assertIn(b"alice@example.com", directory)
        self.assertIn(b"bob@example.com", directory)
        self.assertIn("Bestätigung ausstehend".encode(), directory)
        self.assertIn(b"Administrator: admin", directory)
        config = call_app(self.app, "/admin/settings", cookie=admin_cookie)[2]
        self.assertIn(b"rss@example.com", config)
        self.assertIn(b"smtp.example.com", config)

    def legacy_cookie(self, username):
        payload = f"{username}|{int(time.time()) + 3600}"
        encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        return f"regionalrss_user_session={encoded}.{self.app.user_sessions._sign(payload)}"

    def test_same_username_and_legacy_user_cookie_cannot_impersonate_admin(self):
        for user_cookie in (self.legacy_cookie("admin"), self.app.user_sessions.create_cookie("admin", secure=True)):
            forged = user_cookie.replace("regionalrss_user_session=", "regionalrss_session=")
            self.assertFalse(self.app.sessions.authenticated(forged))
            self.assertEqual("/admin/login", call_app(self.app, "/admin/accounts", cookie=forged)[1]["Location"])

    def test_settings_require_csrf_current_password_and_valid_values(self):
        self.assertEqual("403 Forbidden", self.post(csrf="wrong")[0])
        self.assertEqual("400 Bad Request", self.post(current_password="wrong")[0])
        self.assertEqual("400 Bad Request", self.post(email="bob@example.com")[0])
        self.assertEqual("400 Bad Request", self.post(email="x@example.com\r\nBcc: a@example.com")[0])
        self.assertEqual("400 Bad Request", self.post("password", new_password="too-short", password_confirm="too-short")[0])
        self.assertEqual("400 Bad Request", self.post("password", new_password="different", password_confirm="other")[0])
        self.assertIsNone(self.app.accounts.get("Alice").pending_email)
        self.assertEqual(self.password_hash, self.app.accounts.password_hash("Alice"))
        self.sender.assert_not_called()

    def test_password_change_revokes_old_sessions_and_preserves_current_login(self):
        legacy = self.legacy_cookie("Alice")
        self.assertEqual("200 OK", call_app(self.app, "/settings", cookie=legacy)[0])
        password = "my-new-long-password"
        status, headers, _ = self.post("password", new_password=password, password_confirm=password)
        self.assertEqual("200 OK", status)
        new_cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertIn("Secure", headers["Set-Cookie"])
        self.assertEqual("200 OK", call_app(self.app, "/settings", cookie=new_cookie)[0])
        for old in (self.cookie, legacy):
            self.assertEqual("/login", call_app(self.app, "/settings", cookie=old)[1]["Location"])
            self.assertEqual("/login", call_app(self.app, "/my-feeds", cookie=old)[1]["Location"])
        self.assertTrue(verify_password(password, self.app.accounts.password_hash("Alice")))
        self.assertEqual("401 Unauthorized", call_app(self.app, "/login", method="POST",
                         form={"username": "Alice", "password": self.password})[0])
        status, headers, _ = call_app(self.app, "/login", method="POST", form={"username": "alice", "password": password})
        self.assertEqual("/my-feeds", headers["Location"])
        self.assertEqual("200 OK", call_app(self.app, "/settings", cookie=headers["Set-Cookie"])[0])

    def test_email_change_keeps_verified_address_until_confirmation(self):
        self.assertEqual("200 OK", self.post()[0])
        account = self.app.accounts.get("Alice")
        self.assertEqual("alice@example.com", account.email)
        self.assertEqual("new@example.com", account.pending_email)
        self.assertTrue(account.active)
        self.assertEqual("200 OK", call_app(self.app, "/my-feeds", cookie=self.cookie)[0])
        recipient, token = self.sender.call_args.args
        self.assertEqual("new@example.com", recipient)
        self.assertFalse(self.app.accounts.create("Other", self.password_hash, email="NEW@example.com"))
        self.assertEqual("200 OK", call_app(self.app, f"/verify-email?token={token}")[0])
        self.assertEqual("alice@example.com", self.app.accounts.get("Alice").email)
        status, headers, _ = call_app(self.app, "/verify-email", method="POST", form={"token": token})
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual("303 See Other", status)
        self.assertEqual("new@example.com", self.app.accounts.get("Alice").email)
        self.assertIsNone(self.app.accounts.get("Alice").pending_email)
        self.assertFalse(self.app.accounts.confirm_email(token))

    def test_email_delivery_failure_and_resend_keep_original_account_active(self):
        self.sender.side_effect = MailDeliveryError("SMTP unavailable")
        now = int(time.time())
        with patch("app.account_store.time.time", return_value=now):
            self.assertEqual("503 Service Unavailable", self.post()[0])
        token = self.sender.call_args.args[1]
        self.assertFalse(self.app.accounts.verification_valid(token))
        self.assertEqual("alice@example.com", self.app.accounts.get("Alice").email)
        self.assertTrue(self.app.accounts.get("Alice").active)
        self.sender.side_effect = None
        with patch("app.account_store.time.time", return_value=now + 60):
            status, _, body = call_app(self.app, "/verify-email/resend", method="POST", cookie=self.cookie,
                form={"csrf": self.app.user_sessions.csrf_token("Alice")})
        self.assertEqual("200 OK", status)
        self.assertIn(b"Einstellungen", body)
        self.assertTrue(self.app.accounts.confirm_email(self.sender.call_args.args[1]))

    def test_pending_user_can_correct_address_without_bypassing_confirmation(self):
        old = self.app.accounts.issue_verification("Bob")
        self.cookie = self.app.user_sessions.create_cookie("Bob", secure=True)
        status, _, _ = self.post(csrf=self.app.user_sessions.csrf_token("Bob"), email="correct@example.com")
        self.assertEqual("429 Too Many Requests", status)
        self.assertFalse(self.app.accounts.confirm_email(old))
        self.assertEqual("200 OK", call_app(self.app, "/settings", cookie=self.cookie)[0])
        self.assertEqual("/verify-email", call_app(self.app, "/my-feeds", cookie=self.cookie)[1]["Location"])
        with patch("app.account_store.time.time", return_value=int(time.time()) + 60):
            self.assertEqual("200 OK", call_app(self.app, "/verify-email/resend", method="POST", cookie=self.cookie,
                form={"csrf": self.app.user_sessions.csrf_token("Bob")})[0])
            self.assertTrue(self.app.accounts.confirm_email(self.sender.call_args.args[1]))
        self.assertEqual("correct@example.com", self.app.accounts.get("Bob").email)

    def test_no_smtp_does_not_stage_email_change(self):
        self.app.mailer.settings = MailSettings()
        self.assertEqual("503 Service Unavailable", self.post()[0])
        self.assertIsNone(self.app.accounts.get("Alice").pending_email)
        self.sender.assert_not_called()

    def test_only_latest_target_can_be_confirmed_and_duplicates_are_serialized(self):
        self.app.accounts.stage_email("Alice", "first@example.com")
        first = self.app.accounts.issue_verification("Alice")
        self.app.accounts.stage_email("Alice", "second@example.com")
        self.assertFalse(self.app.accounts.confirm_email(first))
        def stage(username):
            try:
                self.app.accounts.stage_email(username, "shared@example.com")
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual([False, True], sorted(pool.map(stage, ["Alice", "Bob"])))

    def test_migration_keeps_040_links_and_legacy_accounts_can_add_email(self):
        path = Path(self.temp.name) / "version040.sqlite3"
        token = "a" * 43
        with sqlite3.connect(path) as db:
            db.executescript('''
                CREATE TABLE accounts(username TEXT PRIMARY KEY COLLATE NOCASE, password_hash TEXT,
                    created_at INTEGER, email TEXT COLLATE NOCASE, email_verified_at INTEGER,
                    verification_required INTEGER NOT NULL DEFAULT 0, verification_sent_at INTEGER);
                INSERT INTO accounts VALUES ('Legacy', 'hash', 123, NULL, NULL, 0, NULL);
                INSERT INTO accounts VALUES ('Pending', 'hash', 123, 'pending@example.com', NULL, 1, 1000);
                CREATE TABLE email_verifications(token_hash TEXT PRIMARY KEY, username TEXT, expires_at INTEGER);
            ''')
            db.execute("INSERT INTO email_verifications VALUES (?, 'Pending', ?)",
                       (hashlib.sha256(token.encode()).hexdigest(), int(time.time()) + 300))
        store = AccountStore(path)
        self.assertTrue(store.verification_valid(token))
        self.assertTrue(store.confirm_email(token))
        self.assertEqual(0, store.get("Legacy").session_version)
        self.assertTrue(store.get("Legacy").active)
        store.stage_email("Legacy", "legacy@example.com")
        self.assertTrue(store.get("Legacy").active)
        self.assertTrue(store.confirm_email(store.issue_verification("Legacy")))
        self.assertEqual("legacy@example.com", store.get("Legacy").email)


if __name__ == "__main__":
    unittest.main()
