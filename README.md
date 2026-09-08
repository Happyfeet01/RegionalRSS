# RegionalRSS

RegionalRSS erzeugt RSS-2.0-Feeds für Webseiten, die selbst keinen passenden
Feed anbieten. Die Anwendung ist nicht auf Flieden beschränkt: Jede unterstützte
Webseite liegt als kleine YAML-Quellenkonfiguration im Ordner `sources/`.

Der erste enthaltene Feed ist:

```text
/feeds/flieden-aktuelles.xml
```

Er enthält Überschrift, Veröffentlichungszeit, Anreißer, Kategorien, Link zum
Original und die originale Artikelbild-URL. Bilddateien werden von RegionalRSS
weder heruntergeladen noch gespeichert. Der RSS-Client lädt ein Bild erst bei
der Anzeige direkt von der Quellseite.

## Eigenschaften

- RSS 2.0 mit `content:encoded`, `media:content` und `media:thumbnail`
- beliebig viele freigeschaltete Quellen über YAML-Dateien
- stabile Artikel-GUIDs auf Basis der Original-URL
- SQLite-Cache: Eine Quellseite wird unabhängig von der Zahl der Abonnenten nur
  im festgelegten Intervall abgerufen
- Unterstützung für `ETag` und `Last-Modified`
- letzter erfolgreicher Feed bleibt bei einem vorübergehenden Quellfehler
  verfügbar
- Schutz vor privaten beziehungsweise lokalen Zieladressen und unsicheren
  Weiterleitungen
- kein öffentliches Feld für beliebige URLs
- kleine öffentliche Feed-Übersicht unter `/`
- geschützte Administrationsoberfläche zum Hinzufügen und Testen neuer Quellen
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

Die öffentlichen Feeds benötigen weiterhin keine Anmeldung. Nur das Hinzufügen,
Bearbeiten und Löschen von Webseiten unter `/admin` ist geschützt. Es gibt
bewusst keine öffentliche Registrierung.

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
geschrieben werden und bleiben bei Container-Updates erhalten.

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

## Weitere Webseiten hinzufügen

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

RegionalRSS speichert nur die ausgelesenen Feed-Metadaten und die Bild-URL als
Text in SQLite. Es gibt keinen Bildproxy und kein Artikelarchiv. Vor einer
öffentlichen Bereitstellung sollten Impressum und Datenschutzhinweise auf der
Domain ergänzt sowie die jeweiligen Nutzungsbedingungen der Quellen geprüft
werden.

Lizenz: MIT
