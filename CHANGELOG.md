# Changelog

## 0.3.0

- URL-basierte automatische Erkennung über Admin- und Nutzeroberfläche
- vorhandene RSS-, Atom- und JSON-Feeds erkennen, validieren und übernehmen
- HTML-Meldungslisten automatisch in RegionalRSS-Quellen umwandeln
- selbst registrierbare Nutzerkonten mit persönlicher Feed-Verwaltung
- öffentliche Übersicht für vorhandene und erzeugte Feeds
- `noindex, nofollow` per HTML, HTTP und `robots.txt`
- fertige Quellenkonfiguration für die Pressemitteilungen der Gemeinde Neuhof
- sichere Ergänzung neuer Standardquellen in bereits bestehende Docker-Volumes

## 0.2.0

- geschützte Administrationsoberfläche unter `/admin`
- Webseiten hinzufügen, bearbeiten, testen und löschen
- Passwort-Hashes mit Scrypt und signierte Sitzungscookies
- CSRF-Schutz für schreibende Aktionen
- persistentes Docker-Volume für Quellenkonfigurationen
- Nginx-Konfiguration für HTTP-Einrichtung und HTTPS-Betrieb

## 0.1.0

- generische YAML-basierte Umwandlung von Webseiten in RSS 2.0
- SQLite-Cache, ETag und letzter erfolgreicher Feed als Rückfall
- Original-Bild-URLs ohne lokalen Bilddownload
- erste Quellenkonfiguration für flieden.de
