from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, HttpUrl

from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.subtitle import SubtitleStyle


class AvatarEngine(str, Enum):
    tuilionnx = "tuilionnx"


class AvatarRequest(BaseModel):
    audio_path: str
    model_id: str = "uploaded-avatar"
    engine: AvatarEngine = AvatarEngine.tuilionnx
    workspace: str | None = None
    subtitle_path: str | None = None
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    reference_video_path: str | None = None
    background_video_path: str | None = None
    overlay_text: str | None = None
    batch_size: int = 4
    sync_offset: float = 0.0
    scale_h: float = 1.6
    scale_w: float = 3.6
    compress_inference: bool = False
    beautify_teeth: bool = False
    add_ai_watermark: bool = False
    quality_preset: str = "clear"
    max_reference_edge: int | None = 1080


class AvatarResult(BaseModel):
    success: bool
    video_path: str | None = None
    duration_sec: float | None = None
    elapsed_sec: float | None = None
    download_url: HttpUrl | None = None
    engine_used: str | None = None
    model_asset_path: str | None = None
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
