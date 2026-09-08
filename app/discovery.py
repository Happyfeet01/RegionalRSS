from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
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
HEADING_XPATH = ".//*[self::h1 or self::h2 or self::h3 or self::h4]"
DATE_CLASS_XPATH = (
    ".//*[contains(translate(@class, "
    "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'date') or "
    "contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'datum') or "
    "contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'time')]"
)


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


def _exact_class_xpath(tag: str, classes: str) -> str | None:
    classes = " ".join(classes.split())
    if not classes or "'" in classes:
        return None
    return f"//{tag}[normalize-space(@class)='{classes}']"


def _usable_container(element: html.HtmlElement) -> bool:
    return bool(element.xpath(".//a[@href]")) and bool(element.xpath(HEADING_XPATH))


def _candidate_xpaths(document: html.HtmlElement) -> list[str]:
    candidates = ["//article"]
    seen = set(candidates)
    qualified = [
        element
        for element in document.xpath("//article | //li | //div | //section")
        if isinstance(element, html.HtmlElement) and _usable_container(element)
    ]

    exact_counts = Counter(
        (element.tag, " ".join((element.get("class") or "").split()))
        for element in qualified
        if element.get("class")
    )
    token_counts = Counter(
        (element.tag, token)
        for element in qualified
        for token in (element.get("class") or "").split()
        if len(token) >= 3
    )

    # Prefer a complete repeated class signature. This catches municipal CMS
    # cards whose classes are generic instead of containing words like "news".
    for (tag, classes), count in exact_counts.items():
        if count < 2:
            continue
        xpath = _exact_class_xpath(tag, classes)
        if xpath and xpath not in seen:
            seen.add(xpath)
            candidates.append(xpath)

    # Keep the established news/card heuristics and also allow generic class
    # tokens when at least two useful heading/link containers use them.
    for element in qualified:
        for token in (element.get("class") or "").split():
            if not CLASS_TOKEN_RE.search(token) and token_counts[(element.tag, token)] < 2:
                continue
            xpath = _class_xpath(element.tag, token)
            if xpath not in seen:
                seen.add(xpath)
                candidates.append(xpath)

    # Last fallback: repeated direct siblings below an identifiable parent.
    # This helps older municipal layouts without meaningful card class names.
    for parent in document.xpath("//*[@id or @class]"):
        child_groups: dict[str, list[html.HtmlElement]] = {}
        for child in parent:
            if (
                isinstance(child, html.HtmlElement)
                and child.tag in {"article", "li", "div", "section"}
                and _usable_container(child)
            ):
                child_groups.setdefault(child.tag, []).append(child)
        for child_tag, children in child_groups.items():
            if len(children) < 2:
                continue
            parent_xpath = None
            parent_id = (parent.get("id") or "").strip()
            if parent_id and "'" not in parent_id:
                parent_xpath = f"//*[@id='{parent_id}']"
            elif parent.get("class"):
                parent_xpath = _exact_class_xpath(parent.tag, parent.get("class") or "")
            if parent_xpath:
                xpath = f"{parent_xpath}/{child_tag}"
                if xpath not in seen:
                    seen.add(xpath)
                    candidates.append(xpath)
    return candidates


def _candidate_score(elements: list[html.HtmlElement]) -> float:
    if len(elements) < 2:
        return -1
    sample = elements[:30]
    useful = 0
    single_heading = 0
    dated = 0
    descriptive = 0
    images = 0
    for element in sample:
        links = element.xpath(".//a[@href]")
        headings = element.xpath(HEADING_XPATH)
        if links and headings:
            useful += 1
        if len(headings) == 1:
            single_heading += 1
        if element.xpath(".//time | " + DATE_CLASS_XPATH):
            dated += 1
        if element.xpath(".//p"):
            descriptive += 1
        if element.xpath(".//img"):
            images += 1
    ratio = useful / len(sample)
    if useful < 2 or ratio < 0.60:
        return -1

    # A clean repeated card normally contains one headline and one useful link.
    # Dates are a bonus, not a requirement: many municipal pages simply omit them.
    return (
        useful * 10
        + ratio * 20
        + single_heading * 3
        + dated * 2
        + descriptive * 0.5
        + images * 0.25
        - min(len(elements), 100) * 0.02
    )


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


def _detect_date_rule(elements: list[html.HtmlElement]) -> dict[str, str] | None:
    sample = elements[:10]
    if not sample:
        return None
    rules = (
        (".//time[1]", "datetime"),
        (DATE_CLASS_XPATH + "[1]", ""),
    )
    minimum = min(2, len(sample))
    for xpath, attribute in rules:
        matches = 0
        for element in sample:
            try:
                if element.xpath(xpath):
                    matches += 1
            except etree.XPathError:
                break
        if matches >= minimum:
            rule = {"xpath": xpath}
            if attribute:
                rule["attribute"] = attribute
            return rule
    return None


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
    fields: dict[str, dict[str, str]] = {
        "title": {"xpath": ".//*[self::h1 or self::h2 or self::h3 or self::h4][1]"},
        "link": {"xpath": ".//a[@href][1]", "attribute": "href"},
        "summary": {"xpath": ".//p[1]"},
        "image": {"xpath": ".//img[1]", "attribute": image_attribute},
    }
    date_rule = _detect_date_rule(elements)
    if date_rule:
        fields["date"] = date_rule

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
        "fields": fields,
        "date_formats": ["iso8601", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"],
    }
    source = parse_source(raw, "Automatische Erkennung")
    items = extract_items(page_html, source)
    return DiscoveryResult(source=replace(source, min_items=min(2, len(items))), items=items, native=False)
