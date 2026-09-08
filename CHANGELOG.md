# Changelog

## 0.6.0

- automatische Erkennung für Meldungslisten ohne sichtbares Veröffentlichungsdatum erweitert
- wiederkehrende generische Inhaltsblöcke auch ohne typische Klassen wie `news`, `card` oder `article` erkennen
- Veröffentlichungsdatum im erzeugten Feed optional; ohne Datum wird kein `pubDate` ausgegeben
- verständlichere Experteneinstellungen mit Erklärungen statt technischer Kurzbegriffe
- öffentliche Datenschutzerklärung unter `/datenschutz` mit konfigurierbaren Betreiberangaben
- Gunicorn- und Nginx-Access-Logs ohne Client-IP, Query-Strings oder Referer
- Nutzer können ihr Konto mit aktuellem Passwort und zusätzlicher Bestätigung vollständig löschen
- beim Löschen eines Kontos werden auch seine Feed-Zuordnungen, Bestätigungstoken und eigenen Feed-Konfigurationen entfernt
- Administratoren können registrierte Nutzerkonten samt zugehörigen Feeds löschen
- Datenschutzlink auf öffentlichen, Nutzer- und Administrationsseiten

## 0.5.0

- hellere orangefarbene Schaltflächen auf öffentlichen, Nutzer- und Adminseiten
- sichtbare Navigation zu Nutzereinstellungen, Admin-Kontenübersicht und Systemeinstellungen
- eigene E-Mail-Adresse und Bestätigungsstatus anzeigen; Änderungen mit aktuellem Passwort bestätigen
- neue E-Mail-Adresse erst nach Bestätigung übernehmen; bestehende Adresse bis dahin erhalten
- Passwortänderung mit Beendigung älterer Nutzersitzungen
- Adminübersicht mit Benutzernamen, E-Mail-Adressen, Bestätigungsstatus und Feedanzahl
- Systemeinstellungen ohne Anzeige von Passwörtern oder Sitzungsschlüsseln
- getrennte Signaturen für Admin- und Nutzersitzungen; Admin nach dem Update einmal neu anmelden

## 0.4.0

- Systemabsender und SMTP-Konfiguration über `.env`, STARTTLS oder direktes TLS
- Pflicht-E-Mail-Adresse bei neuen Konten und Freischaltung nach Bestätigung
- einmalige Bestätigungslinks mit 24 Stunden Gültigkeit, nur Hashes in SQLite
- erneuter Versand nach Anmeldung mit CSRF-Schutz und Versandlimits
- bestehende Konten und Quellen bleiben bei der Datenbankmigration erhalten
- Registrierung bleibt ohne Mailkonfiguration gesperrt; bestehende Feeds und Adminzugang bleiben erreichbar
- Gunicorn- und Nginx-Zugriffslogs ohne Query-Strings und Referer

## 0.3.0

- URL-basierte automatische Erkennung über Admin- und Nutzeroberfläche
- vorhandene RSS-, Atom- und JSON-Feeds erkennen, validieren und übernehmen
- HTML-Meldungslisten automatisch in RegionalRSS-Quellen umwandeln
- selbst registrierbare Nutzerkonten mit persönlicher Feed-Verwaltung
- öffentliche Übersicht für vorhandene und erzeugte Feeds
- `noindex, nofollow` per HTML, HTTP und `robots.txt`
- fertige Quellenkonfiguration für die Pressemitteilungen der Gemeinde Neuhof
- sichere Ergänzung neuer Standardquellen in bereits bestehende Docker-Volumes
- fehlende HTML-Antwortfunktion für Login, Registrierung und persönliche Feeds ergänzt

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
