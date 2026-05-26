from __future__ import annotations

from pydantic import BaseModel, Field

from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.subtitle import SubtitleStyle


class BGMSelection(BaseModel):
    bgm_path: str | None = None
    bgm_directory: str | None = None
    bgm_name: str | None = None
    random_choice: bool = False
    volume: float = Field(default=0.2, ge=0.0, le=1.0)


class CoverStyle(BaseModel):
    font_name: str = "Microsoft YaHei"
    font_size: int = 64
    font_color: str = "#FFFFFF"
    highlight_color: str = "#F59E0B"
    position: str = "bottom"


class CoverRequest(BaseModel):
    enabled: bool = False
    source_video_path: str | None = None
    output_name: str | None = None
    timestamp_sec: float | None = None
    title: str = ""
    highlight_text: str = ""
    style: CoverStyle = Field(default_factory=CoverStyle)


class PictureInPictureRequest(BaseModel):
    enabled: bool = False
    source_path: str | None = None
    start_sec: float = Field(default=0.0, ge=0.0)
    end_sec: float | None = Field(default=None, ge=0.0)
    template: str = "corner"
    position: str = "top_right"
    scale: float = Field(default=0.18, ge=0.1, le=0.6)
    border_width: int = Field(default=0, ge=0, le=48)
    border_color: str = "#FFFFFF"
    shadow: bool = False
    opacity: float = Field(default=1.0, ge=0.1, le=1.0)
    animation: str = "none"
    fade_duration: float = Field(default=0.35, ge=0.0, le=3.0)
    loop: bool = True
    mute: bool = True


class PostProcessRequest(BaseModel):
    video_path: str
    picture_in_picture: PictureInPictureRequest = Field(default_factory=PictureInPictureRequest)
    subtitle_path: str | None = None
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    burn_subtitles: bool = False
    bgm: BGMSelection | None = None
    cover: CoverRequest = Field(default_factory=CoverRequest)
    workspace: str | None = None
    output_name: str | None = None


class PostProcessResult(BaseModel):
    success: bool
    final_video_path: str | None = None
    pip_video_path: str | None = None
    subtitle_video_path: str | None = None
    bgm_video_path: str | None = None
    cover_image_path: str | None = None
    pip_source_path: str | None = None
    cover_source_path: str | None = None
    bgm_source_path: str | None = None
    steps_applied: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
