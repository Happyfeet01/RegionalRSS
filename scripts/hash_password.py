#!/usr/bin/env python3
from __future__ import annotations

import getpass

from app.auth import hash_password


def main() -> None:
    password = getpass.getpass("Admin-Passwort: ")
    confirmation = getpass.getpass("Admin-Passwort wiederholen: ")
    if password != confirmation:
        raise SystemExit("Die Passwörter stimmen nicht überein.")
    print(hash_password(password))


if __name__ == "__main__":
    main()
