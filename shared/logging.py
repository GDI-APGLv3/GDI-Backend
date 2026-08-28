
import logging
import os
import sys
from typing import Optional


class CorrelationIdFormatter(logging.Formatter):

    def format(self, record: logging.LogRecord) -> str:
        try:
            from shared.context import get_correlation_id
            record.correlation_id = get_correlation_id()
        except Exception:
            record.correlation_id = "unknown"

        return super().format(record)


def setup_logging(
    level: int = None,
    format_string: Optional[str] = None
) -> None:
    if level is None:
        level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)

    if format_string is None:
        format_string = (
            "%(asctime)s - [%(correlation_id)s] - [%(name)s] - "
            "%(levelname)s - %(message)s"
        )

    formatter = CorrelationIdFormatter(format_string)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
