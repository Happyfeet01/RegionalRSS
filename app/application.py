from __future__ import annotations

import hashlib
import html
import hmac
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qs

from .auth import SessionManager, verify_password
from .cache import FeedCache
from .config import parse_source
from .extractor import extract_items
from .feed import build_rss
from .fetcher import FetchError, fetch_html
from .models import (
    ConfigurationError,
    ExtractionError,
    SourceConfig,
    SourceUnavailable,
)
from .service import SourceService
from .source_store import SourceStore


LOGGER = logging.getLogger(__name__)
FEED_PATH_RE = re.compile(r"^/feeds/([a-z0-9][a-z0-9-]{1,62}[a-z0-9])\.xml$")


@dataclass(frozen=True)
class Settings:
    sources_dir: Path
    cache_path: Path
    public_base_url: str | None
    user_agent: str
    timeout_seconds: int
    max_response_bytes: int
    allow_private_hosts: bool
    site_title: str
    admin_username: str | None = None
    admin_password_hash: str | None = None
    session_secret: str | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        project_root = Path(__file__).resolve().parent.parent
        public_base_url = os.getenv("REGIONALRSS_PUBLIC_BASE_URL", "").strip().rstrip("/")
        return cls(
            sources_dir=Path(
                os.getenv("REGIONALRSS_SOURCES_DIR", str(project_root / "sources"))
            ),
            cache_path=Path(
                os.getenv(
                    "REGIONALRSS_CACHE_PATH", str(project_root / "data" / "cache.sqlite3")
                )
            ),
            public_base_url=public_base_url or None,
            user_agent=os.getenv(
                "REGIONALRSS_USER_AGENT",
                "RegionalRSS/0.2 (configured public feed generator)",
            ),
            timeout_seconds=int(os.getenv("REGIONALRSS_TIMEOUT_SECONDS", "12")),
            max_response_bytes=int(
                os.getenv("REGIONALRSS_MAX_RESPONSE_BYTES", str(5 * 1024 * 1024))
            ),
            allow_private_hosts=os.getenv(
                "REGIONALRSS_ALLOW_PRIVATE_HOSTS", "false"
            ).lower()
            in {"1", "true", "yes"},
            site_title=os.getenv("REGIONALRSS_SITE_TITLE", "RegionalRSS"),
            admin_username=os.getenv("REGIONALRSS_ADMIN_USER", "").strip() or None,
            admin_password_hash=os.getenv(
                "REGIONALRSS_ADMIN_PASSWORD_HASH", ""
            ).strip()
            or None,
            session_secret=os.getenv("REGIONALRSS_SESSION_SECRET", "").strip() or None,
        )


StartResponse = Callable[[str, list[tuple[str, str]]], Callable]


class RegionalRssApplication:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.source_store = SourceStore(settings.sources_dir)
        self.sources = self.source_store.all()
        self.cache = FeedCache(settings.cache_path)
        self.service = SourceService(
            self.cache,
            user_agent=settings.user_agent,
            timeout_seconds=settings.timeout_seconds,
            max_response_bytes=settings.max_response_bytes,
            allow_private_hosts=settings.allow_private_hosts,
        )
        auth_values = (
            settings.admin_username,
            settings.admin_password_hash,
            settings.session_secret,
        )
        if any(auth_values) and not all(auth_values):
            raise ConfigurationError(
                "Admin access requires REGIONALRSS_ADMIN_USER, "
                "REGIONALRSS_ADMIN_PASSWORD_HASH and REGIONALRSS_SESSION_SECRET"
            )
        if settings.session_secret and len(settings.session_secret) < 32:
            raise ConfigurationError("REGIONALRSS_SESSION_SECRET is too short")
        self.sessions = (
            SessionManager(settings.admin_username, settings.session_secret)
            if settings.admin_username and settings.session_secret
            else None
        )

    def __call__(self, environ: dict, start_response: StartResponse) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        if path == "/admin" or path.startswith("/admin/"):
            try:
                return self._handle_admin(environ, start_response, method, path)
            except (ValueError, UnicodeDecodeError, AttributeError):
                return self._respond(
                    start_response,
                    "400 Bad Request",
                    b"Invalid form data\n",
                    method=method,
                    extra_headers=[("Cache-Control", "no-store")],
                )

        if method not in {"GET", "HEAD"}:
            return self._respond(
                start_response,
                "405 Method Not Allowed",
                b"Method not allowed\n",
                method=method,
                extra_headers=[("Allow", "GET, HEAD")],
            )

        self._refresh_sources()

        if path == "/":
            body = self._index_page().encode("utf-8")
            return self._respond(
                start_response,
                "200 OK",
                body,
                method=method,
                content_type="text/html; charset=utf-8",
                extra_headers=[
                    (
                        "Content-Security-Policy",
                        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
                        "base-uri 'none'; frame-ancestors 'none'",
                    )
                ],
            )

        if path == "/healthz":
            body = json.dumps(
                {"status": "ok", "sources": len(self.sources)}, separators=(",", ":")
            ).encode("utf-8")
            return self._respond(
                start_response,
                "200 OK",
                body,
                method=method,
                content_type="application/json; charset=utf-8",
                extra_headers=[("Cache-Control", "no-store")],
            )

        match = FEED_PATH_RE.fullmatch(path)
        if not match:
            return self._respond(
                start_response,
                "404 Not Found",
                b"Not found\n",
                method=method,
            )

        source = self.sources.get(match.group(1))
        if source is None:
            return self._respond(
                start_response,
                "404 Not Found",
                b"Unknown feed\n",
                method=method,
            )

        try:
            result = self.service.get_items(source)
        except SourceUnavailable:
            LOGGER.exception("Unable to serve source %s", source.source_id)
            return self._respond(
                start_response,
                "502 Bad Gateway",
                b"The source is temporarily unavailable\n",
                method=method,
                extra_headers=[("Retry-After", "300")],
            )

        self_url = (
            f"{self.settings.public_base_url}/feeds/{source.source_id}.xml"
            if self.settings.public_base_url
            else None
        )
        generated_at = datetime.fromtimestamp(result.fetched_at, tz=UTC)
        body = build_rss(
            source,
            result.items,
            self_url=self_url,
            generated_at=generated_at,
        )
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        if environ.get("HTTP_IF_NONE_MATCH") == etag:
            start_response(
                "304 Not Modified",
                [
                    ("ETag", etag),
                    ("Cache-Control", f"public, max-age={min(source.cache_seconds, 300)}"),
                    ("X-Content-Type-Options", "nosniff"),
                ],
            )
            return [b""]

        headers = [
            ("Cache-Control", f"public, max-age={min(source.cache_seconds, 300)}"),
            ("ETag", etag),
            ("Access-Control-Allow-Origin", "*"),
            ("X-Robots-Tag", "noindex"),
        ]
        if result.stale:
            headers.append(("Warning", '110 RegionalRSS "Response is stale"'))
            headers.append(("X-RegionalRSS-Stale", "true"))
        return self._respond(
            start_response,
            "200 OK",
            body,
            method=method,
            content_type="application/rss+xml; charset=utf-8",
            extra_headers=headers,
        )

    def _refresh_sources(self) -> None:
        try:
            self.sources = self.source_store.all()
        except ConfigurationError:
            LOGGER.exception("Unable to reload source definitions")

    def _handle_admin(
        self,
        environ: dict,
        start_response: StartResponse,
        method: str,
        path: str,
    ) -> list[bytes]:
        if self.sessions is None:
            return self._respond(
                start_response,
                "404 Not Found",
                b"Admin access is not configured\n",
                method=method,
            )

        authenticated = self.sessions.authenticated(environ.get("HTTP_COOKIE", ""))
        if path == "/admin/login":
            if method == "GET":
                if authenticated:
                    return self._redirect(start_response, "/admin")
                return self._admin_html(
                    start_response,
                    method,
                    self._login_page(),
                    extra_headers=[("Cache-Control", "no-store")],
                )
            if method == "POST":
                form = self._read_form(environ)
                username = self._form_value(form, "username")
                password = self._form_value(form, "password")
                valid = hmac.compare_digest(username, self.settings.admin_username or "")
                valid = valid and verify_password(
                    password, self.settings.admin_password_hash or ""
                )
                if not valid:
                    return self._admin_html(
                        start_response,
                        method,
                        self._login_page("Benutzername oder Passwort ist falsch."),
                        status="401 Unauthorized",
                        extra_headers=[("Cache-Control", "no-store")],
                    )
                secure = self._is_https(environ)
                return self._redirect(
                    start_response,
                    "/admin",
                    extra_headers=[
                        ("Set-Cookie", self.sessions.create_cookie(secure=secure))
                    ],
                )
            return self._method_not_allowed(start_response, method, "GET, POST")

        if not authenticated:
            return self._redirect(start_response, "/admin/login")

        if path == "/admin/logout":
            if method != "POST":
                return self._method_not_allowed(start_response, method, "POST")
            form = self._read_form(environ)
            if not self.sessions.valid_csrf(self._form_value(form, "csrf")):
                return self._forbidden(start_response, method)
            return self._redirect(
                start_response,
                "/admin/login",
                extra_headers=[
                    (
                        "Set-Cookie",
                        self.sessions.clear_cookie(secure=self._is_https(environ)),
                    )
                ],
            )

        self._refresh_sources()
        if path == "/admin":
            if method != "GET":
                return self._method_not_allowed(start_response, method, "GET")
            return self._admin_html(start_response, method, self._admin_index_page())

        if path == "/admin/sources/new":
            if method != "GET":
                return self._method_not_allowed(start_response, method, "GET")
            return self._admin_html(
                start_response, method, self._source_form_page(None)
            )

        edit_match = re.fullmatch(
            r"/admin/sources/([a-z0-9][a-z0-9-]{1,62}[a-z0-9])/edit", path
        )
        if edit_match:
            if method != "GET":
                return self._method_not_allowed(start_response, method, "GET")
            source = self.sources.get(edit_match.group(1))
            if source is None:
                return self._respond(
                    start_response, "404 Not Found", b"Unknown source\n", method=method
                )
            return self._admin_html(
                start_response, method, self._source_form_page(source)
            )

        if path == "/admin/sources/save":
            if method != "POST":
                return self._method_not_allowed(start_response, method, "POST")
            return self._save_or_test_source(environ, start_response, method)

        delete_match = re.fullmatch(
            r"/admin/sources/([a-z0-9][a-z0-9-]{1,62}[a-z0-9])/delete", path
        )
        if delete_match:
            if method != "POST":
                return self._method_not_allowed(start_response, method, "POST")
            form = self._read_form(environ)
            if not self.sessions.valid_csrf(self._form_value(form, "csrf")):
                return self._forbidden(start_response, method)
            source_id = delete_match.group(1)
            if source_id not in self.sources:
                return self._respond(
                    start_response, "404 Not Found", b"Unknown source\n", method=method
                )
            if len(self.sources) <= 1:
                return self._admin_html(
                    start_response,
                    method,
                    self._admin_index_page(
                        "Die letzte Quelle kann nicht gelöscht werden."
                    ),
                    status="400 Bad Request",
                )
            self.source_store.delete(source_id)
            self._refresh_sources()
            return self._redirect(start_response, "/admin?changed=deleted")

        return self._respond(
            start_response, "404 Not Found", b"Not found\n", method=method
        )

    def _save_or_test_source(
        self, environ: dict, start_response: StartResponse, method: str
    ) -> list[bytes]:
        assert self.sessions is not None
        form = self._read_form(environ)
        if not self.sessions.valid_csrf(self._form_value(form, "csrf")):
            return self._forbidden(start_response, method)
        previous_id = self._form_value(form, "previous_id") or None
        try:
            source = self._source_from_form(form)
            if previous_id and previous_id not in self.sources:
                raise ConfigurationError("Die zu bearbeitende Quelle existiert nicht mehr.")
            if source.source_id in self.sources and source.source_id != previous_id:
                raise ConfigurationError("Diese Feed-ID ist bereits vergeben.")
        except (ConfigurationError, ValueError) as exc:
            return self._admin_html(
                start_response,
                method,
                self._source_form_page(None, values=form, error=str(exc)),
                status="400 Bad Request",
            )

        if self._form_value(form, "action") == "test":
            try:
                response = fetch_html(
                    source.list_url,
                    user_agent=self.settings.user_agent,
                    timeout_seconds=self.settings.timeout_seconds,
                    max_response_bytes=self.settings.max_response_bytes,
                    allow_private_hosts=self.settings.allow_private_hosts,
                )
                if response.body is None:
                    raise FetchError("Die Quellseite hat keinen HTML-Inhalt geliefert.")
                items = extract_items(response.body, source)
            except (FetchError, ExtractionError) as exc:
                return self._admin_html(
                    start_response,
                    method,
                    self._source_form_page(
                        source, values=form, error=f"Test fehlgeschlagen: {exc}"
                    ),
                    status="400 Bad Request",
                )
            return self._admin_html(
                start_response,
                method,
                self._source_form_page(source, values=form, preview=items[:5]),
            )

        try:
            self.source_store.save(source, previous_id=previous_id)
            self._refresh_sources()
        except OSError as exc:
            LOGGER.exception("Unable to save source %s", source.source_id)
            return self._admin_html(
                start_response,
                method,
                self._source_form_page(
                    source, values=form, error=f"Speichern fehlgeschlagen: {exc}"
                ),
                status="500 Internal Server Error",
            )
        return self._redirect(start_response, "/admin?changed=saved")

    def _source_from_form(self, form: dict[str, list[str]]) -> SourceConfig:
        fields: dict[str, dict[str, str]] = {}
        for name in ("title", "link", "date", "summary", "image"):
            xpath = self._form_value(form, f"{name}_xpath").strip()
            attribute = self._form_value(form, f"{name}_attribute").strip()
            if xpath:
                fields[name] = {"xpath": xpath}
                if attribute:
                    fields[name]["attribute"] = attribute
        raw = {
            "id": self._form_value(form, "id").strip(),
            "name": self._form_value(form, "name").strip(),
            "description": self._form_value(form, "description").strip(),
            "site_url": self._form_value(form, "site_url").strip(),
            "list_url": self._form_value(form, "list_url").strip(),
            "language": self._form_value(form, "language").strip() or "de-DE",
            "timezone": self._form_value(form, "timezone").strip()
            or "Europe/Berlin",
            "cache_seconds": int(self._form_value(form, "cache_seconds") or "1800"),
            "max_items": int(self._form_value(form, "max_items") or "20"),
            "min_items": int(self._form_value(form, "min_items") or "1"),
            "item_xpath": self._form_value(form, "item_xpath").strip(),
            "fields": fields,
            "date_formats": [
                value.strip()
                for value in self._form_value(form, "date_formats").splitlines()
                if value.strip()
            ]
            or ["iso8601"],
            "categories": [
                value.strip()
                for value in self._form_value(form, "categories").split(",")
                if value.strip()
            ],
        }
        return parse_source(raw, "Formular")

    @staticmethod
    def _read_form(environ: dict) -> dict[str, list[str]]:
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
        except ValueError as exc:
            raise ValueError("Ungültige Formularlänge") from exc
        if length < 0 or length > 65536:
            raise ValueError("Das Formular ist zu groß")
        raw = environ.get("wsgi.input").read(length) if length else b""
        return parse_qs(raw.decode("utf-8"), keep_blank_values=True)

    @staticmethod
    def _form_value(form: dict[str, list[str]], name: str) -> str:
        values = form.get(name, [""])
        return values[0] if values else ""

    def _login_page(self, error: str | None = None) -> str:
        message = (
            f'<p class="message error">{html.escape(error)}</p>' if error else ""
        )
        return self._admin_layout(
            "Anmeldung",
            f"""
            <main class="narrow">
              <h1>RegionalRSS-Administration</h1>
              <p>Melde dich an, um Webseiten und deren Feed-Regeln zu verwalten.</p>
              {message}
              <form method="post" action="/admin/login">
                <label>Benutzername
                  <input name="username" autocomplete="username" required autofocus>
                </label>
                <label>Passwort
                  <input type="password" name="password" autocomplete="current-password" required>
                </label>
                <button type="submit">Anmelden</button>
              </form>
            </main>
            """,
            show_navigation=False,
        )

    def _admin_index_page(self, error: str | None = None) -> str:
        assert self.sessions is not None
        message = (
            f'<p class="message error">{html.escape(error)}</p>' if error else ""
        )
        cards: list[str] = []
        csrf = html.escape(self.sessions.csrf_token(), quote=True)
        for source in sorted(self.sources.values(), key=lambda item: item.name.casefold()):
            source_id = html.escape(source.source_id, quote=True)
            cards.append(
                '<article class="source-card">'
                f"<div><h2>{html.escape(source.name)}</h2>"
                f"<p>{html.escape(source.description)}</p>"
                f"<code>/feeds/{source_id}.xml</code></div>"
                '<div class="actions">'
                f'<a class="button secondary" href="/admin/sources/{source_id}/edit">Bearbeiten</a>'
                f'<a class="button secondary" href="/feeds/{source_id}.xml">Feed öffnen</a>'
                f'<form method="post" action="/admin/sources/{source_id}/delete">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                '<button class="danger" type="submit">Löschen</button></form>'
                "</div></article>"
            )
        return self._admin_layout(
            "Quellen",
            f"""
            <main>
              <div class="title-row">
                <div><h1>Quellen</h1><p>{len(self.sources)} konfigurierte RSS-Quelle(n)</p></div>
                <a class="button" href="/admin/sources/new">Neue Webseite</a>
              </div>
              {message}
              <section class="source-list">{''.join(cards)}</section>
            </main>
            """,
        )

    def _source_form_page(
        self,
        source: SourceConfig | None,
        *,
        values: dict[str, list[str]] | None = None,
        error: str | None = None,
        preview: list | None = None,
    ) -> str:
        assert self.sessions is not None

        def value(name: str, default: object = "") -> str:
            if values is not None and name in values:
                raw = self._form_value(values, name)
            else:
                raw = str(default)
            return html.escape(raw, quote=True)

        def field(name: str, part: str, default: str = "") -> str:
            if values is not None and f"{name}_{part}" in values:
                return value(f"{name}_{part}")
            if source and name in source.fields:
                rule = source.fields[name]
                raw = rule.xpath if part == "xpath" else (rule.attribute or "")
                return html.escape(raw, quote=True)
            return html.escape(default, quote=True)

        defaults = {
            "id": source.source_id if source else "",
            "name": source.name if source else "",
            "description": source.description if source else "",
            "site_url": source.site_url if source else "",
            "list_url": source.list_url if source else "",
            "language": source.language if source else "de-DE",
            "timezone": source.timezone if source else "Europe/Berlin",
            "cache_seconds": source.cache_seconds if source else 1800,
            "max_items": source.max_items if source else 20,
            "min_items": source.min_items if source else 1,
            "item_xpath": source.item_xpath if source else "//article",
            "date_formats": "\n".join(source.date_formats) if source else "iso8601",
            "categories": ", ".join(source.categories) if source else "",
        }
        message = (
            f'<p class="message error">{html.escape(error)}</p>' if error else ""
        )
        preview_html = ""
        if preview is not None:
            rows = "".join(
                "<tr>"
                f"<td>{html.escape(item.title)}</td>"
                f"<td>{html.escape(item.published.isoformat())}</td>"
                f"<td>{'ja' if item.image_url else 'nein'}</td>"
                "</tr>"
                for item in preview
            )
            preview_html = f"""
              <section class="preview">
                <h2>Test erfolgreich</h2>
                <p>Die ersten {len(preview)} erkannten Meldungen:</p>
                <div class="table-wrap"><table><thead><tr><th>Titel</th><th>Datum</th><th>Bild</th></tr></thead>
                <tbody>{rows}</tbody></table></div>
              </section>
            """

        title = "Quelle bearbeiten" if source else "Neue Webseite hinzufügen"
        previous_id = source.source_id if source else self._form_value(values or {}, "previous_id")
        csrf = html.escape(self.sessions.csrf_token(), quote=True)
        body = f"""
        <main>
          <div class="title-row"><div><h1>{title}</h1><p>Die XPath-Regeln bestimmen, welche Meldungen im RSS-Feed erscheinen.</p></div></div>
          {message}
          {preview_html}
          <form method="post" action="/admin/sources/save">
            <input type="hidden" name="csrf" value="{csrf}">
            <input type="hidden" name="previous_id" value="{html.escape(previous_id, quote=True)}">
            <fieldset><legend>Webseite</legend>
              <div class="grid two">
                <label>Feed-ID <small>z. B. osthessen-aktuelles</small>
                  <input name="id" pattern="[a-z0-9][a-z0-9-]{{1,62}}[a-z0-9]" value="{value('id', defaults['id'])}" required>
                </label>
                <label>Name<input name="name" value="{value('name', defaults['name'])}" required></label>
              </div>
              <label>Beschreibung<input name="description" value="{value('description', defaults['description'])}" required></label>
              <div class="grid two">
                <label>Startseite<input type="url" name="site_url" value="{value('site_url', defaults['site_url'])}" required></label>
                <label>Seite mit Meldungen<input type="url" name="list_url" value="{value('list_url', defaults['list_url'])}" required></label>
              </div>
            </fieldset>
            <fieldset><legend>Artikel erkennen</legend>
              <label>XPath eines Artikels<input name="item_xpath" value="{value('item_xpath', defaults['item_xpath'])}" required></label>
              <div class="rule-grid"><strong>Feld</strong><strong>XPath</strong><strong>Attribut</strong>
                <span>Titel *</span><input name="title_xpath" value="{field('title', 'xpath', './/h2[1]')}" required><input name="title_attribute" value="{field('title', 'attribute')}">
                <span>Link *</span><input name="link_xpath" value="{field('link', 'xpath', './/a[1]')}" required><input name="link_attribute" value="{field('link', 'attribute', 'href')}" required>
                <span>Datum *</span><input name="date_xpath" value="{field('date', 'xpath', './/time[1]')}" required><input name="date_attribute" value="{field('date', 'attribute', 'datetime')}">
                <span>Zusammenfassung</span><input name="summary_xpath" value="{field('summary', 'xpath')}"><input name="summary_attribute" value="{field('summary', 'attribute')}">
                <span>Bild-URL</span><input name="image_xpath" value="{field('image', 'xpath', './/img[1]')}"><input name="image_attribute" value="{field('image', 'attribute', 'src')}">
              </div>
            </fieldset>
            <fieldset><legend>Feed-Einstellungen</legend>
              <div class="grid three">
                <label>Cache in Sekunden<input type="number" min="60" max="86400" name="cache_seconds" value="{value('cache_seconds', defaults['cache_seconds'])}" required></label>
                <label>Maximale Artikel<input type="number" min="1" max="100" name="max_items" value="{value('max_items', defaults['max_items'])}" required></label>
                <label>Mindestens erkannt<input type="number" min="1" max="100" name="min_items" value="{value('min_items', defaults['min_items'])}" required></label>
              </div>
              <div class="grid two">
                <label>Sprache<input name="language" value="{value('language', defaults['language'])}" required></label>
                <label>Zeitzone<input name="timezone" value="{value('timezone', defaults['timezone'])}" required></label>
              </div>
              <div class="grid two">
                <label>Datumsformate <small>ein Format pro Zeile</small><textarea name="date_formats" rows="3">{value('date_formats', defaults['date_formats'])}</textarea></label>
                <label>Kategorien <small>mit Komma trennen</small><textarea name="categories" rows="3">{value('categories', defaults['categories'])}</textarea></label>
              </div>
            </fieldset>
            <div class="actions"><button name="action" value="save" type="submit">Quelle speichern</button>
              <button class="secondary" name="action" value="test" type="submit">Erkennung testen</button>
              <a class="button secondary" href="/admin">Abbrechen</a></div>
          </form>
        </main>
        """
        return self._admin_layout(title, body)

    def _admin_layout(
        self, title: str, body: str, *, show_navigation: bool = True
    ) -> str:
        navigation = ""
        if show_navigation and self.sessions is not None:
            csrf = html.escape(self.sessions.csrf_token(), quote=True)
            navigation = f"""
            <nav><a href="/">Öffentliche Feeds</a><a href="/admin">Quellen</a>
              <form method="post" action="/admin/logout"><input type="hidden" name="csrf" value="{csrf}"><button class="link" type="submit">Abmelden</button></form>
            </nav>
            """
        return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} – RegionalRSS</title>
<style>
:root {{ color-scheme: light dark; font-family: system-ui, sans-serif; --accent:#e06b20; }}
* {{ box-sizing:border-box; }} body {{ max-width:1050px; margin:auto; padding:1.5rem; line-height:1.5; }}
nav,.title-row,.actions {{ display:flex; gap:.8rem; align-items:center; flex-wrap:wrap; }} nav {{ justify-content:flex-end; margin-bottom:2rem; }} nav form {{ margin:0; }}
.title-row {{ justify-content:space-between; margin-bottom:1.5rem; }} h1,h2,p {{ margin-top:0; }}
form {{ display:grid; gap:1rem; }} fieldset {{ border:1px solid #8886; border-radius:12px; padding:1rem; display:grid; gap:1rem; }} legend {{ font-weight:700; padding:0 .4rem; }}
label {{ display:grid; gap:.35rem; font-weight:600; }} small {{ font-weight:400; opacity:.75; }} input,textarea {{ width:100%; padding:.7rem; border:1px solid #8888; border-radius:7px; font:inherit; }}
button,.button {{ border:0; border-radius:8px; padding:.7rem 1rem; background:var(--accent); color:white; font:inherit; font-weight:700; text-decoration:none; cursor:pointer; }}
.secondary {{ background:#667085; }} .danger {{ background:#b42318; }} button.link {{ background:none; color:inherit; padding:0; text-decoration:underline; }}
.grid {{ display:grid; gap:1rem; }} .source-list {{ display:grid; gap:1rem; }} .source-card {{ border:1px solid #8886; border-radius:12px; padding:1rem; display:flex; justify-content:space-between; gap:1rem; align-items:center; }} .source-card form {{ display:block; }}
.message,.preview {{ padding:1rem; border-radius:10px; }} .error {{ background:#b4231822; border:1px solid #b42318; }} .preview {{ background:#16803c22; border:1px solid #16803c; margin-bottom:1rem; }}
.rule-grid {{ display:grid; grid-template-columns:minmax(100px,.6fr) minmax(220px,2fr) minmax(100px,.7fr); gap:.6rem; align-items:center; }}
.table-wrap {{ overflow:auto; }} table {{ border-collapse:collapse; width:100%; }} th,td {{ text-align:left; border-bottom:1px solid #8886; padding:.5rem; }} .narrow {{ max-width:440px; margin:8vh auto; }}
@media (min-width:700px) {{ .grid.two {{ grid-template-columns:1fr 1fr; }} .grid.three {{ grid-template-columns:repeat(3,1fr); }} }}
@media (max-width:699px) {{ .source-card {{ align-items:flex-start; flex-direction:column; }} .rule-grid {{ grid-template-columns:1fr; }} .rule-grid > strong {{ display:none; }} }}
</style></head><body>{navigation}{body}</body></html>"""

    def _index_page(self) -> str:
        cards: list[str] = []
        for source in sorted(self.sources.values(), key=lambda item: item.name.casefold()):
            feed_path = f"/feeds/{source.source_id}.xml"
            cards.append(
                "<article>"
                f"<h2>{html.escape(source.name)}</h2>"
                f"<p>{html.escape(source.description)}</p>"
                '<div class="actions">'
                f'<a class="feed" href="{feed_path}">RSS-Feed öffnen</a>'
                f'<a href="{html.escape(source.site_url, quote=True)}">Originalseite</a>'
                "</div>"
                f'<code>{html.escape(feed_path)}</code>'
                "</article>"
            )
        cards_html = "".join(cards)
        title = html.escape(self.settings.site_title)
        admin_link = (
            '<a href="/admin">Quellen verwalten</a>' if self.sessions is not None else ""
        )
        return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ max-width: 840px; margin: 0 auto; padding: 3rem 1.2rem; line-height: 1.55; }}
    header {{ margin-bottom: 2.5rem; }}
    h1 {{ margin-bottom: .4rem; }}
    main {{ display: grid; gap: 1rem; }}
    article {{ border: 1px solid #8886; border-radius: 14px; padding: 1.2rem; }}
    article h2 {{ margin-top: 0; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: .8rem; margin: 1rem 0; }}
    a {{ color: inherit; }}
    a.feed {{ background: #e06b20; color: white; padding: .55rem .8rem; border-radius: 8px; text-decoration: none; }}
    code {{ overflow-wrap: anywhere; }}
    footer {{ margin-top: 2.5rem; font-size: .9rem; opacity: .75; }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <p>RSS-Feeds für Webseiten, die selbst keinen passenden Feed anbieten.</p>
  </header>
  <main>{cards_html}</main>
  <footer>
    Bilder werden nicht gespeichert oder weiterverteilt. Der RSS-Client lädt sie bei Bedarf direkt von der jeweiligen Originalseite.
    {admin_link}
  </footer>
</body>
</html>"""

    def _admin_html(
        self,
        start_response: StartResponse,
        method: str,
        page: str,
        *,
        status: str = "200 OK",
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> list[bytes]:
        headers = [
            ("Cache-Control", "no-store"),
            (
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            ),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        return self._respond(
            start_response,
            status,
            page.encode("utf-8"),
            method=method,
            content_type="text/html; charset=utf-8",
            extra_headers=headers,
        )

    @staticmethod
    def _redirect(
        start_response: StartResponse,
        location: str,
        *,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> list[bytes]:
        headers = [
            ("Location", location),
            ("Content-Length", "0"),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        start_response("303 See Other", headers)
        return [b""]

    def _method_not_allowed(
        self, start_response: StartResponse, method: str, allowed: str
    ) -> list[bytes]:
        return self._respond(
            start_response,
            "405 Method Not Allowed",
            b"Method not allowed\n",
            method=method,
            extra_headers=[("Allow", allowed)],
        )

    def _forbidden(self, start_response: StartResponse, method: str) -> list[bytes]:
        return self._respond(
            start_response,
            "403 Forbidden",
            b"Invalid CSRF token\n",
            method=method,
            extra_headers=[("Cache-Control", "no-store")],
        )

    @staticmethod
    def _is_https(environ: dict) -> bool:
        return (
            environ.get("HTTP_X_FORWARDED_PROTO", "").split(",", 1)[0].strip()
            == "https"
            or environ.get("wsgi.url_scheme") == "https"
        )

    @staticmethod
    def _respond(
        start_response: StartResponse,
        status: str,
        body: bytes,
        *,
        method: str,
        content_type: str = "text/plain; charset=utf-8",
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> list[bytes]:
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(status, headers)
        return [b"" if method == "HEAD" else body]


def create_application(settings: Settings | None = None) -> RegionalRssApplication:
    return RegionalRssApplication(settings or Settings.from_environment())
