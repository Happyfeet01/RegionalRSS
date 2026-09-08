from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lxml import etree, html

from .models import ExtractionError, FeedItem, FieldRule, SourceConfig


WHITESPACE_RE = re.compile(r"\s+")
LEADING_DATE_RE = re.compile(r"^\s*\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\s*")


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = WHITESPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()
    return cleaned or None


def _extract_value(element: html.HtmlElement, rule: FieldRule, field_name: str) -> str | None:
    try:
        matches = element.xpath(rule.xpath)
    except etree.XPathError as exc:
        raise ExtractionError(f"Invalid XPath for field '{field_name}': {exc}") from exc

    if not matches:
        if rule.required:
            raise ExtractionError(f"Required field '{field_name}' was not found")
        return None

    match = matches[0]
    if isinstance(match, etree._Element):
        if rule.attribute:
            value = match.get(rule.attribute)
            # Dates are often text on older municipal sites even when newer cards
            # use a machine-readable attribute.
            if value is None and field_name == "date":
                value = match.text_content()
        else:
            value = match.text_content()
    else:
        value = str(match)

    value = _clean_text(value)
    if not value and rule.required:
        raise ExtractionError(f"Required field '{field_name}' is empty")
    return value


def _absolute_http_url(value: str | None, base_url: str, field_name: str) -> str | None:
    if not value:
        return None
    absolute = urljoin(base_url, value)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ExtractionError(f"Field '{field_name}' does not contain an HTTP(S) URL")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ExtractionError(f"Field '{field_name}' points to a local host")
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and not literal_ip.is_global:
        raise ExtractionError(f"Field '{field_name}' points to a non-public address")
    return absolute


def _parse_date(value: str, source: SourceConfig) -> datetime:
    parsed: datetime | None = None
    for date_format in source.date_formats:
        try:
            if date_format == "iso8601":
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                parsed = datetime.strptime(value, date_format)
        except ValueError:
            continue
        if parsed is not None:
            break

    if parsed is None:
        raise ExtractionError(f"Unable to parse publication date: {value!r}")

    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(source.timezone))
        except ZoneInfoNotFoundError as exc:
            raise ExtractionError(f"Unknown timezone: {source.timezone}") from exc
    return parsed


def extract_items(page_html: str, source: SourceConfig) -> list[FeedItem]:
    """Extract feed metadata only. Referenced images are never downloaded."""

    try:
        document = html.fromstring(page_html, base_url=source.list_url)
        elements = document.xpath(source.item_xpath)
    except (etree.ParserError, etree.XPathError) as exc:
        raise ExtractionError(f"Unable to parse source page: {exc}") from exc

    if not elements:
        raise ExtractionError("No item elements matched; the source layout may have changed")

    items: list[FeedItem] = []
    seen_urls: set[str] = set()
    for element in elements:
        if not isinstance(element, etree._Element):
            continue
        try:
            title = _extract_value(element, source.fields["title"], "title")
            link = _extract_value(element, source.fields["link"], "link")
            date_value = (
                _extract_value(element, source.fields["date"], "date")
                if "date" in source.fields
                else None
            )
            summary = (
                _extract_value(element, source.fields["summary"], "summary")
                if "summary" in source.fields
                else None
            )
            image = (
                _extract_value(element, source.fields["image"], "image")
                if "image" in source.fields
                else None
            )
        except ExtractionError:
            # A single malformed card must not break an otherwise healthy feed.
            continue

        assert title is not None and link is not None
        uri = _absolute_http_url(link, source.list_url, "link")
        try:
            image_url = _absolute_http_url(image, source.list_url, "image")
        except ExtractionError:
            # A broken or unsafe image must not remove an otherwise valid article.
            image_url = None
        assert uri is not None
        if uri in seen_urls:
            continue

        published: datetime | None = None
        if date_value:
            try:
                published = _parse_date(date_value, source)
            except ExtractionError:
                # Dates are useful metadata, but an otherwise valid article should
                # not disappear merely because a site omits or changes its date.
                published = None
        # Some CMS place the visible date inside the headline element. Keep the
        # generated item title clean while retaining the machine-readable date.
        title = LEADING_DATE_RE.sub("", title).strip() or title
        seen_urls.add(uri)

        items.append(
            FeedItem(
                uid=hashlib.sha256(uri.encode("utf-8")).hexdigest(),
                title=title,
                uri=uri,
                published=published,
                summary=summary,
                image_url=image_url,
                categories=source.categories,
            )
        )
        if len(items) >= source.max_items:
            break

    if len(items) < source.min_items:
        raise ExtractionError(
            f"Only {len(items)} valid item(s) found; expected at least {source.min_items}"
        )

    fallback = datetime.min.replace(tzinfo=UTC)
    items.sort(key=lambda item: item.published or fallback, reverse=True)
    return items
