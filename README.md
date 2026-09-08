# RegionalRSS

RegionalRSS erzeugt RSS-2.0-Feeds für Webseiten, die selbst keinen passenden
Feed anbieten. Die Anwendung ist nicht auf Flieden beschränkt: Jede unterstützte
Webseite liegt als kleine YAML-Quellenkonfiguration im Ordner `sources/`.

Enthaltene Beispiel-Feeds sind:

```text
/feeds/flieden-aktuelles.xml
/feeds/neuhof-pressemitteilungen.xml
```

Sie enthalten Überschrift, Veröffentlichungszeit, Anreißer, Kategorien, Link zum
Original und die originale Artikelbild-URL. Bilddateien werden von RegionalRSS
weder heruntergeladen noch gespeichert. Der RSS-Client lädt ein Bild erst bei
der Anzeige direkt von der Quellseite.

## Eigenschaften

- RSS 2.0 mit `content:encoded`, `media:content` und `media:thumbnail`
- automatische Erkennung bereits vorhandener RSS-, Atom- und JSON-Feeds
- automatische Ableitung von Regeln für übliche HTML-Meldungslisten
- beliebig viele freigeschaltete Quellen über die Oberfläche oder YAML-Dateien
- stabile Artikel-GUIDs auf Basis der Original-URL
- SQLite-Cache: Eine Quellseite wird unabhängig von der Zahl der Abonnenten nur
  im festgelegten Intervall abgerufen
- Unterstützung für `ETag` und `Last-Modified`
- letzter erfolgreicher Feed bleibt bei einem vorübergehenden Quellfehler
  verfügbar
- Schutz vor privaten beziehungsweise lokalen Zieladressen und unsicheren
  Weiterleitungen
- Nutzerkonten mit persönlicher Feed-Verwaltung und einem konfigurierbaren Limit
- E-Mail-Bestätigung für neue Konten, Systemabsender und SMTP über `.env`
- öffentliche Feed-Übersicht unter `/`
- geschützte Administrationsoberfläche zum Hinzufügen und Testen neuer Quellen
- `noindex, nofollow` als HTML-Metadaten, HTTP-Header und `robots.txt`
- Gesundheitsprüfung unter `/healthz`

## Schnellstart mit Docker

```bash
cp .env.example .env
nano .env
docker compose up -d --build
curl http://127.0.0.1:8787/healthz
```

Danach ist die Übersicht lokal unter `http://127.0.0.1:8787/` erreichbar.

Der Feed für Flieden lautet lokal:

```text
http://127.0.0.1:8787/feeds/flieden-aktuelles.xml
```

## Administrationskonto einrichten

Die öffentlichen Feeds benötigen weiterhin keine Anmeldung. Das zentrale
Hinzufügen, Bearbeiten und Löschen von Webseiten unter `/admin` ist geschützt.

Zuerst Passwort-Hash und Sitzungsschlüssel erzeugen:

```bash
docker compose run --rm --no-deps regionalrss python scripts/hash_password.py
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Die beiden Ausgaben zusammen mit dem Benutzernamen in `.env` eintragen:

```dotenv
REGIONALRSS_ADMIN_USER=lars
REGIONALRSS_ADMIN_PASSWORD_HASH=scrypt.16384.8.1...
REGIONALRSS_SESSION_SECRET=hier-steht-der-lange-zufällige-schlüssel
```

Wichtig: Entweder müssen alle drei `REGIONALRSS_ADMIN_...`- beziehungsweise
`REGIONALRSS_SESSION_SECRET`-Werte gesetzt sein oder alle drei leer bleiben.
Eine nur teilweise eingerichtete Anmeldung wird aus Sicherheitsgründen nicht
gestartet.

Anschließend RegionalRSS neu erstellen und starten:

```bash
docker compose up -d --build
```

Die Verwaltung ist danach unter
`https://feeds.dasnetzundich.de/admin` erreichbar. Dort kann eine Quelle
angelegt, bearbeitet, vor dem Speichern getestet oder wieder gelöscht werden.
Die beim Test gefundenen Artikel werden als Vorschau angezeigt; Bilddateien
werden auch dabei nicht gespeichert.

Die Quellen liegen in einem eigenen Docker-Volume. Damit können sie von der UI
geschrieben werden und bleiben bei Container-Updates erhalten. Beim Start werden
neue mitgelieferte Standardquellen nur ergänzt, wenn noch keine gleichnamige
Datei im Volume existiert. Eigene oder bearbeitete Quellen werden nie überschrieben.

## Nutzerkonten und automatische Prüfung

Wenn `REGIONALRSS_SESSION_SECRET` gesetzt ist, steht zusätzlich die Anmeldung
unter `/login` bereit. Die öffentliche Registrierung wird über `.env` gesteuert:

```dotenv
REGIONALRSS_ALLOW_REGISTRATION=true
REGIONALRSS_MAX_SOURCES_PER_USER=20
```

Unter `/my-feeds` trägt ein Nutzer nur noch die Seite mit den Meldungen ein.
RegionalRSS lädt deren HTML und arbeitet anschließend in dieser Reihenfolge:

1. Im HTML angekündigten RSS-, Atom- oder JSON-Feed finden und validieren.
2. Wenn kein Feed vorhanden ist, wiederkehrende Artikelkarten, Überschrift,
   Link, Datum, Kurztext und Original-Bild-URL erkennen.
3. Die Quelle dem Nutzerkonto zuordnen und öffentlich auf der Startseite listen.

Die Konten und Zuordnungen liegen in `/data/accounts.sqlite3`. Passwörter werden
mit Scrypt gehasht. Schreibende Aktionen sind durch signierte Cookies und
CSRF-Token geschützt. Private und lokale Zieladressen bleiben gegen SSRF
gesperrt. Für eine rein private Installation kann die Registrierung mit
`REGIONALRSS_ALLOW_REGISTRATION=false` abgeschaltet werden.

### Systemabsender und E-Mail-Bestätigung

Neue Konten benötigen ab Version 0.4.0 eine eindeutige E-Mail-Adresse. Bis zur
Bestätigung führt die Anmeldung zur Bestätigungsseite; eigene Feeds lassen sich
erst danach anlegen und verwalten. Das öffentliche Feed-Verzeichnis bleibt ohne
Anmeldung nutzbar. Nutzeradressen werden dort nicht angezeigt.

In `.env` die SMTP-Daten deines Mailanbieters und einen dort erlaubten Absender
eintragen. Die Anwendung legt **kein Postfach beim Mailanbieter** an.

```dotenv
REGIONALRSS_PUBLIC_BASE_URL=https://rss.dasnetzundich.de
REGIONALRSS_ALLOW_REGISTRATION=true
REGIONALRSS_MAIL_FROM=regionalrss@deine-domain.de
REGIONALRSS_MAIL_FROM_NAME=RegionalRSS
REGIONALRSS_SMTP_HOST=smtp.dein-mailanbieter.de
REGIONALRSS_SMTP_PORT=587
REGIONALRSS_SMTP_SECURITY=starttls
REGIONALRSS_SMTP_USER=regionalrss@deine-domain.de
REGIONALRSS_SMTP_PASSWORD='DEIN_SMTP_PASSWORT'
```

Für direktes TLS stattdessen `REGIONALRSS_SMTP_SECURITY=ssl` und den vom Anbieter
genannten Port (üblicherweise 465) verwenden. Benutzername und Passwort dürfen
beide leer bleiben, wenn der konfigurierte SMTP-Server keine Anmeldung verlangt.
TLS-Zertifikate werden geprüft; eine unverschlüsselte Rückfallverbindung gibt es
nicht. Das Passwort in einfachen Anführungszeichen schützt insbesondere `$` vor
der Compose-Variablenersetzung.

`REGIONALRSS_PUBLIC_BASE_URL` muss die öffentliche **HTTPS-Adresse** der Instanz
ohne Unterpfad sein. Der Bestätigungslink wird ausschließlich aus dieser
Konfiguration erzeugt. Ohne vollständige Mailkonfiguration nimmt die Anwendung
keine neuen Registrierungen an und zeigt eine verständliche Meldung. Bestehende
Feeds und der Adminzugang funktionieren weiter.

Der Bestätigungslink gilt 24 Stunden. Erst ein Klick auf „E-Mail-Adresse
bestätigen“ auf der geöffneten Seite verbraucht ihn; automatisches Öffnen durch
Mailprogramme tut das nicht. Der Link bestätigt nur die Adresse und meldet kein
Gerät automatisch an. In SQLite steht nur der SHA-256-Hash des zufälligen Tokens.
Nach erfolgreicher Bestätigung werden alle Links dieses Kontos ungültig.

Bei abgelaufenen Links oder Versandproblemen mit Benutzername und Passwort
anmelden und „Bestätigungsmail erneut senden“ wählen. Mindestens eine Minute
Abstand und höchstens fünf Versandversuche pro Stunde und Konto sind erlaubt.
Vorherige, noch gültige Links bleiben bei erneutem Versand gültig, bis einer
bestätigt wird. Schlägt SMTP fehl, bleibt das Konto vorgemerkt und kann denselben
Ablauf später erneut versuchen. Fehler erscheinen ohne Adressen, Token oder
Zugangsdaten im Container-Log. Die Registrierung ist zusätzlich auf zehn Versuche
pro 15 Minuten und Verbindungspartner begrenzt. Hinter einem Reverse Proxy
teilen Nutzer dieses Limit; clientseitige Forwarded-Header werden nicht vertraut.

### Update von 0.3.x

Vor dem Update `/data/accounts.sqlite3` zusammen mit den übrigen Anwendungsdaten
sichern (bei einer Dateikopie den Container vorher anhalten). Dann die obigen
Werte in `.env` ergänzen und neu bauen:

```bash
git pull --ff-only
docker compose up -d --build
docker compose logs --tail=100 regionalrss
```

Die SQLite-Migration läuft beim Start automatisch und erhält vorhandene Konten,
Passwort-Hashes und Feed-Zuordnungen. Konten aus Version 0.3.x bleiben ohne
nachträgliche Bestätigung nutzbar; neue Konten sind auch nach einem Neustart bis
zur Bestätigung gesperrt. Der bestehende Administrator aus `.env` ist unabhängig
von den registrierten Nutzerkonten.

Die mitgelieferten Gunicorn- und Nginx-Zugriffslogformate lassen Query-Strings und
Referer weg, damit Bestätigungstoken nicht im normalen Access-Log landen. Bei
einer bestehenden Nginx-Konfiguration die `log_format regionalrss_safe`- und
`access_log ... regionalrss_safe`-Zeilen aus `nginx/regionalrss.conf` übernehmen,
im HTTPS-Server `Referrer-Policy no-referrer` setzen und mit `nginx -t` prüfen.
Das gilt entsprechend für einen anders eingerichteten Reverse Proxy.

Zum Prüfen nach dem Update mit einer eigenen E-Mail-Adresse registrieren, den
Posteingang kontrollieren und den Link bestätigen. Die automatisierten Tests
simulieren SMTP; sie prüfen nicht die Zustellung bei deinem Mailanbieter.

Technische Grundlagen: [Python SMTP](https://docs.python.org/3/library/smtplib.html)
und [OWASP zu Einmaltokens](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).

### Kontoeinstellungen und Kontenübersicht (ab 0.5.0)

Die Navigation enthält jetzt direkt die Einstellungen. Die Oberflächen verwenden
einheitlich das hellere Orange mit dunkler Beschriftung.

| Bereich | Adresse | Inhalt |
| --- | --- | --- |
| Nutzer | `/settings` | Eigene E-Mail-Adresse, Bestätigungsstatus, Feedanzahl sowie E-Mail und Passwort ändern |
| Admin | `/admin/accounts` | Registrierte Konten mit E-Mail-Adresse, Status, Feedanzahl und Registrierungsdatum |
| Admin | `/admin/settings` | Aktuelle Serverkonfiguration, Systemabsender und SMTP-Konfiguration ohne Zugangsdaten |

E-Mail- und Passwortänderungen benötigen das aktuelle Passwort. Eine neue
E-Mail-Adresse wird vorgemerkt und erst nach Bestätigung übernommen. Eine bereits
bestätigte Adresse und der Zugriff auf eigene Feeds bleiben bis dahin erhalten.
Auch Bestandskonten ohne E-Mail können dort eine Adresse ergänzen. Noch nicht
bestätigte neue Nutzer können eine falsch eingegebene Adresse korrigieren, bleiben
aber bis zur Bestätigung für die Feedverwaltung gesperrt. Der erneute Mailversand
nutzt dieselben Versandlimits wie die Registrierung.

Eine Passwortänderung beendet andere bestehende Nutzersitzungen; das gerade
verwendete Gerät erhält eine neue Sitzung. Nutzereinstellungen und die
Admin-Kontenübersicht sind zugriffsgeschützt und werden nicht öffentlich gecacht.
Adressen erscheinen weiterhin nicht im öffentlichen Feedverzeichnis.

Das Administratorkonto aus `.env` ist ein eigenes Systemkonto. Es hat keine
persönliche E-Mail-Adresse und ist in der Kontenübersicht separat gekennzeichnet.
Ein zusätzlich registriertes Nutzerkonto hat sein eigenes Profil unter
`/settings`. Ein gleicher Benutzername verleiht diesem Konto keine Adminrechte.
Systemeinstellungen werden in der Adminoberfläche angezeigt und weiterhin in
`.env` geändert; danach `docker compose up -d` ausführen.

Beim Update auf 0.5.0 werden vorhandene Konten und noch gültige Bestätigungslinks
automatisch migriert. Die Updatebefehle oben gelten weiterhin. Wegen der neuen
Trennung von Admin- und Nutzersignaturen muss sich der Admin einmal neu anmelden.

Die automatische Erkennung deckt übliche serverseitig ausgelieferte
Meldungslisten ab. Bei ungewöhnlichem HTML kann der Administrator weiterhin die
XPath-Expertenmaske verwenden. Seiten mit reiner JavaScript-Ausgabe oder
Bot-Prüfung lassen sich dadurch nicht automatisch erschließen.

## Mit Nginx veröffentlichen

`compose.yaml` veröffentlicht RegionalRSS absichtlich nur auf
`127.0.0.1:8787`. Nginx kann den Dienst dadurch erreichen, aus dem Internet ist
der Anwendungsport jedoch nicht direkt zugänglich.

Für die erste Zertifikatsausstellung liegt eine reine HTTP-Konfiguration bei:

```bash
sudo cp nginx/regionalrss-bootstrap.conf /etc/nginx/sites-available/regionalrss.conf
sudo ln -s /etc/nginx/sites-available/regionalrss.conf /etc/nginx/sites-enabled/regionalrss.conf
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d feeds.dasnetzundich.de
```

Alternativ kann nach vorhandener Zertifikatsausstellung die mitgelieferte
vollständige HTTPS-Konfiguration verwendet werden:

```bash
sudo cp nginx/regionalrss.conf /etc/nginx/sites-available/regionalrss.conf
sudo nginx -t
sudo systemctl reload nginx
```

Vorher in `.env` die öffentliche Adresse eintragen und RegionalRSS neu starten:

```dotenv
REGIONALRSS_PUBLIC_BASE_URL=https://feeds.dasnetzundich.de
```

Falls eine andere Subdomain verwendet wird, müssen `server_name`, die beiden
Zertifikatspfade und `REGIONALRSS_PUBLIC_BASE_URL` entsprechend angepasst
werden. Die Feed-Domain darf nicht durch eine Anubis-Prüfung geschützt werden,
da RSS-Clients diese nicht lösen können.

## Mit Traefik starten

In `.env` müssen mindestens `FEED_HOST`, `REGIONALRSS_PUBLIC_BASE_URL`,
`TRAEFIK_NETWORK` und gegebenenfalls `TRAEFIK_CERTRESOLVER` zur vorhandenen
Installation passen. Anschließend:

```bash
docker compose -f compose.yaml -f compose.traefik.yaml up -d --build
```

Für die Beispielkonfiguration wäre der öffentliche Feed anschließend:

```text
https://feeds.dasnetzundich.de/feeds/flieden-aktuelles.xml
```

Die veröffentlichte Feed-Domain sollte nicht hinter einer Anubis-Prüfung liegen,
weil RSS-Clients diese nicht lösen können. Der Traefik-Aufbau enthält stattdessen
ein einfaches Rate-Limit; die Anwendung selbst reduziert Quellabrufe durch ihren
Cache.

## Weitere Webseiten manuell hinzufügen

1. `sources/_template.yml.example` unter einem neuen Namen kopieren, zum
   Beispiel `sources/osthessen-beispiel.yml`.
2. Eine eindeutige `id`, die beiden URLs und die XPath-Ausdrücke eintragen.
3. Die Definition prüfen:

   ```bash
   python scripts/check_source.py sources/osthessen-beispiel.yml
   ```

4. Den Container neu starten:

   ```bash
   docker compose restart regionalrss
   ```

Der neue Feed ist dann automatisch unter
`/feeds/<id>.xml` auf der Übersichtsseite vorhanden. Es muss kein Python-Code
angepasst werden.

## Suchmaschinen ausschließen

Die öffentliche Übersicht soll von Menschen und RSS-Clients verwendet, aber
nicht in Suchmaschinen aufgenommen werden. RegionalRSS setzt deshalb auf allen
Antworten `X-Robots-Tag: noindex, nofollow`, ergänzt das entsprechende
HTML-Metafeld und liefert eine `robots.txt` mit `Disallow: /`. Die mitgelieferte
Nginx-Konfiguration setzt denselben Header zusätzlich am Reverse Proxy.

### Aufbau einer Quelle

```yaml
id: beispiel-aktuelles
name: Beispielseite – Aktuelles
description: Neue Meldungen der Beispielseite.
site_url: https://www.example.org/
list_url: https://www.example.org/aktuelles/
cache_seconds: 1800
max_items: 20
min_items: 1

item_xpath: //article[contains(@class, 'news-item')]

fields:
  title:
    xpath: .//h2[1]
  link:
    xpath: .//a[1]
    attribute: href
  date:
    xpath: .//time[1]
    attribute: datetime
  summary:
    xpath: .//*[contains(@class, 'summary')][1]
  image:
    xpath: .//img[1]
    attribute: src

date_formats:
  - iso8601
```

`min_items` dient als Layout-Wächter. Erkennt die Konfiguration nach einer
Änderung der Quellseite zu wenige Meldungen, ersetzt RegionalRSS den letzten
funktionierenden Feed nicht durch einen leeren oder beschädigten Feed.

Relative Artikel- und Bildpfade werden automatisch auf Basis von `list_url` in
absolute URLs umgewandelt. Bei der Bildregel wird nur der Wert des angegebenen
Attributes übernommen; die Bilddatei selbst wird nicht angefordert.

## Grenzen

Die allgemeine Engine verarbeitet Meldungen, die im ausgelieferten HTML stehen.
Seiten, die ihre Inhalte ausschließlich per JavaScript oder über eine versteckte
API nachladen, benötigen später einen eigenen Adapter. Bezahlschranken,
Zugriffsschutz und Bot-Prüfungen werden nicht umgangen.

Ändert eine Webseite ihre HTML-Struktur, muss lediglich ihre YAML-Datei
angepasst werden. Die übrigen Feeds und die Anwendung bleiben davon unberührt.

## Tests

```bash
python -m unittest discover -s tests -v
```

Die Tests prüfen unter anderem:

- Auslesen der aktuell verwendeten Flieden-Struktur
- Erhalt der unveränderten Originalbild-URL
- gültige RSS-2.0-Ausgabe
- Cache-Speicherung ohne Bilddateien
- Feed-Endpunkt und HTTP-ETag

## Datenschutz und Betrieb

RegionalRSS speichert Feed-Metadaten und die Bild-URL als Text in SQLite. Bei
Nutzerkonten kommen Benutzername, E-Mail-Adresse, Passwort-Hash,
Bestätigungsstatus und zeitlich begrenzte Token-Hashes hinzu. Es gibt keinen
Bildproxy und kein Artikelarchiv. Vor einer
öffentlichen Bereitstellung sollten Impressum und Datenschutzhinweise auf der
Domain ergänzt sowie die jeweiligen Nutzungsbedingungen der Quellen geprüft
werden.

Lizenz: MIT
