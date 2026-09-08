from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .mailer import normalize_email


VERIFICATION_SECONDS = 24 * 60 * 60
RESEND_SECONDS = 60


@dataclass(frozen=True)
class Account:
    username: str
    email: str | None
    email_verified_at: int | None
    verification_required: bool

    @property
    def active(self) -> bool:
        return not self.verification_required or self.email_verified_at is not None


class VerificationThrottled(ValueError):
    pass


class AccountStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            # Gunicorn workers may migrate concurrently; serialize schema changes.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    username TEXT PRIMARY KEY COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )""")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(accounts)")}
            for name, definition in (
                ("email", "TEXT COLLATE NOCASE"),
                ("email_verified_at", "INTEGER"),
                # Only pre-existing accounts are grandfathered. New INSERTs set 1.
                ("verification_required", "INTEGER NOT NULL DEFAULT 0"),
                ("verification_sent_at", "INTEGER"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS accounts_email ON accounts(email COLLATE NOCASE)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS source_owners (
                    source_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(username) REFERENCES accounts(username) ON DELETE CASCADE
                )""")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS email_verifications (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE,
                    expires_at INTEGER NOT NULL,
                    FOREIGN KEY(username) REFERENCES accounts(username) ON DELETE CASCADE
                )""")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS account_rate_limits (
                    key TEXT PRIMARY KEY,
                    window_start INTEGER NOT NULL,
                    attempts INTEGER NOT NULL
                )""")

    def create(self, username: str, password_hash: str, *, email: str) -> bool:
        email = normalize_email(email)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO accounts(username, password_hash, created_at, email, verification_required) VALUES (?, ?, ?, ?, 1)",
                    (username, password_hash, int(time.time()), email),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def get(self, username: str) -> Account | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT username, email, email_verified_at, verification_required FROM accounts WHERE username = ?",
                (username,),
            ).fetchone()
        return Account(row[0], row[1], row[2], bool(row[3])) if row else None

    def take_rate_limit(self, key: str, *, limit: int, seconds: int) -> bool:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM account_rate_limits WHERE window_start <= ?", (now - 86400,))
            row = connection.execute(
                "SELECT window_start, attempts FROM account_rate_limits WHERE key = ?", (key,)
            ).fetchone()
            if row and row[0] > now - seconds:
                if row[1] >= limit:
                    return False
                connection.execute("UPDATE account_rate_limits SET attempts = attempts + 1 WHERE key = ?", (key,))
            else:
                connection.execute(
                    "INSERT OR REPLACE INTO account_rate_limits VALUES (?, ?, 1)", (key, now)
                )
        return True

    def issue_verification(self, username: str) -> str | None:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT email, email_verified_at, verification_required, verification_sent_at FROM accounts WHERE username = ?",
                (username,),
            ).fetchone()
            if row is None or not row[0] or row[1] is not None or not row[2]:
                return None
            if row[3] is not None and row[3] > now - RESEND_SECONDS:
                raise VerificationThrottled("Bitte warte eine Minute vor dem erneuten Versand.")
            connection.execute("DELETE FROM email_verifications WHERE expires_at <= ?", (now,))
            connection.execute(
                "INSERT INTO email_verifications VALUES (?, ?, ?)",
                (hashlib.sha256(token.encode()).hexdigest(), username, now + VERIFICATION_SECONDS),
            )
            connection.execute("UPDATE accounts SET verification_sent_at = ? WHERE username = ?", (now, username))
        return token

    def discard_verification(self, token: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM email_verifications WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))

    def verification_valid(self, token: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return False
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM email_verifications v JOIN accounts a ON a.username = v.username "
                "WHERE token_hash = ? AND expires_at > ? AND a.email_verified_at IS NULL AND a.verification_required = 1",
                (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
            ).fetchone() is not None

    def confirm_email(self, token: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return False
        now = int(time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT username FROM email_verifications WHERE token_hash = ? AND expires_at > ?",
                (hashlib.sha256(token.encode()).hexdigest(), now),
            ).fetchone()
            if row is None:
                return False
            updated = connection.execute(
                "UPDATE accounts SET email_verified_at = ? WHERE username = ? AND email_verified_at IS NULL AND verification_required = 1",
                (now, row[0]),
            ).rowcount
            connection.execute("DELETE FROM email_verifications WHERE username = ?", (row[0],))
        return bool(updated)

    def password_hash(self, username: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM accounts WHERE username = ?", (username,)
            ).fetchone()
        return str(row[0]) if row else None

    def exists(self, username: str) -> bool:
        return self.password_hash(username) is not None

    def claim(self, source_id: str, username: str) -> bool:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO source_owners(source_id, username, created_at) VALUES (?, ?, ?)",
                    (source_id, username, int(time.time())),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def release(self, source_id: str, username: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM source_owners WHERE source_id = ? AND username = ?",
                (source_id, username),
            )

    def release_any(self, source_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM source_owners WHERE source_id = ?", (source_id,)
            )

    def owner(self, source_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT username FROM source_owners WHERE source_id = ?", (source_id,)
            ).fetchone()
        return str(row[0]) if row else None

    def source_ids(self, username: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT source_id FROM source_owners WHERE username = ? ORDER BY created_at DESC",
                (username,),
            ).fetchall()
        return [str(row[0]) for row in rows]
