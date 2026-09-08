from pathlib import Path
import unittest

from app.config import load_source
from app.extractor import extract_items


ROOT = Path(__file__).resolve().parents[1]


class ExtractorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = load_source(ROOT / "sources" / "flieden-aktuelles.yml")
        self.page = (ROOT / "tests" / "fixtures" / "flieden.html").read_text(
            encoding="utf-8"
        )

    def test_extracts_current_flieden_structure(self) -> None:
        items = extract_items(self.page, self.source)

        self.assertEqual(3, len(items))
        self.assertEqual("Neue Meldung aus Flieden", items[0].title)
        self.assertEqual(
            "https://www.flieden.de/rathaus-politik/aktuelles/2026/september/meldung-eins/",
            items[0].uri,
        )
        self.assertEqual(
            "https://www.flieden.de/bilder/beispiel-eins.jpg?cid=abc&resize=130x130c",
            items[0].image_url,
        )
        self.assertEqual(2 * 60 * 60, int(items[0].published.utcoffset().total_seconds()))

    def test_items_are_sorted_newest_first(self) -> None:
        items = extract_items(self.page, self.source)
        self.assertEqual(
            ["Neue Meldung aus Flieden", "Zweite Meldung mit Umlauten: Rückers", "Dritte Meldung"],
            [item.title for item in items],
        )


if __name__ == "__main__":
    unittest.main()
