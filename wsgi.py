import logging
import os

from app.application import create_application


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
application = create_application()
