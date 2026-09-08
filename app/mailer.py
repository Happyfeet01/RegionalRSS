from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from urllib.parse import urlparse


LOGGER = logging.getLogger(__name__)


def normalize_email(value: str) -> str:
    """Accept one mailbox, never a display name, address list or header."""
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Bitte eine gültige E-Mail-Adresse angeben.")
    value = value.strip()
    if value.count("@") != 1:
        raise ValueError("Bitte eine gültige E-Mail-Adresse angeben.")
    local, domain = value.rsplit("@", 1)
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Bitte eine gültige E-Mail-Adresse angeben.") from exc
    if (
        not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}", local)
        or local.startswith(".") or local.endswith(".") or ".." in local
        or len(domain) > 253 or "." not in domain
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
               for part in domain.split("."))
        or len(local) + len(domain) + 1 > 254
    ):
        raise ValueError("Bitte eine gültige E-Mail-Adresse angeben.")
    return f"{local.lower()}@{domain}"


class MailDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class MailSettings:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = field(default="", repr=False)
    security: str = "starttls"
    from_address: str = ""
    from_name: str = "RegionalRSS"
    timeout: int = 10

    @classmethod
    def from_environment(cls) -> "MailSettings":
        try:
            port = int(os.getenv("REGIONALRSS_SMTP_PORT", "587"))
        except ValueError:
            # A mistyped mail setting disables signups, not existing feeds.
            port = 0
        return cls(
            host=os.getenv("REGIONALRSS_SMTP_HOST", "").strip(),
            port=port,
            username=os.getenv("REGIONALRSS_SMTP_USER", "").strip(),
            password=os.getenv("REGIONALRSS_SMTP_PASSWORD", ""),
            security=os.getenv("REGIONALRSS_SMTP_SECURITY", "starttls").strip().lower(),
            from_address=os.getenv("REGIONALRSS_MAIL_FROM", "").strip(),
            from_name=os.getenv("REGIONALRSS_MAIL_FROM_NAME", "RegionalRSS").strip(),
        )


class VerificationMailer:
    def __init__(self, settings: MailSettings, public_base_url: str | None) -> None:
        self.settings = settings
        self.public_base_url = (public_base_url or "").rstrip("/")

    @property
    def configured(self) -> bool:
        settings = self.settings
        try:
            normalize_email(settings.from_address)
            base = urlparse(self.public_base_url)
            port = base.port
        except ValueError:
            return False
        return bool(
            settings.host and 1 <= settings.port <= 65535
            and settings.security in {"starttls", "ssl"}
            and bool(settings.username) == bool(settings.password)
            and not any(ord(c) < 32 for c in settings.from_name + settings.host)
            and base.scheme == "https" and base.hostname
            and not base.username and not base.password
            and not base.query and not base.fragment and base.path in {"", "/"}
            and not any(c.isspace() for c in self.public_base_url)
            and (port is None or 1 <= port <= 65535)
        )

    def send_verification(self, recipient: str, token: str) -> None:
        if not self.configured:
            raise MailDeliveryError("Mailversand ist nicht eingerichtet.")
        settings = self.settings
        message = EmailMessage()
        sender = normalize_email(settings.from_address)
        recipient = normalize_email(recipient)
        message["From"] = formataddr((settings.from_name, sender))
        message["To"] = recipient
        message["Subject"] = "RegionalRSS: E-Mail-Adresse bestätigen"
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[1])
        message.set_content(
            "Hallo,\n\nbitte bestätige deine E-Mail-Adresse für dein RegionalRSS-Konto.\n\n"
            f"{self.public_base_url}/verify-email?token={token}\n\n"
            "Öffne den Link und klicke auf »E-Mail-Adresse bestätigen«. "
            "Der Link ist 24 Stunden gültig und kann nur einmal verwendet werden.\n\n"
            "Falls du dich nicht registriert hast, kannst du diese E-Mail ignorieren.\n"
        )
        context = ssl.create_default_context()
        try:
            if settings.security == "ssl":
                connection = smtplib.SMTP_SSL(
                    settings.host, settings.port, timeout=settings.timeout, context=context
                )
            else:
                connection = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout)
            with connection as smtp:
                if settings.security == "starttls":
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                if settings.username:
                    smtp.login(settings.username, settings.password)
                smtp.send_message(message, from_addr=sender, to_addrs=[recipient])
        except (OSError, smtplib.SMTPException) as exc:
            # SMTP errors can contain recipients, credentials or message content.
            LOGGER.error("Verification mail delivery failed (%s)", type(exc).__name__)
            raise MailDeliveryError("Die Bestätigungsmail konnte nicht versendet werden.") from None
