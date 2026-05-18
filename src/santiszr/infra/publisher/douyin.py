from __future__ import annotations

from pathlib import Path

from santiszr.domain.schemas.publish import PublishRequest
from santiszr.infra.publisher.base import ScriptPublisherBase


class DouyinPublisher(ScriptPublisherBase):
    platform_name = "Douyin"

    def script_relative_path(self) -> Path:
        return Path("upload_video_to_douyin.py")

    def build_command(
        self,
        request: PublishRequest,
        *,
        video_path: Path,
        cover_path: Path | None,
    ) -> list[str]:
        return [
            self.python_executable,
            str(self.script_path()),
            str(video_path),
            str(cover_path) if cover_path else "",
            request.title,
            self.format_tags_csv(request.tags),
        ]
