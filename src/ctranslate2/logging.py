from __future__ import annotations

import logging

from ctranslate2 import _ext


_PYTHON_TO_CT2_LEVEL = {
    logging.CRITICAL: _ext.LogLevel.Critical,
    logging.ERROR: _ext.LogLevel.Error,
    logging.WARNING: _ext.LogLevel.Warning,
    logging.INFO: _ext.LogLevel.Info,
    logging.DEBUG: _ext.LogLevel.Debug,
    logging.NOTSET: _ext.LogLevel.Trace,
}

_CT2_TO_PYTHON_LEVEL = {value: key for key, value in _PYTHON_TO_CT2_LEVEL.items()}


def set_log_level(level: int) -> None:
    ct2_level = _PYTHON_TO_CT2_LEVEL.get(level)
    if ct2_level is None:
        raise ValueError(f"Level {level} is not a valid logging level")
    _ext.set_log_level(ct2_level)


def get_log_level() -> int:
    return _CT2_TO_PYTHON_LEVEL[_ext.get_log_level()]
