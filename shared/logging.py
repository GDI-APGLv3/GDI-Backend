"""
Configuración centralizada de logging con soporte para correlation_id.
"""

import logging
import os
import sys
from typing import Optional


class CorrelationIdFormatter(logging.Formatter):
    """
    Formatter que inyecta correlation_id en cada log.
    Usa try-except para evitar que errores en logging rompan la aplicación.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Formatea el log incluyendo correlation_id.

        Args:
            record: Log record a formatear

        Returns:
            str: Log formateado con correlation_id
        """
        try:
            # Import lazy para evitar circular imports
            from shared.context import get_correlation_id
            record.correlation_id = get_correlation_id()
        except Exception:
            # Si falla obtener correlation_id, usar "unknown"
            record.correlation_id = "unknown"

        return super().format(record)


def setup_logging(
    level: int = None,
    format_string: Optional[str] = None
) -> None:
    """
    Configura logging centralizado con correlation_id.

    IMPORTANTE: Llamar ANTES de importar módulos que usen logging.

    Args:
        level: Nivel de logging (default: INFO, configurable via LOG_LEVEL env var)
        format_string: Formato custom (opcional)
    """
    if level is None:
        level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)

    if format_string is None:
        format_string = (
            "%(asctime)s - [%(correlation_id)s] - [%(name)s] - "
            "%(levelname)s - %(message)s"
        )

    # Crear formatter con correlation_id
    formatter = CorrelationIdFormatter(format_string)

    # Configurar handler de consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # Configurar root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Limpiar handlers existentes para evitar duplicados
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)

    # Silenciar loggers ruidosos
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Obtiene un logger configurado.

    Args:
        name: Nombre del logger (típicamente __name__)

    Returns:
        Logger configurado
    """
    return logging.getLogger(name)
