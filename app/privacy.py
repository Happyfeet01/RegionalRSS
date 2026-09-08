from __future__ import annotations

import html
import os


ACCENT_COLOR = "#f5a04b"
ACCENT_TEXT = "#33200c"


def _env(name: str, fallback: str) -> str:
    return os.getenv(name, "").strip() or fallback


def render_privacy_page(site_title: str) -> str:
    controller = _env(
        "REGIONALRSS_PRIVACY_CONTROLLER_NAME",
        "Vom Betreiber noch in der .env zu ergänzen",
    )
    address = _env(
        "REGIONALRSS_PRIVACY_CONTROLLER_ADDRESS",
        "Anschrift noch nicht hinterlegt",
    )
    email = _env(
        "REGIONALRSS_PRIVACY_EMAIL",
        "Datenschutz-Kontakt noch nicht hinterlegt",
    )
    hoster = _env(
        "REGIONALRSS_PRIVACY_HOSTING_PROVIDER",
        "Hosting-Anbieter noch nicht hinterlegt",
    )
    mail_provider = _env(
        "REGIONALRSS_PRIVACY_MAIL_PROVIDER",
        "der in RegionalRSS konfigurierte E-Mail-Anbieter",
    )
    backup_retention = _env(
        "REGIONALRSS_PRIVACY_BACKUP_RETENTION",
        "bis zum Ablauf der vom Betreiber festgelegten Backup-Aufbewahrung",
    )

    esc = html.escape
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>Datenschutzerklärung – {esc(site_title)}</title>
  <style>
    :root {{ color-scheme:light dark; font-family:system-ui,sans-serif; --accent:{ACCENT_COLOR}; }}
    * {{ box-sizing:border-box; }}
    body {{ max-width:850px; margin:auto; padding:2rem 1.2rem; line-height:1.6; }}
    h1,h2 {{ line-height:1.2; }} h2 {{ margin-top:2rem; }}
    a {{ color:inherit; }} .back {{ display:inline-block; margin-bottom:1.5rem; }}
    .notice {{ border:1px solid #8886; border-left:5px solid var(--accent); border-radius:10px; padding:1rem; }}
    code {{ overflow-wrap:anywhere; }}
  </style>
</head>
<body>
  <a class="back" href="/">← Zurück zu RegionalRSS</a>
  <main>
    <h1>Datenschutzerklärung</h1>
    <p>Diese Datenschutzerklärung beschreibt die Datenverarbeitung durch {esc(site_title)} (RegionalRSS).</p>

    <h2>1. Verantwortlicher</h2>
    <p><strong>{esc(controller)}</strong><br>{esc(address)}<br>E-Mail: {esc(email)}</p>

    <h2>2. Welche Daten RegionalRSS verarbeitet</h2>
    <p>Für ein Nutzerkonto werden Benutzername, E-Mail-Adresse, ein kryptografischer Passwort-Hash, Zeitpunkte zur Registrierung und E-Mail-Bestätigung sowie die Zuordnung der vom Nutzer angelegten Feeds gespeichert. Das Klartext-Passwort wird nicht gespeichert.</p>
    <p>Für E-Mail-Bestätigungen werden nur zufällige Einmal-Token in gehashter Form und mit einer Gültigkeit von höchstens 24 Stunden gespeichert. Sicherheits- und Rate-Limit-Einträge werden nur vorübergehend gespeichert und spätestens nach dem vorgesehenen kurzen Schutzzeitraum bereinigt.</p>

    <h2>3. Zweck und Rechtsgrundlagen</h2>
    <p>Kontodaten werden verarbeitet, um das Nutzerkonto und die Feed-Verwaltung bereitzustellen (Art. 6 Abs. 1 lit. b DSGVO). Sicherheitsmaßnahmen wie Anmelde-, Bestätigungs- und Rate-Limits dienen dem Schutz des Dienstes und seiner Nutzer (Art. 6 Abs. 1 lit. f DSGVO). Soweit gesetzliche Pflichten bestehen, kann die Verarbeitung zusätzlich auf Art. 6 Abs. 1 lit. c DSGVO beruhen.</p>

    <h2>4. Server- und Zugriffsprotokolle</h2>
    <p>Die für RegionalRSS mitgelieferte Nginx-Konfiguration schreibt in das normale Zugriffsprotokoll <strong>keine IP-Adresse</strong>, keinen Referer und keine Query-Strings. Dadurch gelangen insbesondere E-Mail-Bestätigungstoken nicht in das Access-Log.</p>
    <p>Für technische Schutzfunktionen verwendet RegionalRSS nur abgeleitete beziehungsweise kurzlebige Kennungen. Der Hosting-Anbieter kann im Rahmen des technischen Betriebs des Servers unabhängig von RegionalRSS Verbindungsdaten verarbeiten. Hosting-Anbieter dieser Instanz: <strong>{esc(hoster)}</strong>.</p>

    <h2>5. Cookies und Sitzungen</h2>
    <p>RegionalRSS verwendet ausschließlich technisch notwendige Sitzungscookies für angemeldete Nutzer und Administratoren. Sie dienen der Anmeldung und dem Schutz vor unberechtigten Formularaufrufen. Es gibt keine Werbe-, Analyse- oder Tracking-Cookies. Nutzersitzungen sind zeitlich begrenzt.</p>

    <h2>6. E-Mail-Versand</h2>
    <p>Für Registrierung und Änderung einer E-Mail-Adresse versendet RegionalRSS Bestätigungsnachrichten. Dabei werden Empfängeradresse und Nachrichteninhalt an den konfigurierten Mailanbieter übermittelt. Eingesetzter Mailanbieter: <strong>{esc(mail_provider)}</strong>.</p>

    <h2>7. Öffentliche Feeds und externe Webseiten</h2>
    <p>Die mit RegionalRSS erzeugten Feeds sind öffentlich abrufbar. Die Anweisung <code>noindex</code> bittet Suchmaschinen, Seiten und Feeds nicht zu indexieren; sie macht einen Feed jedoch nicht privat oder zugriffsgeschützt.</p>
    <p>RegionalRSS ruft die eingetragene Quellwebseite vom Server aus ab, um Meldungen zu erkennen. Bilder werden nicht auf dem RegionalRSS-Server gespeichert. Im Feed bleibt die Original-Bildadresse erhalten. Lädt ein RSS-Reader dieses Bild, baut der RSS-Reader eine direkte Verbindung zur jeweiligen Quellwebseite auf; dabei kann diese insbesondere die IP-Adresse des Readers verarbeiten.</p>

    <h2>8. Speicherdauer und Kontolöschung</h2>
    <p>Kontodaten werden grundsätzlich gespeichert, solange das Konto besteht. Nutzer können ihr Konto in den Einstellungen selbst löschen. Dabei werden das Konto, seine E-Mail-Daten, Bestätigungstoken, Feed-Zuordnungen und die vom Konto angelegten Feed-Konfigurationen aus dem laufenden System entfernt.</p>
    <p>Bereits vorhandene Sicherungskopien können gelöschte Daten noch enthalten: {esc(backup_retention)}. Sie werden nicht erneut in das Produktivsystem eingespielt, außer dies ist für eine notwendige Wiederherstellung erforderlich.</p>

    <h2>9. Empfänger</h2>
    <p>Daten werden nur an Dienstleister übermittelt, soweit dies für Hosting und E-Mail-Versand erforderlich ist. Eine Weitergabe zu Werbe- oder Profilingzwecken findet nicht statt.</p>

    <h2>10. Rechte betroffener Personen</h2>
    <p>Betroffene Personen haben nach Maßgabe der DSGVO insbesondere Rechte auf Auskunft, Berichtigung, Löschung, Einschränkung der Verarbeitung, Datenübertragbarkeit und – soweit die Verarbeitung auf berechtigten Interessen beruht – Widerspruch. Außerdem besteht ein Beschwerderecht bei einer zuständigen Datenschutzaufsichtsbehörde.</p>

    <h2>11. Keine automatisierte Entscheidungsfindung</h2>
    <p>RegionalRSS verwendet keine automatisierte Entscheidungsfindung und kein Profiling im Sinne von Art. 22 DSGVO.</p>

    <div class="notice"><strong>Hinweis für den Betreiber:</strong> Vor der öffentlichen Freigabe müssen Verantwortlicher, Anschrift, Datenschutzkontakt, Hosting-/Mailanbieter und die tatsächliche Backup-Aufbewahrung in der <code>.env</code> korrekt eingetragen werden. Diese technische Vorlage ersetzt keine individuelle Rechtsberatung.</div>
  </main>
</body>
</html>"""
