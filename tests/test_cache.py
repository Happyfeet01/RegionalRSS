from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.cache import FeedCache
from app.config import load_source
from app.extractor import extract_items


ROOT = Path(__file__).resolve().parents[1]


class CacheTest(unittest.TestCase):
    def test_round_trip_preserves_image_url(self) -> None:
        source = load_source(ROOT / "sources" / "flieden-aktuelles.yml")
        page = (ROOT / "tests" / "fixtures" / "flieden.html").read_text(
            encoding="utf-8"
        )
        items = extract_items(page, source)
        with TemporaryDirectory() as directory:
            cache = FeedCache(Path(directory) / "cache.sqlite3")
            cache.put(
                source.source_id,
                fetched_at=100,
                expires_at=200,
                etag='"example"',
                last_modified=None,
                items=items,
            )
            loaded = cache.get(source.source_id)

        self.assertIsNotNone(loaded)
        self.assertEqual(items[0].image_url, loaded.items[0].image_url)
        self.assertEqual(items[0].published, loaded.items[0].published)


if __name__ == "__main__":
    unittest.main()
