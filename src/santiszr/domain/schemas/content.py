from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from santiszr.domain.schemas.common import ErrorInfo


class VideoSource(BaseModel):
    source_type: Literal["douyin_share_text", "url", "local_video", "local_audio", "raw_text"] = (
        "douyin_share_text"
    )
    raw_input: str


class ContentRequest(BaseModel):
    source: VideoSource
    workspace: str
    download_video: bool = False
    extract_audio: bool = False
    stream_transcription: bool = True


class ExtractedCopy(BaseModel):
    raw_text: str
    cleaned_text: str
    title: str | None = None
    source: str = "unknown"
    language: str = "zh-CN"


class ContentResult(BaseModel):
    success: bool
    platform: str = "unknown"
    workspace: str | None = None
    video_id: str | None = None
    source_url: str | None = None
    resolved_url: str | None = None
    title: str | None = None
    video_path: str | None = None
    audio_path: str | None = None
    cover_path: str | None = None
    transcript_path: str | None = None
    extracted_copy: ExtractedCopy | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
