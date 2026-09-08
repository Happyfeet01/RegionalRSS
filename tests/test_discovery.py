import unittest

from app.discovery import analyze_page, discover_native_feed, validate_feed_document


class DiscoveryTest(unittest.TestCase):
    def test_discovers_declared_native_feed(self) -> None:
        page = """
        <html><head><title>Kappeln</title>
        <meta name="feed" content="/lokales/kappeln/rss">
        </head><body></body></html>
        """
        self.assertEqual(
            "https://www.shz.de/lokales/kappeln/rss",
            discover_native_feed(page, "https://www.shz.de/lokales/kappeln"),
        )
        result = analyze_page(page, "https://www.shz.de/lokales/kappeln")
        self.assertTrue(result.native)
        self.assertEqual("native", result.source.source_type)
        validate_feed_document("<rss version='2.0'><channel/></rss>")

    def test_generates_rules_for_repeated_news_cards(self) -> None:
        page = """
        <html><head><title>Gemeinde – Aktuelles</title></head><body>
          <article class="news-card"><a href="/meldung/eins"><h3><time datetime="2026-09-08T12:00:00+02:00">08.09.2026</time>Erste Meldung</h3><img data-src="/eins.jpg"><p>Text eins</p></a></article>
          <article class="news-card"><a href="/meldung/zwei"><h3><time datetime="2026-09-07T12:00:00+02:00">07.09.2026</time>Zweite Meldung</h3><img data-src="/zwei.jpg"><p>Text zwei</p></a></article>
        </body></html>
        """
        result = analyze_page(page, "https://gemeinde.example/aktuelles/")
        self.assertFalse(result.native)
        self.assertEqual(2, len(result.items))
        self.assertEqual("Erste Meldung", result.items[0].title)
        self.assertEqual("https://gemeinde.example/eins.jpg", result.items[0].image_url)


if __name__ == "__main__":
    unittest.main()
