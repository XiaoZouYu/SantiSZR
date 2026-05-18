from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from santiszr.domain.schemas.common import ErrorInfo


class WorkerTaskKind(str, Enum):
    full_workflow = "full-workflow"
    content = "content"
    rewrite = "rewrite"
    rewrite_text = "rewrite-text"
    tts = "tts"
    subtitle = "subtitle"
    avatar = "avatar"


class WorkerEventType(str, Enum):
    started = "started"
    progress = "progress"
    log = "log"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class WorkerTaskRequest(BaseModel):
    task_id: str
    task_kind: WorkerTaskKind
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkerEvent(BaseModel):
    event: WorkerEventType
    task_id: str
    task_kind: WorkerTaskKind
    stage: str = ""
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    error: ErrorInfo | None = None


def encode_json_line(model: BaseModel) -> str:
    return model.model_dump_json() + "\n"


def parse_task_request(raw: str) -> WorkerTaskRequest:
    return WorkerTaskRequest.model_validate_json(raw)


def parse_worker_event(raw: str) -> WorkerEvent:
    return WorkerEvent.model_validate_json(raw)
