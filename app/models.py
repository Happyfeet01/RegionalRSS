from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


class ConfigurationError(ValueError):
    """Raised when a source definition is missing or unsafe."""


class ExtractionError(RuntimeError):
    """Raised when a page no longer matches its source definition."""


class SourceUnavailable(RuntimeError):
    """Raised when no current or cached source data can be served."""


@dataclass(frozen=True)
class FieldRule:
    xpath: str
    attribute: str | None = None
    required: bool = True


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    name: str
    description: str
    site_url: str
    list_url: str
    language: str
    timezone: str
    cache_seconds: int
    max_items: int
    min_items: int
    item_xpath: str
    fields: dict[str, FieldRule]
    date_formats: tuple[str, ...]
    categories: tuple[str, ...]
    source_type: str = "scrape"
    native_feed_url: str | None = None


@dataclass(frozen=True)
class FeedItem:
    uid: str
    title: str
    uri: str
    published: datetime
    summary: str | None = None
    image_url: str | None = None
    categories: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["published"] = self.published.isoformat()
        data["categories"] = list(self.categories)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeedItem":
        return cls(
            uid=str(data["uid"]),
            title=str(data["title"]),
            uri=str(data["uri"]),
            published=datetime.fromisoformat(str(data["published"])),
            summary=data.get("summary"),
            image_url=data.get("image_url"),
            categories=tuple(data.get("categories", [])),
        )
