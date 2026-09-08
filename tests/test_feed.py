from datetime import UTC, datetime
import html
from pathlib import Path
import unittest
from xml.etree import ElementTree as ET

from app.config import load_source
from app.extractor import extract_items
from app.feed import CONTENT_NS, MEDIA_NS, build_rss


ROOT = Path(__file__).resolve().parents[1]


class FeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = load_source(ROOT / "sources" / "flieden-aktuelles.yml")
        page = (ROOT / "tests" / "fixtures" / "flieden.html").read_text(
            encoding="utf-8"
        )
        self.items = extract_items(page, self.source)

    def test_rss_keeps_original_image_url(self) -> None:
        payload = build_rss(
            self.source,
            self.items,
            self_url="https://feeds.example.de/feeds/flieden-aktuelles.xml",
            generated_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        )
        root = ET.fromstring(payload)
        first = root.find("./channel/item")
        self.assertIsNotNone(first)
        media = first.find(f"{{{MEDIA_NS}}}content")
        self.assertIsNotNone(media)
        self.assertEqual(self.items[0].image_url, media.attrib["url"])
        encoded = first.find(f"{{{CONTENT_NS}}}encoded")
        self.assertIn(self.items[0].image_url, html.unescape(encoded.text))

    def test_feed_is_rss_2_and_contains_no_binary_image(self) -> None:
        payload = build_rss(self.source, self.items)
        root = ET.fromstring(payload)
        self.assertEqual("2.0", root.attrib["version"])
        self.assertNotIn(b"JFIF", payload)
        self.assertNotIn(b"PNG", payload)


if __name__ == "__main__":
    unittest.main()
