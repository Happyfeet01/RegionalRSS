from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .cache import FeedCache
from .extractor import extract_items
from .fetcher import FetchError, fetch_html
from .models import ExtractionError, FeedItem, SourceConfig, SourceUnavailable


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceResult:
    items: list[FeedItem]
    fetched_at: int
    stale: bool


class SourceService:
    def __init__(
        self,
        cache: FeedCache,
        *,
        user_agent: str,
        timeout_seconds: int,
        max_response_bytes: int,
        allow_private_hosts: bool = False,
    ) -> None:
        self.cache = cache
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.allow_private_hosts = allow_private_hosts

    def get_items(self, source: SourceConfig) -> ServiceResult:
        now = int(time.time())
        cached = self.cache.get(source.source_id)
        if cached and cached.expires_at > now:
            return ServiceResult(cached.items, cached.fetched_at, stale=False)

        try:
            response = fetch_html(
                source.list_url,
                user_agent=self.user_agent,
                timeout_seconds=self.timeout_seconds,
                max_response_bytes=self.max_response_bytes,
                etag=cached.etag if cached else None,
                last_modified=cached.last_modified if cached else None,
                allow_private_hosts=self.allow_private_hosts,
            )
            expires_at = now + source.cache_seconds
            if response.status == 304:
                if cached is None:
                    raise FetchError("Source returned 304 but no cached feed exists")
                self.cache.touch(
                    source.source_id,
                    fetched_at=now,
                    expires_at=expires_at,
                    etag=response.etag,
                    last_modified=response.last_modified,
                )
                return ServiceResult(cached.items, now, stale=False)

            if response.body is None:
                raise FetchError("Source returned an empty response")
            items = extract_items(response.body, source)
            self.cache.put(
                source.source_id,
                fetched_at=now,
                expires_at=expires_at,
                etag=response.etag,
                last_modified=response.last_modified,
                items=items,
            )
            return ServiceResult(items, now, stale=False)
        except (FetchError, ExtractionError) as exc:
            if cached is not None:
                LOGGER.warning(
                    "Serving stale cache for source %s after refresh failure: %s",
                    source.source_id,
                    exc,
                )
                return ServiceResult(cached.items, cached.fetched_at, stale=True)
            raise SourceUnavailable(
                f"Source '{source.source_id}' is temporarily unavailable"
            ) from exc
