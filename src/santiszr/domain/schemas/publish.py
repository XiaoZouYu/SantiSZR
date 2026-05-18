from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from santiszr.domain.schemas.audio import RewriteMode, RewriteResult, TTSResult
from santiszr.domain.schemas.avatar import AvatarEngine, AvatarResult
from santiszr.domain.schemas.common import ErrorInfo, TaskContext
from santiszr.domain.schemas.content import ContentResult, VideoSource
from santiszr.domain.schemas.postprocess import BGMSelection, CoverRequest, PostProcessResult
from santiszr.domain.schemas.subtitle import SubtitleResult, SubtitleStyle


class PublishPlatform(str, Enum):
    douyin = "douyin"
    xiaohongshu = "xiaohongshu"
    wechat_channels = "wechat_channels"


class PublishRequest(BaseModel):
    platform: PublishPlatform
    video_path: str
    title: str
    tags: list[str] = Field(default_factory=list)
    cover_path: str | None = None
    scheduled_at: datetime | None = None
    workspace: str | None = None
    account_file: str | None = None
    category: str | None = None
    description: str | None = None
    browser_assist: bool = False


class PublishResult(BaseModel):
    success: bool
    platform: PublishPlatform
    post_id: str | None = None
    status: str | None = None
    command: list[str] = Field(default_factory=list)
    stdout: str | None = None
    stderr: str | None = None
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None


class PublishBatchRequest(BaseModel):
    platforms: list[PublishPlatform]
    video_path: str
    title: str
    tags: list[str] = Field(default_factory=list)
    cover_path: str | None = None
    scheduled_at: datetime | None = None
    workspace: str | None = None
    account_file: str | None = None
    category: str | None = None
    description: str | None = None
    continue_on_error: bool = True
    browser_assist: bool = False


class PublishBatchResult(BaseModel):
    success: bool
    results: list[PublishResult] = Field(default_factory=list)
    summary: str | None = None
    error: ErrorInfo | None = None


class GenerateVideoWorkflowRequest(BaseModel):
    source: VideoSource
    rewrite_mode: RewriteMode = RewriteMode.custom
    rewrite_prompt: str | None = None
    rewrite_model: str = "deepseek"
    voice: str
    reference_audio_path: str | None = None
    ultimate_clone: bool = False
    prompt_text: str | None = None
    voice_speed: float = 1.0
    avatar_model_id: str = "uploaded-avatar"
    avatar_engine: AvatarEngine = AvatarEngine.tuilionnx
    subtitle_burn_in: bool = True
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    enable_postprocess: bool = False
    bgm: BGMSelection | None = None
    cover: CoverRequest = Field(default_factory=CoverRequest)
    publish_platforms: list[PublishPlatform] = Field(default_factory=list)
    publish_continue_on_error: bool = True
    publish_title: str | None = None
    publish_tags: list[str] = Field(default_factory=list)
    publish_scheduled_at: datetime | None = None
    publish_account_file: str | None = None
    publish_category: str | None = None
    reference_video_path: str | None = None
    workspace: str


class WorkflowArtifacts(BaseModel):
    content: ContentResult | None = None
    rewrite: RewriteResult | None = None
    tts: TTSResult | None = None
    subtitle: SubtitleResult | None = None
    avatar: AvatarResult | None = None
    postprocess: PostProcessResult | None = None
    publish: PublishBatchResult | None = None


class GenerateVideoWorkflowResult(BaseModel):
    success: bool
    context: TaskContext
    artifacts: WorkflowArtifacts
    final_video_path: str | None = None
    final_cover_path: str | None = None
    summary: str | None = None
    error: ErrorInfo | None = None
