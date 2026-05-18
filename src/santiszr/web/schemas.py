from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from santiszr.domain.schemas.common import ErrorInfo


class WebTaskKind(str, Enum):
    content = "content"
    rewrite = "rewrite"
    tts = "tts"
    subtitle = "subtitle"
    avatar = "avatar"
    workflow = "workflow"
    postprocess = "postprocess"
    publish_materials = "publish_materials"


class WebTaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


def _event_for_status(status: object) -> str:
    value = status.value if isinstance(status, WebTaskStatus) else str(status)
    if value == WebTaskStatus.queued.value:
        return "created"
    if value == WebTaskStatus.running.value:
        return "progress"
    return value


class WorkspaceSelectRequest(BaseModel):
    path: str | None = None
    workspace: str | None = None

    @property
    def resolved_path(self) -> str:
        candidate = (self.path or self.workspace or "").strip()
        if not candidate:
            raise ValueError("Workspace path is required.")
        return candidate


class DiagnosticInfo(BaseModel):
    name: str
    status: str
    message: str
    detail: str | None = None


class LLMStatusInfo(BaseModel):
    configured: bool = False
    provider: str = "unconfigured"
    model: str = ""
    api_base: str = ""
    key_preview: str = ""
    message: str = ""


class LLMSettingsRequest(BaseModel):
    api_key: str | None = None
    api_base: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"


class LLMSettingsResponse(BaseModel):
    ok: bool = True
    llm: LLMStatusInfo


class LLMTestRequest(BaseModel):
    api_key: str | None = None
    api_base: str | None = None
    model: str | None = None


class LLMTestResponse(BaseModel):
    ok: bool
    provider: str = ""
    model: str = ""
    message: str = ""


class HealthResponse(BaseModel):
    app: str
    version: str
    workspace: str
    runtime_ok: bool
    diagnostics: list[DiagnosticInfo] = Field(default_factory=list)
    llm: LLMStatusInfo = Field(default_factory=LLMStatusInfo)


class StateResponse(BaseModel):
    workspace: str
    current_task: "TaskRecordResponse | None" = None
    recent_tasks: list["TaskRecordResponse"] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)


class WorkspaceResponse(BaseModel):
    workspace: str
    recent_workspaces: list[str] = Field(default_factory=list)


class AssetInfo(BaseModel):
    asset_id: str
    category: str
    name: str
    path: str
    size_bytes: int = 0
    modified_at: str | None = None
    source: str = "workspace"
    linked_text_path: str | None = None
    linked_text_ref: str | None = None
    text_preview: str | None = None


class AssetListResponse(BaseModel):
    workspace: str
    assets: list[AssetInfo] = Field(default_factory=list)


class AssetDeleteResponse(BaseModel):
    ok: bool = True
    path: str
    deleted: bool = True


class FileWriteRequest(BaseModel):
    path: str
    content: str = ""


class FileWriteResponse(BaseModel):
    ok: bool = True
    path: str
    size_bytes: int = 0
    modified_at: str | None = None


class UploadResponse(BaseModel):
    asset: AssetInfo
    asset_id: str | None = None
    category: str | None = None
    path: str | None = None
    name: str | None = None
    type: str | None = None
    size_bytes: int | None = None


class ReferenceTranscriptRequest(BaseModel):
    reference_audio_path: str
    workspace: str | None = None


class ReferenceTranscriptResponse(BaseModel):
    reference_audio_path: str
    transcript: str
    cache_hit: bool = False


class PublishMaterialsPrepareRequest(BaseModel):
    workspace: str
    video_path: str
    source_text: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    cover_title: str = ""
    cover_highlight: str = ""
    cover_timestamp_sec: float = Field(default=0.0, ge=0.0)
    generate_with_ai: bool = True
    generate_cover: bool = True


class PublishMaterialsPrepareResponse(BaseModel):
    ok: bool = True
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    two_line_input: str
    publish_text_path: str
    cover_path: str | None = None
    cover_title: str = ""
    cover_highlight: str = ""
    notes: list[str] = Field(default_factory=list)


class TaskSubmitRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_wrapped_or_direct_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        wrapped_payload = data.get("payload")
        if isinstance(wrapped_payload, dict):
            return {"payload": wrapped_payload}
        return {"payload": data}


class TaskSubmitResponse(BaseModel):
    task_id: str
    kind: WebTaskKind | None = None
    task_kind: WebTaskKind | None = None
    status: WebTaskStatus

    @model_validator(mode="before")
    @classmethod
    def populate_task_kind(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("task_kind") is None and data.get("kind") is not None:
            data = dict(data)
            data["task_kind"] = data["kind"]
        if data.get("kind") is None and data.get("task_kind") is not None:
            data = dict(data)
            data["kind"] = data["task_kind"]
        return data


class TaskEventResponse(BaseModel):
    task_id: str
    kind: WebTaskKind
    task_kind: WebTaskKind | None = None
    event: str | None = None
    status: WebTaskStatus
    stage: str = ""
    progress: float = 0.0
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    error: ErrorInfo | None = None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def populate_event_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if data.get("task_kind") is None and data.get("kind") is not None:
            data["task_kind"] = data["kind"]
        if data.get("kind") is None and data.get("task_kind") is not None:
            data["kind"] = data["task_kind"]
        if data.get("event") is None and data.get("status") is not None:
            data["event"] = _event_for_status(data["status"])
        return data


class TaskRecordResponse(BaseModel):
    task_id: str
    kind: WebTaskKind
    task_kind: WebTaskKind | None = None
    status: WebTaskStatus
    stage: str = ""
    progress: float = 0.0
    message: str = ""
    logs: list[str] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    error: ErrorInfo | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def populate_task_kind(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if data.get("task_kind") is None and data.get("kind") is not None:
            data["task_kind"] = data["kind"]
        if data.get("kind") is None and data.get("task_kind") is not None:
            data["kind"] = data["task_kind"]
        return data


class TaskListResponse(BaseModel):
    tasks: list[TaskRecordResponse]
