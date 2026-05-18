from __future__ import annotations

from pathlib import Path

from santiszr.domain.schemas.publish import PublishRequest
from santiszr.infra.publisher.base import ScriptPublisherBase


class XiaohongshuPublisher(ScriptPublisherBase):
    platform_name = "Xiaohongshu"

    def script_relative_path(self) -> Path:
        return Path("uploader") / "xiaohongshu_uploader" / "main.py"

    def build_command(
        self,
        request: PublishRequest,
        *,
        video_path: Path,
        cover_path: Path | None,
    ) -> list[str]:
        command = [
            self.python_executable,
            str(self.script_path()),
            "--title",
            request.title,
            "--file_path",
            str(video_path),
            "--tags",
            self.format_tags_csv(request.tags, limit=10),
        ]
        publish_date = self.format_datetime(request.scheduled_at)
        if publish_date:
            command.extend(["--publish_date", publish_date])
        if request.account_file:
            command.extend(["--account_file", request.account_file])
        if cover_path:
            command.extend(["--thumbnail_path", str(cover_path)])
        return command
