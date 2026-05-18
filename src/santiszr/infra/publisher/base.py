from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import subprocess
import sys
from typing import Protocol

from santiszr.config.settings import load_settings
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.publish import PublishRequest, PublishResult
from santiszr.infra.publisher.browser_assist import browser_publish_assistant


@dataclass(slots=True)
class CommandExecution:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(self, command: list[str], *, cwd: Path) -> CommandExecution:
        ...


class SubprocessCommandRunner:
    def run(self, command: list[str], *, cwd: Path) -> CommandExecution:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        return CommandExecution(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


class ScriptPublisherBase(ABC):
    platform_name = "Publisher"

    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        python_executable: str | Path | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        settings = load_settings().publish
        configured_root = project_root or settings.external_publisher_root
        configured_python = python_executable or settings.python_executable or sys.executable
        self.project_root = Path(configured_root).expanduser().resolve() if configured_root else None
        self.python_executable = str(Path(configured_python).expanduser())
        self.runner = runner or SubprocessCommandRunner()

    def publish(self, request: PublishRequest) -> PublishResult:
        try:
            video_path, cover_path = self.validate_request(request)
            if self.project_root is None:
                if request.browser_assist:
                    return browser_publish_assistant.publish(
                        request,
                        video_path=video_path,
                        cover_path=cover_path,
                    )
                return self.not_configured_result(request)

            script_path = self.script_path()
            if not script_path.exists():
                return PublishResult(
                    success=False,
                    platform=request.platform,
                    status="failed",
                    command=[],
                    notes=[f"Configured external publisher root: {self.project_root}"],
                    error=ErrorInfo(
                        code="publish_script_missing",
                        message=f"{self.platform_name} publish script does not exist: {script_path}",
                    ),
                )

            command = self.build_command(request, video_path=video_path, cover_path=cover_path)
            execution = self.run_command(command)
            if execution.returncode == 0:
                return PublishResult(
                    success=True,
                    platform=request.platform,
                    status="published",
                    command=command,
                    stdout=execution.stdout,
                    stderr=execution.stderr,
                    notes=[f"{self.platform_name} publish script completed."],
                )

            return PublishResult(
                success=False,
                platform=request.platform,
                status="failed",
                command=command,
                stdout=execution.stdout,
                stderr=execution.stderr,
                error=ErrorInfo(
                    code="publish_failed",
                    message=(
                        f"{self.platform_name} publish script failed with exit code "
                        f"{execution.returncode}."
                    ),
                ),
            )
        except Exception as exc:
            return PublishResult(
                success=False,
                platform=request.platform,
                status="failed",
                error=ErrorInfo(code="publish_failed", message=str(exc)),
            )

    def validate_request(self, request: PublishRequest) -> tuple[Path, Path | None]:
        video_path = Path(request.video_path).expanduser().resolve()
        if not video_path.exists() or not video_path.is_file():
            raise FileNotFoundError(f"Publish video does not exist: {video_path}")

        cover_path = None
        if request.cover_path:
            cover_path = Path(request.cover_path).expanduser().resolve()
            if not cover_path.exists() or not cover_path.is_file():
                raise FileNotFoundError(f"Publish cover does not exist: {cover_path}")
        return video_path, cover_path

    def not_configured_result(self, request: PublishRequest) -> PublishResult:
        return PublishResult(
            success=False,
            platform=request.platform,
            status="failed",
            notes=[self.unavailable_note()],
            error=ErrorInfo(
                code="publish_not_configured",
                message=self.unavailable_message(),
            ),
        )

    def script_path(self) -> Path:
        if self.project_root is None:
            raise RuntimeError("External publisher root is not configured.")
        return self.project_root / self.script_relative_path()

    @abstractmethod
    def script_relative_path(self) -> Path:
        raise NotImplementedError

    @abstractmethod
    def build_command(
        self,
        request: PublishRequest,
        *,
        video_path: Path,
        cover_path: Path | None,
    ) -> list[str]:
        raise NotImplementedError

    def run_command(self, command: list[str]) -> CommandExecution:
        if self.project_root is None:
            raise RuntimeError("External publisher root is not configured.")
        return self.runner.run(command, cwd=self.project_root)

    def unavailable_note(self) -> str:
        return (
            f"{self.platform_name} publishing adapter is not bundled in SantiSZR. "
            "This build can only generate publishing materials."
        )

    def unavailable_message(self) -> str:
        return (
            f"{self.platform_name} publisher is not configured. "
            "This build can only generate publishing materials and cannot publish automatically."
        )

    def format_tags_csv(self, tags: list[str], *, limit: int | None = None) -> str:
        normalized = self.normalized_tags(tags, limit=limit)
        return ",".join(normalized)

    def format_tags_line(self, tags: list[str], *, limit: int | None = None) -> str:
        normalized = self.normalized_tags(tags, limit=limit)
        return " ".join(f"#{tag}" for tag in normalized)

    def normalized_tags(self, tags: list[str], *, limit: int | None = None) -> list[str]:
        normalized: list[str] = []
        for tag in tags:
            value = self.normalize_tag(tag)
            if not value:
                continue
            normalized.append(value)
            if limit is not None and len(normalized) >= limit:
                break
        return normalized

    def normalize_tag(self, tag: str) -> str:
        return tag.strip().strip("#").strip()

    def format_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat(sep=" ", timespec="seconds")
