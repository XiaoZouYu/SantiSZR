from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ErrorInfo(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class TaskContext(BaseModel):
    task_id: str
    created_at: datetime
    updated_at: datetime
    status: TaskStatus = TaskStatus.pending
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
