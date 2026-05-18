from __future__ import annotations

import random
from pathlib import Path

from santiszr.core.paths import ensure_module_dir, sanitize_filename
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.postprocess import BGMSelection, PostProcessRequest, PostProcessResult
from santiszr.infra.media.ffmpeg import FFmpegAdapter


class PostProcessService:
    def __init__(self, ffmpeg: FFmpegAdapter | None = None) -> None:
        self.ffmpeg = ffmpeg or FFmpegAdapter()

    def process(self, request: PostProcessRequest) -> PostProcessResult:
        notes: list[str] = []
        steps_applied: list[str] = []
        subtitle_video_path: Path | None = None
        bgm_video_path: Path | None = None
        cover_image_path: Path | None = None
        cover_source_path: Path | None = None
        bgm_source_path: Path | None = None

        try:
            source_video = Path(request.video_path).expanduser().resolve()
            if not source_video.exists():
                raise FileNotFoundError(f"Video file does not exist: {source_video}")

            workspace = (
                Path(request.workspace).expanduser().resolve()
                if request.workspace
                else source_video.parent.parent
            )
            postprocess_dir = ensure_module_dir(workspace, "postprocess")
            output_base = sanitize_filename(request.output_name or source_video.stem, fallback="postprocess")

            current_video = source_video

            if request.burn_subtitles:
                if not request.subtitle_path:
                    raise ValueError("Subtitle path is required when burn_subtitles is enabled.")
                subtitle_source = Path(request.subtitle_path).expanduser().resolve()
                if not subtitle_source.exists():
                    raise FileNotFoundError(f"Subtitle file does not exist: {subtitle_source}")
                subtitle_video_path = self.ffmpeg.burn_subtitles(
                    current_video,
                    subtitle_source,
                    postprocess_dir / f"{output_base}_subtitle.mp4",
                    style=request.subtitle_style,
                )
                current_video = subtitle_video_path
                steps_applied.append("subtitle")

            if request.bgm is not None:
                bgm_source_path, bgm_note = self._resolve_bgm_source(request.bgm)
                if bgm_note:
                    notes.append(bgm_note)
                bgm_video_path = self.ffmpeg.mix_background_music(
                    current_video,
                    bgm_source_path,
                    postprocess_dir / f"{output_base}_bgm.mp4",
                    bgm_volume=request.bgm.volume,
                )
                current_video = bgm_video_path
                steps_applied.append("bgm")

            if request.cover.enabled:
                cover_source_path = Path(request.cover.source_video_path or request.video_path).expanduser().resolve()
                if not cover_source_path.exists():
                    raise FileNotFoundError(f"Cover source video does not exist: {cover_source_path}")
                timestamp_sec = request.cover.timestamp_sec
                if timestamp_sec is None:
                    timestamp_sec = self.ffmpeg.probe_duration(cover_source_path) / 2
                cover_name = sanitize_filename(
                    request.cover.output_name or f"{output_base}_cover",
                    fallback=f"{output_base}_cover",
                )
                cover_image_path = self.ffmpeg.render_cover_image(
                    video_path=cover_source_path,
                    output_path=postprocess_dir / f"{cover_name}.jpg",
                    timestamp_sec=timestamp_sec,
                    title=request.cover.title,
                    highlight_text=request.cover.highlight_text,
                    font_name=request.cover.style.font_name,
                    font_size=request.cover.style.font_size,
                    font_color=request.cover.style.font_color,
                    highlight_color=request.cover.style.highlight_color,
                    position=request.cover.style.position,
                )
                steps_applied.append("cover")

            if not steps_applied:
                notes.append("No postprocess step was applied.")

            return PostProcessResult(
                success=True,
                final_video_path=str(current_video),
                subtitle_video_path=str(subtitle_video_path) if subtitle_video_path else None,
                bgm_video_path=str(bgm_video_path) if bgm_video_path else None,
                cover_image_path=str(cover_image_path) if cover_image_path else None,
                cover_source_path=str(cover_source_path) if cover_source_path else None,
                bgm_source_path=str(bgm_source_path) if bgm_source_path else None,
                steps_applied=steps_applied,
                notes=notes,
            )
        except Exception as exc:
            return PostProcessResult(
                success=False,
                final_video_path=str(source_video) if "source_video" in locals() else None,
                subtitle_video_path=str(subtitle_video_path) if subtitle_video_path else None,
                bgm_video_path=str(bgm_video_path) if bgm_video_path else None,
                cover_image_path=str(cover_image_path) if cover_image_path else None,
                cover_source_path=str(cover_source_path) if cover_source_path else None,
                bgm_source_path=str(bgm_source_path) if bgm_source_path else None,
                steps_applied=steps_applied,
                notes=notes,
                error=ErrorInfo(code="postprocess_failed", message=str(exc)),
            )

    def _resolve_bgm_source(self, selection: BGMSelection) -> tuple[Path, str | None]:
        if selection.bgm_path:
            path = Path(selection.bgm_path).expanduser().resolve()
            if not path.exists():
                raise FileNotFoundError(f"BGM file does not exist: {path}")
            return path, None

        if not selection.bgm_directory:
            raise ValueError("BGM configuration requires bgm_path or bgm_directory.")

        bgm_directory = Path(selection.bgm_directory).expanduser().resolve()
        if not bgm_directory.exists() or not bgm_directory.is_dir():
            raise FileNotFoundError(f"BGM directory does not exist: {bgm_directory}")

        candidates = sorted(
            path
            for path in bgm_directory.iterdir()
            if path.is_file() and path.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".flac"}
        )
        if not candidates:
            raise FileNotFoundError(f"No BGM asset is available in: {bgm_directory}")

        if selection.bgm_name:
            normalized = selection.bgm_name.strip().lower()
            for candidate in candidates:
                if candidate.name.lower() == normalized or candidate.stem.lower() == normalized:
                    return candidate, None
            raise FileNotFoundError(f"BGM asset was not found: {selection.bgm_name}")

        if selection.random_choice:
            chosen = random.choice(candidates)
            return chosen, f"Selected random BGM asset: {chosen.name}"

        if len(candidates) == 1:
            return candidates[0], None

        raise ValueError("Multiple BGM assets are available; set bgm_name or random_choice.")
