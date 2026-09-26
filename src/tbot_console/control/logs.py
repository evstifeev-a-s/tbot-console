from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
ROTATE_BYTES = 20_000_000
ROTATE_BACKUPS = 5


def configure_logging(log_path: str | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    if not log_path:
        return
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    resolved = path.resolve()
    if any(
        isinstance(h, RotatingFileHandler) and Path(h.baseFilename).resolve() == resolved
        for h in root.handlers
    ):
        return
    handler = RotatingFileHandler(
        path, maxBytes=ROTATE_BYTES, backupCount=ROTATE_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
