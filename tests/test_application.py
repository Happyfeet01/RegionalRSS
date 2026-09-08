from io import BytesIO
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlencode

from app.application import RegionalRssApplication, Settings
from app.auth import hash_password
from app.config import load_source, parse_source
from app.extractor import extract_items
from app.service import ServiceResult


ROOT = Path(__file__).resolve().parents[1]


class FakeService:
    def __init__(self, result: ServiceResult) -> None:
        self.result = result

    def get_items(self, source):  # noqa: ANN001
        return self.result


def call_app(
    app,
    path: str,
    *,
    method: str = "GET",
    etag: str | None = None,
    form: dict[str, str] | None = None,
    cookie: str | None = None,
    forwarded_proto: str | None = None,
):  # noqa: ANN001
    captured = {}

    def start_response(status, headers):  # noqa: ANN001
        captured["status"] = status
        captured["headers"] = dict(headers)

    payload = urlencode(form or {}).encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(payload)),
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "wsgi.input": BytesIO(payload),
    }
    if etag:
        environ["HTTP_IF_NONE_MATCH"] = etag
    if cookie:
        environ["HTTP_COOKIE"] = cookie
    if forwarded_proto:
        environ["HTTP_X_FORWARDED_PROTO"] = forwarded_proto
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


class ApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        settings = Settings(
            sources_dir=ROOT / "sources",
            cache_path=Path(self.temp.name) / "cache.sqlite3",
            public_base_url="https://feeds.example.de",
            user_agent="RegionalRSS test",
            timeout_seconds=2,
            max_response_bytes=1024 * 1024,
            allow_private_hosts=False,
            site_title="RegionalRSS Test",
        )
        self.app = RegionalRssApplication(settings)
        source = load_source(ROOT / "sources" / "flieden-aktuelles.yml")
        fixture = (ROOT / "tests" / "fixtures" / "flieden.html").read_text(
            encoding="utf-8"
        )
        items = extract_items(fixture, source)
        self.app.service = FakeService(
            ServiceResult(items=items, fetched_at=1788782400, stale=False)
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_index_lists_configured_source(self) -> None:
        status, headers, body = call_app(self.app, "/")
        self.assertEqual("200 OK", status)
        self.assertEqual("text/html; charset=utf-8", headers["Content-Type"])
        self.assertIn(b"flieden-aktuelles.xml", body)
        self.assertIn(b'noindex,nofollow', body)
        self.assertEqual("noindex, nofollow", headers["X-Robots-Tag"])

    def test_feed_endpoint_and_conditional_request(self) -> None:
        status, headers, body = call_app(
            self.app, "/feeds/flieden-aktuelles.xml"
        )
        self.assertEqual("200 OK", status)
        self.assertEqual("application/rss+xml; charset=utf-8", headers["Content-Type"])
        self.assertIn(b"media:content", body)

        status_304, _, body_304 = call_app(
            self.app,
            "/feeds/flieden-aktuelles.xml",
            etag=headers["ETag"],
        )
        self.assertEqual("304 Not Modified", status_304)
        self.assertEqual(b"", body_304)

    def test_missing_packaged_sources_are_seeded_without_overwriting(self) -> None:
        with TemporaryDirectory() as temporary:
            source_dir = Path(temporary) / "mounted-sources"
            settings = Settings(
                sources_dir=source_dir,
                cache_path=Path(temporary) / "cache.sqlite3",
                public_base_url=None,
                user_agent="RegionalRSS test",
                timeout_seconds=2,
                max_response_bytes=1024 * 1024,
                allow_private_hosts=False,
                site_title="RegionalRSS Test",
                default_sources_dir=ROOT / "sources",
            )
            RegionalRssApplication(settings)
            self.assertTrue((source_dir / "flieden-aktuelles.yml").is_file())
            self.assertTrue((source_dir / "neuhof-pressemitteilungen.yml").is_file())


class AdminApplicationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.password = "a-secure-test-password"
        cls.password_hash = hash_password(cls.password)

    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        source_dir = Path(self.temp.name) / "sources"
        shutil.copytree(ROOT / "sources", source_dir)
        settings = Settings(
            sources_dir=source_dir,
            cache_path=Path(self.temp.name) / "cache.sqlite3",
            public_base_url="https://feeds.example.de",
            user_agent="RegionalRSS test",
            timeout_seconds=2,
            max_response_bytes=1024 * 1024,
            allow_private_hosts=False,
            site_title="RegionalRSS Test",
            admin_username="lars",
            admin_password_hash=self.password_hash,
            session_secret="test-session-secret-with-more-than-32-characters",
        )
        self.app = RegionalRssApplication(settings)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def login(self) -> str:
        status, headers, _ = call_app(
            self.app,
            "/admin/login",
            method="POST",
            form={"username": "lars", "password": self.password},
            forwarded_proto="https",
        )
        self.assertEqual("303 See Other", status)
        self.assertIn("Secure", headers["Set-Cookie"])
        return headers["Set-Cookie"].split(";", 1)[0]

    def test_admin_requires_login_and_rejects_wrong_password(self) -> None:
        status, headers, _ = call_app(self.app, "/admin")
        self.assertEqual("303 See Other", status)
        self.assertEqual("/admin/login", headers["Location"])

        status, _, body = call_app(
            self.app,
            "/admin/login",
            method="POST",
            form={"username": "lars", "password": "wrong-password"},
        )
        self.assertEqual("401 Unauthorized", status)
        self.assertIn("Passwort ist falsch".encode(), body)

    def test_admin_can_add_source_and_public_index_reloads_it(self) -> None:
        cookie = self.login()
        csrf = self.app.sessions.csrf_token()
        form = {
            "csrf": csrf,
            "previous_id": "",
            "action": "save",
            "id": "example-news",
            "name": "Example News",
            "description": "Testmeldungen",
            "site_url": "https://example.com/",
            "list_url": "https://example.com/news/",
            "language": "de-DE",
            "timezone": "Europe/Berlin",
            "cache_seconds": "1800",
            "max_items": "20",
            "min_items": "1",
            "item_xpath": "//article",
            "title_xpath": ".//h2[1]",
            "title_attribute": "",
            "link_xpath": ".//a[1]",
            "link_attribute": "href",
            "date_xpath": ".//time[1]",
            "date_attribute": "datetime",
            "summary_xpath": "",
            "summary_attribute": "",
            "image_xpath": ".//img[1]",
            "image_attribute": "src",
            "date_formats": "iso8601",
            "categories": "Test, Regional",
        }
        status, headers, _ = call_app(
            self.app,
            "/admin/sources/save",
            method="POST",
            form=form,
            cookie=cookie,
        )
        self.assertEqual("303 See Other", status)
        self.assertEqual("/admin?changed=saved", headers["Location"])
        self.assertTrue((self.app.settings.sources_dir / "example-news.yml").is_file())

        status, _, body = call_app(self.app, "/")
        self.assertEqual("200 OK", status)
        self.assertIn(b"example-news.xml", body)

    def test_source_write_requires_csrf_token(self) -> None:
        cookie = self.login()
        status, _, _ = call_app(
            self.app,
            "/admin/sources/save",
            method="POST",
            form={"csrf": "wrong"},
            cookie=cookie,
        )
        self.assertEqual("403 Forbidden", status)

    def test_registered_user_can_create_automatically_detected_feed(self) -> None:
        self.app.settings = Settings(
            **{
                **self.app.settings.__dict__,
                "allow_registration": True,
            }
        )
        status, _, body = call_app(self.app, "/login")
        self.assertEqual("200 OK", status)
        self.assertIn(b"Anmelden", body)
        status, _, body = call_app(self.app, "/register")
        self.assertEqual("200 OK", status)
        self.assertIn(b"Konto erstellen", body)
        status, headers, _ = call_app(
            self.app,
            "/register",
            method="POST",
            form={
                "username": "tester",
                "password": "a-secure-user-password",
                "password_confirm": "a-secure-user-password",
            },
            forwarded_proto="https",
        )
        self.assertEqual("303 See Other", status)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, body = call_app(self.app, "/my-feeds", cookie=cookie)
        self.assertEqual("200 OK", status)
        self.assertIn(b"Meine Feeds", body)
        source = parse_source(
            {
                "id": "auto-example",
                "name": "Automatisch",
                "description": "Automatisch erkannt",
                "site_url": "https://example.com/",
                "list_url": "https://example.com/news/",
                "source_type": "native",
                "native_feed_url": "https://example.com/feed.xml",
            }
        )
        self.app._discover_source = lambda _url: (source, [])
        csrf = self.app.user_sessions.csrf_token("tester")
        status, headers, _ = call_app(
            self.app,
            "/my-feeds/create",
            method="POST",
            form={"csrf": csrf, "url": "https://example.com/news/", "name": ""},
            cookie=cookie,
        )
        self.assertEqual("303 See Other", status)
        self.assertEqual("tester", self.app.accounts.owner("auto-example"))
        self.assertTrue((self.app.settings.sources_dir / "auto-example.yml").is_file())

        status, headers, _ = call_app(self.app, "/feeds/auto-example.xml")
        self.assertEqual("307 Temporary Redirect", status)
        self.assertEqual("https://example.com/feed.xml", headers["Location"])


if __name__ == "__main__":
    unittest.main()
