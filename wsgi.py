import logging
import os

from app.webapp import create_web_application


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
application = create_web_application()
