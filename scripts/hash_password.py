#!/usr/bin/env python3
from __future__ import annotations

import getpass
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.auth import hash_password  # noqa: E402


def main() -> None:
    password = getpass.getpass("Admin-Passwort: ")
    confirmation = getpass.getpass("Admin-Passwort wiederholen: ")
    if password != confirmation:
        raise SystemExit("Die Passwörter stimmen nicht überein.")
    print(hash_password(password))


if __name__ == "__main__":
    main()
