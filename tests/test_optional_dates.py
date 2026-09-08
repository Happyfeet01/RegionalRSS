import unittest
from xml.etree import ElementTree as ET

from app.discovery import analyze_page
from app.feed import build_rss
from app.models import FeedItem


class OptionalDateDiscoveryTest(unittest.TestCase):
    def test_generates_feed_for_kalbach_style_cards_without_dates(self) -> None:
        page = """
        <html><head><title>Gemeinde Kalbach – Aktuelles aus dem Rathaus</title></head><body>
          <main id="news-list">
            <div class="content-box col-md-6"><h2>Linie 52 - Haltestellen entfallen</h2><p>Alle Informationen erhalten Sie hier.</p><a href="/meldung/linie-52">mehr erfahren</a></div>
            <div class="content-box col-md-6"><h2>Neubaugebiet Bornhecke</h2><p>Alle Informationen zum Neubaugebiet erhalten Sie hier.</p><a href="/meldung/bornhecke">mehr erfahren</a></div>
            <div class="content-box col-md-6"><h2>Nächster Bauabschnitt</h2><p>Hier erhalten Sie weitere Informationen.</p><a href="/meldung/umfahrung">mehr erfahren</a></div>
          </main>
        </body></html>
        """
        result = analyze_page(
            page,
            "https://www.gemeinde-kalbach.de/gemeinde-und-rathaus/aktuelles-aus-dem-rathaus/",
        )
        self.assertFalse(result.native)
        self.assertEqual(3, len(result.items))
        self.assertNotIn("date", result.source.fields)
        self.assertIsNone(result.items[0].published)
        self.assertEqual("Linie 52 - Haltestellen entfallen", result.items[0].title)

        rss = ET.fromstring(build_rss(result.source, result.items))
        items = rss.findall("./channel/item")
        self.assertEqual(3, len(items))
        self.assertIsNone(items[0].find("pubDate"))

    def test_feed_item_roundtrip_keeps_missing_date(self) -> None:
        item = FeedItem(
            uid="1",
            title="Ohne Datum",
            uri="https://example.org/1",
            published=None,
        )
        restored = FeedItem.from_dict(item.to_dict())
        self.assertIsNone(restored.published)


if __name__ == "__main__":
    unittest.main()
