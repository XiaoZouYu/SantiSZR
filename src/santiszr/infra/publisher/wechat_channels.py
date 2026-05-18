from __future__ import annotations

from pathlib import Path

from santiszr.domain.schemas.publish import PublishRequest
from santiszr.infra.publisher.base import ScriptPublisherBase


class WechatChannelsPublisher(ScriptPublisherBase):
    platform_name = "WeChat Channels"

    def script_relative_path(self) -> Path:
        return Path("upload_video_to_tencent.py")

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
            "--file_path",
            str(video_path),
            "--title",
            request.title,
            "--tags",
            self.format_tags_csv(request.tags),
        ]
        publish_date = self.format_datetime(request.scheduled_at)
        if publish_date:
            command.extend(["--publish_date", publish_date])
        if request.account_file:
            command.extend(["--account_file", request.account_file])
        command.extend(["--category", request.category or "生活"])
        if cover_path:
            command.extend(["--cover_path", str(cover_path)])
        return command
