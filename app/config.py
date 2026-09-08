from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .models import ConfigurationError, FieldRule, SourceConfig


SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
REQUIRED_FIELDS = ("title", "link")
OPTIONAL_FIELDS = ("date", "summary", "image")


def _require_text(data: dict, key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{context}: '{key}' must be a non-empty string")
    return value.strip()


def _validate_http_url(value: str, context: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError(f"{context}: only absolute HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise ConfigurationError(f"{context}: credentials in URLs are not allowed")
    return value


def _parse_rule(data: object, context: str, *, required: bool) -> FieldRule:
    if not isinstance(data, dict):
        raise ConfigurationError(f"{context}: field rule must be a mapping")
    xpath = _require_text(data, "xpath", context)
    attribute = data.get("attribute")
    if attribute is not None and (not isinstance(attribute, str) or not attribute.strip()):
        raise ConfigurationError(f"{context}: attribute must be a non-empty string")
    return FieldRule(xpath=xpath, attribute=attribute, required=required)


def parse_source(raw: object, context: str = "source") -> SourceConfig:
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{context}: source file must contain a mapping")

    source_id = _require_text(raw, "id", context)
    if not SOURCE_ID_RE.fullmatch(source_id):
        raise ConfigurationError(
            f"{context}: id must contain 3-64 lowercase letters, digits or hyphens"
        )

    source_type = str(raw.get("source_type", "scrape")).strip()
    if source_type not in {"scrape", "native"}:
        raise ConfigurationError(f"{context}: source_type must be 'scrape' or 'native'")
    native_feed_url = raw.get("native_feed_url")
    if source_type == "native":
        if not isinstance(native_feed_url, str) or not native_feed_url.strip():
            raise ConfigurationError(f"{context}: native_feed_url is required")
        native_feed_url = _validate_http_url(native_feed_url.strip(), context)

    fields_raw = raw.get("fields", {})
    if not isinstance(fields_raw, dict):
        raise ConfigurationError(f"{context}: 'fields' must be a mapping")

    fields: dict[str, FieldRule] = {}
    for name in REQUIRED_FIELDS:
        if source_type == "scrape":
            if name not in fields_raw:
                raise ConfigurationError(f"{context}: required field '{name}' is missing")
            fields[name] = _parse_rule(
                fields_raw[name], f"{context}: fields.{name}", required=True
            )
    for name in OPTIONAL_FIELDS:
        if name in fields_raw:
            fields[name] = _parse_rule(
                fields_raw[name], f"{context}: fields.{name}", required=False
            )

    cache_seconds = int(raw.get("cache_seconds", 900))
    max_items = int(raw.get("max_items", 20))
    min_items = int(raw.get("min_items", 1))
    if not 60 <= cache_seconds <= 86400:
        raise ConfigurationError(f"{context}: cache_seconds must be between 60 and 86400")
    if not 1 <= max_items <= 100:
        raise ConfigurationError(f"{context}: max_items must be between 1 and 100")
    if not 1 <= min_items <= max_items:
        raise ConfigurationError(f"{context}: min_items must be between 1 and max_items")

    date_formats_raw = raw.get("date_formats", ["iso8601"])
    if not isinstance(date_formats_raw, list) or not all(
        isinstance(value, str) and value for value in date_formats_raw
    ):
        raise ConfigurationError(f"{context}: date_formats must be a list of strings")

    categories_raw = raw.get("categories", [])
    if not isinstance(categories_raw, list) or not all(
        isinstance(value, str) and value.strip() for value in categories_raw
    ):
        raise ConfigurationError(f"{context}: categories must be a list of strings")

    return SourceConfig(
        source_id=source_id,
        name=_require_text(raw, "name", context),
        description=_require_text(raw, "description", context),
        site_url=_validate_http_url(_require_text(raw, "site_url", context), context),
        list_url=_validate_http_url(_require_text(raw, "list_url", context), context),
        language=str(raw.get("language", "de-DE")),
        timezone=str(raw.get("timezone", "Europe/Berlin")),
        cache_seconds=cache_seconds,
        max_items=max_items,
        min_items=min_items,
        item_xpath=(
            _require_text(raw, "item_xpath", context)
            if source_type == "scrape"
            else ""
        ),
        fields=fields,
        date_formats=tuple(date_formats_raw),
        categories=tuple(value.strip() for value in categories_raw),
        source_type=source_type,
        native_feed_url=native_feed_url,
    )


def load_source(path: Path) -> SourceConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Unable to read {path}: {exc}") from exc
    return parse_source(raw, str(path))


def source_to_mapping(source: SourceConfig) -> dict[str, Any]:
    fields: dict[str, dict[str, str]] = {}
    for name, rule in source.fields.items():
        fields[name] = {"xpath": rule.xpath}
        if rule.attribute:
            fields[name]["attribute"] = rule.attribute
    mapping = {
        "id": source.source_id,
        "name": source.name,
        "description": source.description,
        "site_url": source.site_url,
        "list_url": source.list_url,
        "language": source.language,
        "timezone": source.timezone,
        "cache_seconds": source.cache_seconds,
        "max_items": source.max_items,
        "min_items": source.min_items,
        "item_xpath": source.item_xpath,
        "fields": fields,
        "date_formats": list(source.date_formats),
        "categories": list(source.categories),
    }
    if source.source_type == "native":
        mapping["source_type"] = "native"
        mapping["native_feed_url"] = source.native_feed_url
    return mapping


def load_sources(directory: Path) -> dict[str, SourceConfig]:
    if not directory.is_dir():
        raise ConfigurationError(f"Source directory does not exist: {directory}")

    sources: dict[str, SourceConfig] = {}
    paths = sorted((*directory.glob("*.yml"), *directory.glob("*.yaml")))
    for path in paths:
        source = load_source(path)
        if source.source_id in sources:
            raise ConfigurationError(f"Duplicate source id: {source.source_id}")
        sources[source.source_id] = source

    if not sources:
        raise ConfigurationError(f"No source definitions found in {directory}")
    return sources
