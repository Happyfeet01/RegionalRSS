import unittest
from unittest.mock import patch

from app.auth import SessionManager, UserSessionManager, hash_password, verify_password


class PasswordTest(unittest.TestCase):
    def test_password_hash_round_trip(self) -> None:
        encoded = hash_password("a-long-and-secure-password")
        self.assertTrue(verify_password("a-long-and-secure-password", encoded))
        self.assertFalse(verify_password("a-different-password", encoded))
        self.assertNotIn("a-long-and-secure-password", encoded)

    def test_short_password_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            hash_password("too-short")


class SessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = SessionManager(
            "lars", "test-session-secret-with-more-than-32-characters"
        )

    @patch("app.auth.time.time", return_value=1_000_000)
    def test_signed_session_cookie(self, _time) -> None:  # noqa: ANN001
        cookie = self.sessions.create_cookie(secure=True)
        cookie_header = cookie.split(";", 1)[0]
        self.assertTrue(self.sessions.authenticated(cookie_header))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Secure", cookie)

    @patch("app.auth.time.time", return_value=1_000_000)
    def test_tampered_session_cookie_is_rejected(self, _time) -> None:  # noqa: ANN001
        cookie = self.sessions.create_cookie(secure=False).split(";", 1)[0]
        self.assertFalse(self.sessions.authenticated(cookie + "x"))

    @patch("app.auth.time.time", return_value=1_000_000)
    def test_user_session_contains_signed_username(self, _time) -> None:  # noqa: ANN001
        sessions = UserSessionManager("test-session-secret-with-more-than-32-characters")
        cookie = sessions.create_cookie("lars", secure=True)
        self.assertEqual("lars", sessions.username(cookie.split(";", 1)[0]))
        self.assertTrue(sessions.valid_csrf("lars", sessions.csrf_token("lars")))


if __name__ == "__main__":
    unittest.main()
