from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .models import FeedItem


@dataclass(frozen=True)
class CacheRecord:
    source_id: str
    fetched_at: int
    expires_at: int
    etag: str | None
    last_modified: str | None
    items: list[FeedItem]


class FeedCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feed_cache (
                    source_id TEXT PRIMARY KEY,
                    fetched_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    etag TEXT,
                    last_modified TEXT,
                    items_json TEXT NOT NULL
                )
                """
            )

    def get(self, source_id: str) -> CacheRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_id, fetched_at, expires_at, etag, last_modified, items_json
                FROM feed_cache WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        return CacheRecord(
            source_id=row[0],
            fetched_at=int(row[1]),
            expires_at=int(row[2]),
            etag=row[3],
            last_modified=row[4],
            items=[FeedItem.from_dict(item) for item in json.loads(row[5])],
        )

    def put(
        self,
        source_id: str,
        *,
        fetched_at: int,
        expires_at: int,
        etag: str | None,
        last_modified: str | None,
        items: list[FeedItem],
    ) -> None:
        encoded_items = json.dumps(
            [item.to_dict() for item in items], ensure_ascii=False, separators=(",", ":")
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO feed_cache
                    (source_id, fetched_at, expires_at, etag, last_modified, items_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at,
                    etag = excluded.etag,
                    last_modified = excluded.last_modified,
                    items_json = excluded.items_json
                """,
                (
                    source_id,
                    fetched_at,
                    expires_at,
                    etag,
                    last_modified,
                    encoded_items,
                ),
            )

    def touch(
        self,
        source_id: str,
        *,
        fetched_at: int,
        expires_at: int,
        etag: str | None,
        last_modified: str | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE feed_cache
                SET fetched_at = ?, expires_at = ?,
                    etag = COALESCE(?, etag),
                    last_modified = COALESCE(?, last_modified)
                WHERE source_id = ?
                """,
                (fetched_at, expires_at, etag, last_modified, source_id),
            )
