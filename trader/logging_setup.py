from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(level: str = "INFO", to_file: bool = True, filename: str = "data/logs/app.log") -> None:
    """
    Minimal logging setup:
    - Console output always
    - Optional file output
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if to_file:
        log_path = Path(filename)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
    )

    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
