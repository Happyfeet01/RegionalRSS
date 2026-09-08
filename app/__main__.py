from __future__ import annotations

import logging
import os
from wsgiref.simple_server import make_server

from .application import create_application


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8080"))
    application = create_application()
    with make_server(host, port, application) as server:
        print(f"RegionalRSS listening on http://{host}:{port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
