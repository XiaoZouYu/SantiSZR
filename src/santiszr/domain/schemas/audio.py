from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from santiszr.domain.schemas.common import ErrorInfo


class RewriteMode(str, Enum):
    correct = "correct"
    imitate = "imitate"
    custom = "custom"


class RewriteRequest(BaseModel):
    text: str
    mode: RewriteMode
    prompt: str | None = None
    model: str = "deepseek"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    workspace: str | None = None


class RewriteResult(BaseModel):
    success: bool
    rewritten_text: str | None = None
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    provider: str | None = None
    prompt_used: str | None = None
    rewritten_text_path: str | None = None
    publish_text_path: str | None = None
    error: ErrorInfo | None = None


class TTSRequest(BaseModel):
    text: str
    voice: str
    reference_audio_path: str | None = None
    ultimate_clone: bool = False
    prompt_text: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    speaker: str | None = None
    sample_rate: int = 22050
    workspace: str | None = None
    output_name: str | None = None


class AudioMeta(BaseModel):
    duration_sec: float | None = None
    sample_rate: int | None = None
    channels: int | None = None


class TTSResult(BaseModel):
    success: bool
    audio_path: str | None = None
    source_text_path: str | None = None
    reference_audio_path: str | None = None
    meta: AudioMeta | None = None
    voice: str | None = None
    provider: str | None = None
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
