import os
import shutil
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.application import Settings
from app.auth import hash_password
from app.mailer import MailSettings
from app.models import FeedItem
from app.webapp import RegionalRssWebApplication
from test_application import ROOT, call_app


class ReleasePrivacyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "privacy-test-password"
        cls.password_hash = hash_password(cls.password)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        sources = Path(self.temp.name) / "sources"
        shutil.copytree(ROOT / "sources", sources)
        self.app = RegionalRssWebApplication(
            Settings(
                sources_dir=sources,
                cache_path=Path(self.temp.name) / "cache.sqlite3",
                accounts_path=Path(self.temp.name) / "accounts.sqlite3",
                public_base_url="https://rss.example.com",
                user_agent="test",
                timeout_seconds=1,
                max_response_bytes=10000,
                allow_private_hosts=False,
                site_title="RegionalRSS Test",
                admin_username="admin",
                admin_password_hash=self.password_hash,
                session_secret="privacy-test-secret-more-than-32-characters",
                allow_registration=True,
                mail=MailSettings(
                    host="smtp.example.com",
                    from_address="rss@example.com",
                    username="smtp-user",
                    password="smtp-secret",
                ),
            )
        )
        self.app.accounts.create(
            "Alice", self.password_hash, email="alice@example.com"
        )
        self.cookie = self.app.user_sessions.create_cookie(
            "Alice", secure=True
        ).split(";", 1)[0]

    def test_privacy_page_is_public_noindex_and_uses_operator_settings(self):
        values = {
            "REGIONALRSS_PRIVACY_CONTROLLER_NAME": "Example Betreiber",
            "REGIONALRSS_PRIVACY_CONTROLLER_ADDRESS": "Beispielweg 1, 12345 Beispielstadt",
            "REGIONALRSS_PRIVACY_EMAIL": "privacy@example.com",
            "REGIONALRSS_PRIVACY_HOSTING_PROVIDER": "Example Hosting GmbH",
            "REGIONALRSS_PRIVACY_MAIL_PROVIDER": "Example Mail GmbH",
            "REGIONALRSS_PRIVACY_BACKUP_RETENTION": "30 Tage",
        }
        with patch.dict(os.environ, values, clear=False):
            status, headers, body = call_app(self.app, "/datenschutz")
        self.assertEqual("200 OK", status)
        self.assertIn("noindex", headers["X-Robots-Tag"])
        for expected in (
            b"Example Betreiber",
            b"privacy@example.com",
            b"Example Hosting GmbH",
            b"30 Tage",
            b"keine IP-Adresse",
        ):
            self.assertIn(expected, body)
        self.assertNotIn(b"smtp-secret", body)
        self.assertIn("öffentlich abrufbar".encode(), body)

    def test_user_settings_offer_password_change_delete_and_privacy_link(self):
        status, _, body = call_app(self.app, "/settings", cookie=self.cookie)
        self.assertEqual("200 OK", status)
        self.assertIn("Passwort ändern".encode(), body)
        self.assertIn("Konto löschen".encode(), body)
        self.assertIn(b'action="/account/delete"', body)
        self.assertIn(b'href="/datenschutz"', body)

    def test_account_delete_requires_password_username_and_removes_owned_feed(self):
        source = next(iter(self.app.sources.values()))
        owned = replace(source, source_id="alice-test-feed", name="Alice Test Feed")
        self.app.source_store.save(owned)
        self.app.accounts.claim(owned.source_id, "Alice")
        self.app._refresh_sources()
        csrf = self.app.user_sessions.csrf_token("Alice")

        status, _, _ = call_app(
            self.app,
            "/account/delete",
            method="POST",
            cookie=self.cookie,
            form={
                "csrf": csrf,
                "current_password": "wrong",
                "confirm_username": "Alice",
            },
        )
        self.assertEqual("400 Bad Request", status)
        self.assertIsNotNone(self.app.accounts.get("Alice"))

        status, _, _ = call_app(
            self.app,
            "/account/delete",
            method="POST",
            cookie=self.cookie,
            form={
                "csrf": csrf,
                "current_password": self.password,
                "confirm_username": "wrong-name",
            },
        )
        self.assertEqual("400 Bad Request", status)
        self.assertIsNotNone(self.app.accounts.get("Alice"))

        status, headers, _ = call_app(
            self.app,
            "/account/delete",
            method="POST",
            cookie=self.cookie,
            form={
                "csrf": csrf,
                "current_password": self.password,
                "confirm_username": "Alice",
            },
            forwarded_proto="https",
        )
        self.assertEqual("303 See Other", status)
        self.assertEqual("/", headers["Location"])
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertIsNone(self.app.accounts.get("Alice"))
        self.assertNotIn(owned.source_id, self.app.sources)
        self.assertFalse((self.app.settings.sources_dir / "alice-test-feed.yml").exists())

    def test_admin_can_delete_registered_account_and_its_feeds(self):
        source = next(iter(self.app.sources.values()))
        owned = replace(source, source_id="admin-delete-feed", name="Delete Me")
        self.app.source_store.save(owned)
        self.app.accounts.claim(owned.source_id, "Alice")
        self.app._refresh_sources()
        admin_cookie = self.app.sessions.create_cookie(secure=True).split(";", 1)[0]

        status, _, body = call_app(
            self.app, "/admin/accounts", cookie=admin_cookie
        )
        self.assertEqual("200 OK", status)
        self.assertIn(b"Konto und Feeds l", body)
        self.assertIn(b"/admin/accounts/Alice/delete", body)

        status, headers, _ = call_app(
            self.app,
            "/admin/accounts/Alice/delete",
            method="POST",
            cookie=admin_cookie,
            form={"csrf": self.app.sessions.csrf_token()},
        )
        self.assertEqual("303 See Other", status)
        self.assertEqual("/admin/accounts?deleted=1", headers["Location"])
        self.assertIsNone(self.app.accounts.get("Alice"))
        self.assertNotIn(owned.source_id, self.app.sources)

    def test_friendly_expert_form_allows_missing_date_and_preview(self):
        page = self.app._source_form_page(None)
        self.assertIn("Experteneinstellungen".encode().decode(), page)
        self.assertIn("Wo beginnt eine einzelne Meldung?", page)
        self.assertIn("optional – leer lassen", page)
        self.assertNotRegex(page, r'name="date_xpath"[^>]*required')

        item = FeedItem(
            uid="1",
            title="Meldung ohne Datum",
            uri="https://example.com/item",
            published=None,
            summary=None,
            image_url=None,
        )
        preview = self.app._source_form_page(None, preview=[item])
        self.assertIn("kein Datum auf der Seite", preview)

    def test_shipped_access_log_formats_do_not_contain_client_ip(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        nginx = (ROOT / "nginx" / "regionalrss.conf").read_text(encoding="utf-8")
        self.assertNotIn("%(h)s", dockerfile)
        log_line = next(line for line in nginx.splitlines() if line.startswith("log_format regionalrss_privacy"))
        self.assertNotIn("$remote_addr", log_line)
        self.assertNotIn("$http_referer", log_line)
        self.assertNotIn("$args", log_line)


if __name__ == "__main__":
    unittest.main()
