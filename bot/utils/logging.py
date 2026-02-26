import logging
import time
from logging.config import dictConfig

from pythonjsonlogger import jsonlogger


class UTCJsonFormatter(jsonlogger.JsonFormatter):
    converter = time.gmtime


def configure_logging(log_level: str) -> None:
    level = log_level.upper()

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": UTCJsonFormatter,
                    "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "level": level,
                }
            },
            "root": {"handlers": ["console"], "level": level},
            "loggers": {
                "aiogram": {"level": level, "propagate": True},
                "motor": {"level": "WARNING", "propagate": True},
            },
        }
    )

    logging.getLogger(__name__).info("Logging configured", extra={"log_level": level})
