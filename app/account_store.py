from __future__ import annotations

import sqlite3
import time
from pathlib import Path


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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    username TEXT PRIMARY KEY COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_owners (
                    source_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(username) REFERENCES accounts(username) ON DELETE CASCADE
                );
                """
            )

    def create(self, username: str, password_hash: str) -> bool:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO accounts(username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, password_hash, int(time.time())),
                )
            return True
        except sqlite3.IntegrityError:
            return False

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
