from dataclasses import replace
import smtplib
import unittest
from unittest.mock import patch

from app.application import Settings
from app.mailer import MailDeliveryError, MailSettings, VerificationMailer, normalize_email


class MailerTest(unittest.TestCase):
    def setUp(self):
        self.settings = MailSettings(host="smtp.example.com", username="smtp-user",
            password="smtp-secret", from_address="rss@example.com", from_name="RegionalRSS")
        self.mailer = VerificationMailer(self.settings, "https://rss.example.com")

    @patch("app.mailer.smtplib.SMTP")
    def test_starttls_precedes_auth_and_message_uses_fixed_origin(self, smtp_factory):
        smtp = smtp_factory.return_value.__enter__.return_value
        self.mailer.send_verification("User@example.com", "opaque-token")
        smtp_factory.assert_called_once_with("smtp.example.com", 587, timeout=10)
        self.assertEqual(["ehlo", "starttls", "ehlo", "login", "send_message"],
                         [call[0] for call in smtp.method_calls])
        self.assertTrue(smtp.starttls.call_args.kwargs["context"].check_hostname)
        smtp.login.assert_called_once_with("smtp-user", "smtp-secret")
        message = smtp.send_message.call_args.args[0]
        self.assertEqual("RegionalRSS <rss@example.com>", message["From"])
        self.assertEqual("user@example.com", message["To"])
        self.assertIn("https://rss.example.com/verify-email?token=opaque-token", message.get_content())
        self.assertIn("24 Stunden", message.get_content())
        self.assertNotIn("smtp-secret", str(message))
        self.assertEqual(["user@example.com"], smtp.send_message.call_args.kwargs["to_addrs"])

    @patch("app.mailer.smtplib.SMTP_SSL")
    def test_ssl_465(self, smtp_factory):
        self.mailer.settings = replace(self.settings, security="ssl", port=465)
        self.mailer.send_verification("user@example.com", "token")
        smtp = smtp_factory.return_value.__enter__.return_value
        self.assertEqual(("smtp.example.com", 465), smtp_factory.call_args.args)
        self.assertTrue(smtp_factory.call_args.kwargs["context"].check_hostname)
        smtp.starttls.assert_not_called()
        smtp.login.assert_called_once()

    @patch("app.mailer.smtplib.SMTP")
    def test_no_tls_fallback_or_secret_in_error_log(self, smtp_factory):
        smtp = smtp_factory.return_value.__enter__.return_value
        smtp.starttls.side_effect = smtplib.SMTPNotSupportedError("smtp-secret user@example.com token")
        with self.assertLogs("app.mailer", level="ERROR") as logs:
            with self.assertRaises(MailDeliveryError):
                self.mailer.send_verification("user@example.com", "token")
        smtp.login.assert_not_called()
        smtp.send_message.assert_not_called()
        self.assertNotIn("smtp-secret", str(logs.output))
        self.assertNotIn("user@example.com", str(logs.output))

    def test_invalid_config_and_header_injection_are_rejected(self):
        for email in ("x@example.com\r\nBcc:y@example.com", "two@example.com,three@example.com",
                      "User <user@example.com>", "bad..dots@example.com", "user@localhost"):
            with self.subTest(email=email), self.assertRaises(ValueError):
                normalize_email(email)
        self.assertEqual("a+tag@xn--bcher-kva.de", normalize_email("A+tag@bücher.de"))
        for override in ({"security": "none"}, {"port": 0}, {"password": ""},
                         {"from_name": "Name\r\nBcc: attack"}, {"from_address": ""}):
            self.assertFalse(VerificationMailer(replace(self.settings, **override), "https://rss.example.com").configured)
        for url in (None, "http://rss.example.com", "https://user:password@rss.example.com", "https://rss.example.com?evil=1"):
            self.assertFalse(VerificationMailer(self.settings, url).configured)

    def test_mail_env_is_passed_to_application_settings(self):
        with patch.dict("os.environ", {
            "REGIONALRSS_MAIL_FROM": "rss@example.com", "REGIONALRSS_MAIL_FROM_NAME": "Feeds",
            "REGIONALRSS_SMTP_HOST": "smtp.example.com", "REGIONALRSS_SMTP_PORT": "465",
            "REGIONALRSS_SMTP_USER": "smtp-user", "REGIONALRSS_SMTP_PASSWORD": "smtp-secret",
            "REGIONALRSS_SMTP_SECURITY": "ssl",
        }, clear=True):
            settings = Settings.from_environment()
        self.assertEqual("rss@example.com", settings.mail.from_address)
        self.assertEqual("Feeds", settings.mail.from_name)
        self.assertEqual(465, settings.mail.port)
        self.assertEqual("smtp-secret", settings.mail.password)
        self.assertNotIn("smtp-secret", repr(settings))
        with patch.dict("os.environ", {"REGIONALRSS_SMTP_PORT": "not-a-port"}, clear=True):
            invalid = Settings.from_environment()
        self.assertFalse(VerificationMailer(invalid.mail, "https://rss.example.com").configured)


if __name__ == "__main__":
    unittest.main()
