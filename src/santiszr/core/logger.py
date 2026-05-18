from __future__ import annotations

import logging
from pathlib import Path

from santiszr.config.settings import AppSettings
from santiszr.core.paths import resolve_runtime_paths


_CONFIGURED = False


def configure_logging(settings: AppSettings) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    runtime_paths = resolve_runtime_paths(settings)
    log_file = Path(runtime_paths.logs) / "santiszr.log"

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
