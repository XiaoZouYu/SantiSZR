from __future__ import annotations

from pathlib import Path
import time

from santiszr.domain.schemas.avatar import AvatarRequest, AvatarResult
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.core.paths import ensure_module_dir, sanitize_filename
from santiszr.infra.avatar.tuilionnx import TuiliOnnxAdapter
from santiszr.infra.media.ffmpeg import FFmpegAdapter


class AvatarService:
    def __init__(
        self,
        tuilionnx: TuiliOnnxAdapter | None = None,
        ffmpeg: FFmpegAdapter | None = None,
    ) -> None:
        self.ffmpeg = ffmpeg or FFmpegAdapter()
        self.tuilionnx = tuilionnx or TuiliOnnxAdapter(ffmpeg=self.ffmpeg)

    def render(self, request: AvatarRequest) -> AvatarResult:
        workspace = (
            Path(request.workspace).expanduser().resolve()
            if request.workspace
            else Path(request.audio_path).resolve().parent.parent
        )
        avatar_dir = ensure_module_dir(workspace, "avatar")
        started = time.perf_counter()
        try:
            reference_video_path = (request.reference_video_path or "").strip()
            if not reference_video_path:
                raise RuntimeError("Avatar rendering requires an uploaded reference video.")
            output_stem = Path(reference_video_path).expanduser().resolve().stem
            base_name = f"{sanitize_filename(output_stem)}_{request.engine.value}"
            output_path = self._resolve_output_path(avatar_dir, base_name)
            video_path, model_asset_path, notes = self.tuilionnx.render(
                audio_path=request.audio_path,
                model_id=request.model_id,
                output_path=output_path,
                subtitle_path=request.subtitle_path,
                subtitle_style=request.subtitle_style,
                background_video_path=reference_video_path,
                overlay_text=request.overlay_text,
                batch_size=request.batch_size,
                sync_offset=request.sync_offset,
                scale_h=request.scale_h,
                scale_w=request.scale_w,
                compress_inference=request.compress_inference,
                beautify_teeth=request.beautify_teeth,
                add_ai_watermark=request.add_ai_watermark,
            )
            engine_used = "tuilionnx-lipsync"
            duration = self.ffmpeg.probe_duration(video_path)
            return AvatarResult(
                success=True,
                video_path=str(video_path),
                duration_sec=duration,
                elapsed_sec=time.perf_counter() - started,
                engine_used=engine_used,
                model_asset_path=str(model_asset_path) if model_asset_path else None,
                notes=notes,
            )
        except Exception as exc:
            return AvatarResult(
                success=False,
                elapsed_sec=time.perf_counter() - started,
                error=ErrorInfo(code="avatar_failed", message=str(exc)),
            )

    def _resolve_output_path(self, avatar_dir: Path, base_name: str) -> Path:
        candidate = avatar_dir / f"{base_name}.mp4"
        if not candidate.exists():
            return candidate

        index = 2
        while True:
            candidate = avatar_dir / f"{base_name}-{index}.mp4"
            if not candidate.exists():
                return candidate
            index += 1
