from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie


PASSWORD_N = 2**14
PASSWORD_R = 8
PASSWORD_P = 1
SESSION_SECONDS = 12 * 60 * 60


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("The admin password must contain at least 12 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=PASSWORD_N, r=PASSWORD_R, p=PASSWORD_P
    )
    return ".".join(
        (
            "scrypt",
            str(PASSWORD_N),
            str(PASSWORD_R),
            str(PASSWORD_P),
            _encode(salt),
            _encode(digest),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split(".")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(digest, _decode(expected))
    except (ValueError, TypeError):
        return False


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class SessionManager:
    username: str
    secret: str

    def create_cookie(self, *, secure: bool) -> str:
        expires = int(time.time()) + SESSION_SECONDS
        payload = f"{self.username}|{expires}"
        signature = self._sign(payload)
        flags = [
            f"regionalrss_session={_encode(payload.encode('utf-8'))}.{signature}",
            "Path=/admin",
            f"Max-Age={SESSION_SECONDS}",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if secure:
            flags.append("Secure")
        return "; ".join(flags)

    @staticmethod
    def clear_cookie(*, secure: bool) -> str:
        flags = [
            "regionalrss_session=",
            "Path=/admin",
            "Max-Age=0",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if secure:
            flags.append("Secure")
        return "; ".join(flags)

    def authenticated(self, cookie_header: str) -> bool:
        try:
            cookies = SimpleCookie(cookie_header)
            value = cookies["regionalrss_session"].value
            encoded_payload, signature = value.rsplit(".", 1)
            payload = _decode(encoded_payload).decode("utf-8")
            username, expires_raw = payload.split("|", 1)
            return (
                username == self.username
                and int(expires_raw) >= int(time.time())
                and hmac.compare_digest(signature, self._sign(payload))
            )
        except (KeyError, ValueError, UnicodeDecodeError):
            return False

    def csrf_token(self) -> str:
        return self._sign(f"csrf|{self.username}")

    def valid_csrf(self, token: str) -> bool:
        return hmac.compare_digest(token, self.csrf_token())

    def _sign(self, payload: str) -> str:
        digest = hmac.new(
            self.secret.encode("utf-8"), ("admin|" + payload).encode("utf-8"), hashlib.sha256
        ).digest()
        return _encode(digest)


@dataclass(frozen=True)
class UserSessionManager:
    secret: str

    def create_cookie(self, username: str, *, secure: bool, version: int = 0) -> str:
        expires = int(time.time()) + SESSION_SECONDS
        payload = f"{username}|{expires}|{version}"
        flags = [
            f"regionalrss_user_session={_encode(payload.encode('utf-8'))}.{self._sign(payload)}",
            "Path=/",
            f"Max-Age={SESSION_SECONDS}",
            "HttpOnly",
            "SameSite=Lax",
        ]
        if secure:
            flags.append("Secure")
        return "; ".join(flags)

    @staticmethod
    def clear_cookie(*, secure: bool) -> str:
        flags = [
            "regionalrss_user_session=",
            "Path=/",
            "Max-Age=0",
            "HttpOnly",
            "SameSite=Lax",
        ]
        if secure:
            flags.append("Secure")
        return "; ".join(flags)

    def username(self, cookie_header: str) -> str | None:
        session = self.read_session(cookie_header)
        return session[0] if session else None

    def read_session(self, cookie_header: str) -> tuple[str, int] | None:
        try:
            cookies = SimpleCookie(cookie_header)
            value = cookies["regionalrss_user_session"].value
            encoded_payload, signature = value.rsplit(".", 1)
            payload = _decode(encoded_payload).decode("utf-8")
            parts = payload.split("|")
            # Existing cookies have no version and remain valid until a password change.
            if len(parts) == 2:
                username, expires_raw = parts
                version = 0
            else:
                username, expires_raw, raw_version = parts
                version = int(raw_version)
            if int(expires_raw) < int(time.time()):
                return None
            if not hmac.compare_digest(signature, self._sign(payload)):
                return None
            return username, version
        except (KeyError, ValueError, UnicodeDecodeError):
            return None

    def csrf_token(self, username: str) -> str:
        return self._sign(f"csrf|{username}")

    def valid_csrf(self, username: str, token: str) -> bool:
        return hmac.compare_digest(token, self.csrf_token(username))

    def _sign(self, payload: str) -> str:
        digest = hmac.new(
            self.secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).digest()
        return _encode(digest)
