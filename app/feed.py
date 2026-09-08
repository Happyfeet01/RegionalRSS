from __future__ import annotations

import html as html_module
from datetime import UTC, datetime
from email.utils import format_datetime
from xml.etree import ElementTree as ET

from .models import FeedItem, SourceConfig


ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
MEDIA_NS = "http://search.yahoo.com/mrss/"

ET.register_namespace("atom", ATOM_NS)
ET.register_namespace("content", CONTENT_NS)
ET.register_namespace("media", MEDIA_NS)


def _rfc2822(value: datetime) -> str:
    return format_datetime(value.astimezone(UTC), usegmt=True)


def _item_html(item: FeedItem, source: SourceConfig) -> str:
    parts: list[str] = []
    if item.image_url:
        image_url = html_module.escape(item.image_url, quote=True)
        parts.append(
            f'<figure><img src="{image_url}" alt="" loading="lazy"></figure>'
        )
    if item.summary:
        parts.append(f"<p>{html_module.escape(item.summary)}</p>")
    link = html_module.escape(item.uri, quote=True)
    source_name = html_module.escape(source.name)
    parts.append(f'<p><a href="{link}">Originalmeldung bei {source_name} öffnen</a></p>')
    return "".join(parts)


def build_rss(
    source: SourceConfig,
    items: list[FeedItem],
    *,
    self_url: str | None = None,
    generated_at: datetime | None = None,
) -> bytes:
    generated_at = generated_at or datetime.now(UTC)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = source.name
    ET.SubElement(channel, "link").text = source.site_url
    ET.SubElement(channel, "description").text = source.description
    ET.SubElement(channel, "language").text = source.language
    ET.SubElement(channel, "lastBuildDate").text = _rfc2822(generated_at)
    ET.SubElement(channel, "generator").text = "RegionalRSS"
    if self_url:
        ET.SubElement(
            channel,
            f"{{{ATOM_NS}}}link",
            {
                "href": self_url,
                "rel": "self",
                "type": "application/rss+xml",
            },
        )

    for item in items:
        item_node = ET.SubElement(channel, "item")
        ET.SubElement(item_node, "title").text = item.title
        ET.SubElement(item_node, "link").text = item.uri
        ET.SubElement(item_node, "guid", {"isPermaLink": "true"}).text = item.uri
        if item.published is not None:
            ET.SubElement(item_node, "pubDate").text = _rfc2822(item.published)
        if item.summary:
            ET.SubElement(item_node, "description").text = item.summary
        ET.SubElement(item_node, f"{{{CONTENT_NS}}}encoded").text = _item_html(
            item, source
        )
        ET.SubElement(item_node, "source", {"url": source.site_url}).text = source.name
        for category in item.categories:
            ET.SubElement(item_node, "category").text = category
        if item.image_url:
            ET.SubElement(
                item_node,
                f"{{{MEDIA_NS}}}content",
                {"url": item.image_url, "medium": "image"},
            )
            ET.SubElement(
                item_node,
                f"{{{MEDIA_NS}}}thumbnail",
                {"url": item.image_url},
            )

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)
