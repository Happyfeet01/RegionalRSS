from __future__ import annotations

import re
import json
import unicodedata
from dataclasses import dataclass, replace
from urllib.parse import urljoin, urlparse

from lxml import etree, html

from .config import parse_source
from .extractor import extract_items
from .models import ExtractionError, FeedItem, SourceConfig


FEED_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/feed+json",
    "application/rdf+xml",
}
CLASS_TOKEN_RE = re.compile(r"(?:article|teaser|news|post|entry|card)", re.I)


@dataclass(frozen=True)
class DiscoveryResult:
    source: SourceConfig
    items: list[FeedItem]
    native: bool


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if len(slug) < 3:
        slug = f"feed-{slug or 'neu'}"
    return slug[:64].rstrip("-")


def _text(document: html.HtmlElement, xpath: str) -> str:
    value = " ".join(str(document.xpath(f"string({xpath})")).split())
    return value.strip()


def _metadata(document: html.HtmlElement, page_url: str) -> tuple[str, str, str]:
    parsed = urlparse(page_url)
    title = (
        document.xpath("string(//meta[@property='og:title'][1]/@content)").strip()
        or _text(document, "//title[1]")
        or parsed.hostname
        or "Neuer Feed"
    )
    description = (
        document.xpath("string(//meta[@name='description'][1]/@content)").strip()
        or f"Aktuelle Meldungen von {title}."
    )
    site_url = f"{parsed.scheme}://{parsed.netloc}/"
    return title[:160], description[:500], site_url


def discover_native_feed(page_html: str, page_url: str) -> str | None:
    try:
        document = html.fromstring(page_html, base_url=page_url)
    except (etree.ParserError, ValueError):
        return None

    candidates: list[str] = []
    for node in document.xpath("//link[@href] | //meta[@content]"):
        rel = (node.get("rel") or "").lower().split()
        media_type = (node.get("type") or "").lower().split(";", 1)[0].strip()
        name = (node.get("name") or "").lower()
        href = node.get("href") or node.get("content")
        if not href:
            continue
        if ("alternate" in rel and media_type in FEED_TYPES) or name in {
            "feed",
            "rss",
            "atom",
        }:
            candidate = urljoin(page_url, href)
            parsed = urlparse(candidate)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                candidates.append(candidate)
    if not candidates:
        return None
    page_host = (urlparse(page_url).hostname or "").lower()
    candidates.sort(
        key=lambda value: (
            (urlparse(value).hostname or "").lower() != page_host,
            "comment" in value.lower(),
            len(value),
        )
    )
    return candidates[0]


def validate_feed_document(payload: str) -> None:
    stripped = payload.lstrip()
    if stripped.startswith("{"):
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ExtractionError("Der angegebene JSON-Feed ist ungültig.") from exc
        if not isinstance(document, dict) or not str(document.get("version", "")).startswith(
            "https://jsonfeed.org/version/"
        ):
            raise ExtractionError("Die gefundene Datei ist kein gültiger JSON-Feed.")
        return
    try:
        root = etree.fromstring(payload.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        raise ExtractionError("Der gefundene Feed enthält ungültiges XML.") from exc
    local_name = etree.QName(root).localname.lower()
    if local_name not in {"rss", "feed", "rdf"}:
        raise ExtractionError("Die gefundene Datei ist kein RSS- oder Atom-Feed.")


def _class_xpath(tag: str, token: str) -> str:
    safe_token = token.replace("'", "")
    return (
        f"//{tag}[contains(concat(' ', normalize-space(@class), ' '), "
        f"' {safe_token} ')]"
    )


def _candidate_xpaths(document: html.HtmlElement) -> list[str]:
    candidates = ["//article"]
    seen = set(candidates)
    for element in document.xpath("//article[@class] | //li[@class] | //div[@class]"):
        for token in (element.get("class") or "").split():
            if not CLASS_TOKEN_RE.search(token):
                continue
            xpath = _class_xpath(element.tag, token)
            if xpath not in seen:
                seen.add(xpath)
                candidates.append(xpath)
    return candidates


def _candidate_score(elements: list[html.HtmlElement]) -> float:
    if len(elements) < 2:
        return -1
    useful = 0
    for element in elements[:30]:
        has_link = bool(element.xpath(".//a[@href]"))
        has_title = bool(element.xpath(".//*[self::h1 or self::h2 or self::h3 or self::h4]"))
        has_date = bool(
            element.xpath(
                ".//time | .//*[contains(translate(@class, 'DATE', 'date'), 'date')]"
            )
        )
        useful += int(has_link and has_title and has_date)
    ratio = useful / min(len(elements), 30)
    if useful < 2 or ratio < 0.55:
        return -1
    # Prefer a precise repeated component over a page-wide //article selector.
    return useful * 10 + ratio * 20 - min(len(elements), 100) * 0.02


def _detect_item_xpath(document: html.HtmlElement) -> str:
    best_xpath = ""
    best_score = -1.0
    for xpath in _candidate_xpaths(document):
        elements = document.xpath(xpath)
        score = _candidate_score(elements)
        if score > best_score:
            best_xpath, best_score = xpath, score
    if not best_xpath:
        raise ExtractionError(
            "Keine wiederholte Meldungsliste erkannt. Diese Seite benötigt Expertenregeln."
        )
    return best_xpath


def _image_attribute(elements: list[html.HtmlElement]) -> str:
    for attribute in ("data-src", "data-lazy-src", "src"):
        for element in elements[:10]:
            value = element.xpath(f"string(.//img[1]/@{attribute})").strip()
            if value and not value.startswith("data:"):
                return attribute
    return "src"


def analyze_page(page_html: str, page_url: str) -> DiscoveryResult:
    try:
        document = html.fromstring(page_html, base_url=page_url)
    except (etree.ParserError, ValueError) as exc:
        raise ExtractionError(f"Die Seite enthält kein auswertbares HTML: {exc}") from exc

    title, description, site_url = _metadata(document, page_url)
    source_id = _slug(f"{urlparse(page_url).hostname or 'feed'}-{title}")
    native_url = discover_native_feed(page_html, page_url)
    if native_url:
        source = parse_source(
            {
                "id": source_id,
                "name": title,
                "description": description,
                "site_url": site_url,
                "list_url": page_url,
                "source_type": "native",
                "native_feed_url": native_url,
            },
            "Automatische Erkennung",
        )
        return DiscoveryResult(source=source, items=[], native=True)

    item_xpath = _detect_item_xpath(document)
    elements = document.xpath(item_xpath)
    image_attribute = _image_attribute(elements)
    raw = {
        "id": source_id,
        "name": title,
        "description": description,
        "site_url": site_url,
        "list_url": page_url,
        "cache_seconds": 1800,
        "max_items": 30,
        "min_items": 2,
        "item_xpath": item_xpath,
        "fields": {
            "title": {"xpath": ".//*[self::h1 or self::h2 or self::h3 or self::h4][1]"},
            "link": {"xpath": ".//a[@href][1]", "attribute": "href"},
            "date": {
                "xpath": ".//time[1] | .//*[contains(translate(@class, 'DATE', 'date'), 'date')][1]",
                "attribute": "datetime",
            },
            "summary": {"xpath": ".//p[1]"},
            "image": {"xpath": ".//img[1]", "attribute": image_attribute},
        },
        "date_formats": ["iso8601", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"],
    }
    source = parse_source(raw, "Automatische Erkennung")
    items = extract_items(page_html, source)
    return DiscoveryResult(source=replace(source, min_items=min(2, len(items))), items=items, native=False)
