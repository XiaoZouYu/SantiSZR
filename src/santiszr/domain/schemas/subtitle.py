from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from santiszr.domain.schemas.common import ErrorInfo


class SubtitleStyle(BaseModel):
    font_name: str = "Microsoft YaHei"
    font_size: int = 32
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    bottom_margin: int = 72
    template: str = "short_video"
    highlight_keywords: list[str] = Field(default_factory=list)
    highlight_color: str = "#FF3B30"

    @field_validator("highlight_keywords", mode="before")
    @classmethod
    def normalize_highlight_keywords(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = value.replace("，", ",").replace("、", ",").split(",")
        elif isinstance(value, list):
            raw_items = [str(item) for item in value]
        else:
            raw_items = [str(value)]

        seen: set[str] = set()
        keywords: list[str] = []
        for item in raw_items:
            keyword = item.strip()
            if not keyword or keyword in seen:
                continue
            seen.add(keyword)
            keywords.append(keyword)
            if len(keywords) >= 12:
                break
        return keywords


class SubtitleRequest(BaseModel):
    audio_path: str
    video_path: str | None = None
    reference_text: str | None = None
    style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    burn_in: bool = True
    workspace: str | None = None
    output_name: str | None = None
    correct_with_ai: bool = False
    max_chars_per_line: int = 20


class SubtitleSegment(BaseModel):
    start_sec: float
    end_sec: float
    text: str


class SubtitleResult(BaseModel):
    success: bool
    srt_path: str | None = None
    ass_path: str | None = None
    burned_video_path: str | None = None
    subtitle_text: str | None = None
    ass_text: str | None = None
    subtitle_format: str = "ass"
    segments: list[SubtitleSegment] = Field(default_factory=list)
    generated_by: str | None = None
    corrected: bool = False
    quality_ok: bool = True
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
