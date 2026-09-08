from __future__ import annotations

import html
import re
from typing import Iterable

from .application import RegionalRssApplication, Settings, StartResponse
from .auth import verify_password
from .privacy import render_privacy_page


class RegionalRssWebApplication(RegionalRssApplication):
    """Public web application with release-facing privacy/account controls.

    The core feed engine stays in ``RegionalRssApplication``. This layer keeps
    public/legal pages and destructive account actions isolated and testable.
    """

    def __call__(self, environ: dict, start_response: StartResponse) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")

        if path == "/datenschutz":
            if method not in {"GET", "HEAD"}:
                return self._method_not_allowed(start_response, method, "GET, HEAD")
            return self._respond(
                start_response,
                "200 OK",
                render_privacy_page(self.settings.site_title).encode("utf-8"),
                method=method,
                content_type="text/html; charset=utf-8",
                extra_headers=[
                    ("Cache-Control", "public, max-age=300"),
                    (
                        "Content-Security-Policy",
                        "default-src 'none'; style-src 'unsafe-inline'; "
                        "base-uri 'none'; frame-ancestors 'none'",
                    ),
                ],
            )

        if path == "/account/delete":
            return self._delete_own_account(environ, start_response, method)

        return super().__call__(environ, start_response)

    def _current_user(self, environ: dict):
        if self.user_sessions is None:
            return None
        session = self.user_sessions.read_session(environ.get("HTTP_COOKIE", ""))
        if not session:
            return None
        account = self.accounts.get(session[0])
        if account is None or account.session_version != session[1]:
            return None
        return account

    def _remove_account_and_sources(self, username: str) -> None:
        source_ids = self.accounts.source_ids(username)
        for source_id in source_ids:
            self.source_store.delete(source_id)
        self.accounts.delete_account(username)
        self._refresh_sources()

    def _delete_own_account(
        self, environ: dict, start_response: StartResponse, method: str
    ) -> list[bytes]:
        if self.user_sessions is None:
            return self._respond(
                start_response,
                "404 Not Found",
                b"User accounts are not configured\n",
                method=method,
            )
        account = self._current_user(environ)
        if account is None:
            return self._redirect(start_response, "/login")
        if method != "POST":
            return self._method_not_allowed(start_response, method, "POST")

        form = self._read_form(environ)
        if not self.user_sessions.valid_csrf(
            account.username, self._form_value(form, "csrf")
        ):
            return self._forbidden(start_response, method)

        stored_hash = self.accounts.password_hash(account.username)
        if not stored_hash or not verify_password(
            self._form_value(form, "current_password"), stored_hash
        ):
            return self._account_html(
                start_response,
                method,
                self._settings_page(
                    account.username,
                    "Das aktuelle Passwort ist falsch. Das Konto wurde nicht gelöscht.",
                    error=True,
                ),
                status="400 Bad Request",
            )
        if self._form_value(form, "confirm_username").strip() != account.username:
            return self._account_html(
                start_response,
                method,
                self._settings_page(
                    account.username,
                    "Bitte gib deinen Benutzernamen exakt ein, um die Löschung zu bestätigen.",
                    error=True,
                ),
                status="400 Bad Request",
            )

        self._remove_account_and_sources(account.username)
        return self._redirect(
            start_response,
            "/",
            extra_headers=[
                (
                    "Set-Cookie",
                    self.user_sessions.clear_cookie(secure=self._is_https(environ)),
                )
            ],
        )

    def _handle_admin(
        self,
        environ: dict,
        start_response: StartResponse,
        method: str,
        path: str,
    ) -> list[bytes]:
        delete_match = re.fullmatch(r"/admin/accounts/([^/]+)/delete", path)
        if not delete_match:
            return super()._handle_admin(environ, start_response, method, path)

        if self.sessions is None:
            return self._respond(
                start_response,
                "404 Not Found",
                b"Admin access is not configured\n",
                method=method,
            )
        if not self.sessions.authenticated(environ.get("HTTP_COOKIE", "")):
            return self._redirect(start_response, "/admin/login")
        if method != "POST":
            return self._method_not_allowed(start_response, method, "POST")

        form = self._read_form(environ)
        if not self.sessions.valid_csrf(self._form_value(form, "csrf")):
            return self._forbidden(start_response, method)
        username = delete_match.group(1)
        if self.accounts.get(username) is None:
            return self._respond(
                start_response,
                "404 Not Found",
                b"Unknown account\n",
                method=method,
            )
        self._remove_account_and_sources(username)
        return self._redirect(start_response, "/admin/accounts?deleted=1")

    def _settings_page(
        self, username: str, message: str = "", *, error: bool = False
    ) -> str:
        page = super()._settings_page(username, message, error=error)
        if self.user_sessions is None:
            return page
        csrf = html.escape(self.user_sessions.csrf_token(username), quote=True)
        safe_username = html.escape(username)
        danger = f'''
          <section class="panel danger-zone">
            <h2>Konto löschen</h2>
            <p>Damit werden dein Konto und alle von dir angelegten Feeds aus RegionalRSS gelöscht. Dieser Schritt kann nicht rückgängig gemacht werden.</p>
            <form method="post" action="/account/delete">
              <input type="hidden" name="csrf" value="{csrf}">
              <label>Aktuelles Passwort
                <input type="password" name="current_password" autocomplete="current-password" required>
              </label>
              <label>Zur Bestätigung deinen Benutzernamen eingeben
                <small>{safe_username}</small>
                <input name="confirm_username" autocomplete="off" required>
              </label>
              <button class="danger" type="submit">Konto und meine Feeds endgültig löschen</button>
            </form>
          </section>
        '''
        return page.replace("</div></main>", danger + "</div></main>", 1)

    def _admin_accounts_page(self) -> str:
        page = super()._admin_accounts_page()
        if self.sessions is None:
            return page
        accounts = self.accounts.list_accounts()
        if not accounts:
            return page
        csrf = html.escape(self.sessions.csrf_token(), quote=True)
        rows = []
        for account, feed_count in accounts:
            username = html.escape(account.username)
            path_username = html.escape(account.username, quote=True)
            rows.append(
                f'''<li><strong>{username}</strong> – {feed_count} Feed(s)
                <form method="post" action="/admin/accounts/{path_username}/delete">
                  <input type="hidden" name="csrf" value="{csrf}">
                  <button class="danger" type="submit">Konto und Feeds löschen</button>
                </form></li>'''
            )
        management = f'''
          <section class="panel">
            <h2>Konten verwalten</h2>
            <p>Beim Löschen werden auch die von diesem Konto angelegten öffentlichen Feeds entfernt.</p>
            <ul class="account-actions">{''.join(rows)}</ul>
          </section>
        '''
        return page.replace("</main>", management + "</main>", 1)

    def _source_form_page(
        self,
        source,
        *,
        values=None,
        error: str | None = None,
        preview=None,
    ) -> str:
        # Render the base form without its date-assuming preview. Undated pages
        # are valid in 0.6 and get a dedicated, human-readable preview below.
        page = super()._source_form_page(
            source, values=values, error=error, preview=None
        )
        if preview is not None:
            rows = "".join(
                "<tr>"
                f"<td>{html.escape(item.title)}</td>"
                f"<td>{html.escape(item.published.isoformat()) if item.published else 'kein Datum auf der Seite'}</td>"
                f"<td>{'ja' if item.image_url else 'nein'}</td>"
                "</tr>"
                for item in preview
            )
            preview_html = f'''
              <section class="preview">
                <h2>Test erfolgreich</h2>
                <p>Die ersten {len(preview)} erkannten Meldungen:</p>
                <div class="table-wrap"><table><thead><tr><th>Titel</th><th>Datum</th><th>Bild</th></tr></thead>
                <tbody>{rows}</tbody></table></div>
              </section>
            '''
            page = page.replace(
                '<form method="post" action="/admin/sources/save">',
                preview_html + '<form method="post" action="/admin/sources/save">',
                1,
            )

        replacements = {
            "Die XPath-Regeln bestimmen, welche Meldungen im RSS-Feed erscheinen.":
                "Diese Einstellungen brauchst du nur, wenn die automatische Erkennung eine Webseite nicht richtig versteht.",
            "<legend>Artikel erkennen</legend>":
                "<legend>Experteneinstellungen – wo findet RegionalRSS die Meldungen?</legend>",
            "XPath eines Artikels":
                "Wo beginnt eine einzelne Meldung? <small>Beschreibt den wiederholten Block, der jeweils genau eine Nachricht enthält.</small>",
            "<strong>Feld</strong><strong>XPath</strong><strong>Attribut</strong>":
                "<strong>Gesuchter Inhalt</strong><strong>Wo steht er?</strong><strong>Welcher Wert?</strong>",
            "<span>Titel *</span>":
                "<span>Titel * <small>Überschrift der Meldung</small></span>",
            "<span>Link *</span>":
                "<span>Link * <small>Adresse zum Originalartikel</small></span>",
            "<span>Datum *</span>":
                "<span>Datum <small>optional – leer lassen, wenn die Seite keines zeigt</small></span>",
            "<span>Zusammenfassung</span>":
                "<span>Kurztext <small>optional</small></span>",
            "<span>Bild-URL</span>":
                "<span>Artikelbild <small>optional; das Bild selbst wird nicht gespeichert</small></span>",
            "Cache in Sekunden":
                "Wie oft soll die Webseite neu geprüft werden? <small>in Sekunden, z. B. 1800 = 30 Minuten</small>",
            "Maximale Artikel":
                "Höchstens so viele Meldungen in den Feed",
            "Mindestens erkannt":
                "Mindestens so viele Meldungen müssen erkannt werden",
            "Datumsformate <small>ein Format pro Zeile</small>":
                "Datumsdarstellung <small>nur nötig, wenn ein Datum vorhanden ist; ein Format pro Zeile</small>",
            "Kategorien <small>mit Komma trennen</small>":
                "Kategorien <small>optional, z. B. Rathaus, Lokales; mit Komma trennen</small>",
        }
        for old, new in replacements.items():
            page = page.replace(old, new)

        # A publication date is optional for municipal pages such as Kalbach.
        page = page.replace(
            'name="date_xpath" value=',
            'name="date_xpath" value=',
        )
        page = page.replace(
            'name="date_xpath" value=".//time[1]" required',
            'name="date_xpath" value=".//time[1]"',
        )
        # Also remove required when the value comes from an existing source.
        page = re.sub(
            r'(<input name="date_xpath"[^>]*?)\srequired(>)', r'\1\2', page
        )
        return page

    def _account_layout(self, title: str, body: str, *, username: str | None = None) -> str:
        page = super()._account_layout(title, body, username=username)
        footer = '<footer class="legal"><a href="/datenschutz">Datenschutz</a></footer>'
        return page.replace("</body>", footer + "</body>", 1)

    def _admin_layout(self, title: str, body: str, *, show_navigation: bool = True) -> str:
        page = super()._admin_layout(title, body, show_navigation=show_navigation)
        footer = '<footer class="legal"><a href="/datenschutz">Datenschutz</a></footer>'
        return page.replace("</body>", footer + "</body>", 1)

    def _index_page(self) -> str:
        page = super()._index_page()
        return page.replace(
            "</footer>",
            '<br><a href="/datenschutz">Datenschutzerklärung</a></footer>',
            1,
        )


def create_web_application(settings: Settings | None = None) -> RegionalRssWebApplication:
    return RegionalRssWebApplication(settings or Settings.from_environment())
