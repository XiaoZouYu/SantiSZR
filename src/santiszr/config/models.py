from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class WindowSettings(BaseModel):
    title: str = "SantiSZR"
    width: int = 1280
    height: int = 800
    min_width: int = 1100
    min_height: int = 720


class RuntimePathSettings(BaseModel):
    data_dir: Path | None = None
    cache_dir: Path | None = None
    log_dir: Path | None = None


class AppSettingsModel(BaseModel):
    app_name: str = "SantiSZR"
    app_env: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    paths: RuntimePathSettings = Field(default_factory=RuntimePathSettings)
    main_window: WindowSettings = Field(default_factory=WindowSettings)
