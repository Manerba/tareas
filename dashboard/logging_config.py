"""Security-Logging Konfiguration."""

import logging
from logging.handlers import RotatingFileHandler
import os

LOG_DIR = "/var/log"
LOG_FILE = os.path.join(LOG_DIR, "tareas.log")


def setup_security_logger():
    """Konfiguriert den Security-Logger mit RotatingFileHandler."""
    logger = logging.getLogger("tareas.security")
    logger.setLevel(logging.INFO)

    # Nicht doppelt konfigurieren
    if logger.handlers:
        return logger

    try:
        handler = RotatingFileHandler(
            LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5  # 10 MB, 5 Backups
        )
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except (PermissionError, OSError):
        # Falls /var/log nicht beschreibbar: auf stderr fallen
        logger.addHandler(logging.StreamHandler())
        logger.warning(
            "Security-Log konnte nicht nach %s geschrieben werden, verwende stderr",
            LOG_FILE,
        )

    return logger


def get_security_logger():
    """Gibt den Security-Logger zurueck."""
    return logging.getLogger("tareas.security")
