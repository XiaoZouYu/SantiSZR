from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class TaskContext:
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    message: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def advance(self, progress: float, message: str = "") -> None:
        self.progress = max(0.0, min(progress, 1.0))
        self.message = message
        self.updated_at = datetime.now(UTC)

    def mark_running(self, message: str = "") -> None:
        self.status = TaskStatus.RUNNING
        self.advance(self.progress, message=message)

    def mark_succeeded(self, message: str = "") -> None:
        self.status = TaskStatus.SUCCEEDED
        self.advance(1.0, message=message)

    def mark_failed(self, message: str = "") -> None:
        self.status = TaskStatus.FAILED
        self.advance(self.progress, message=message)
