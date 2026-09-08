#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_source  # noqa: E402
from app.extractor import extract_items  # noqa: E402
from app.fetcher import fetch_html  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Quellenkonfiguration abrufen und die ersten RSS-Einträge anzeigen."
    )
    parser.add_argument("source", type=Path, help="Pfad zur YAML-Quellenkonfiguration")
    args = parser.parse_args()

    source = load_source(args.source)
    result = fetch_html(
        source.list_url,
        user_agent=os.getenv(
            "REGIONALRSS_USER_AGENT",
            "RegionalRSS/0.2 (source validation)",
        ),
    )
    if result.body is None:
        print("Die Quelle lieferte keinen HTML-Inhalt.", file=sys.stderr)
        return 1

    items = extract_items(result.body, source)
    print(f"{source.name}: {len(items)} gültige Einträge")
    for item in items[:5]:
        print(f"\n- {item.published.isoformat()} – {item.title}")
        print(f"  Artikel: {item.uri}")
        print(f"  Bild: {item.image_url or 'kein Bild'}")
    print("\nEs wurden keine Bilddateien heruntergeladen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
