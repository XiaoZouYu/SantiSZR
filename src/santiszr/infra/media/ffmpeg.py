from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import wave
from math import ceil
from pathlib import Path

from santiszr.config.settings import load_settings
from santiszr.domain.schemas.subtitle import SubtitleSegment, SubtitleStyle


class FFmpegAdapter:
    def __init__(
        self,
        ffmpeg_path: str | Path | None = None,
        ffprobe_path: str | Path | None = None,
    ) -> None:
        settings = load_settings().media
        self.ffmpeg_path = self._resolve_binary(
            ffmpeg_path or settings.ffmpeg_path,
            [
                *self._project_tool_binaries("ffmpeg"),
                "ffmpeg",
            ],
        )
        self.ffprobe_path = self._resolve_binary(
            ffprobe_path or settings.ffprobe_path,
            [
                *self._project_tool_binaries("ffprobe"),
                "ffprobe",
            ],
        )
        self._gpu_video_encoder: str | None = None

    def available(self) -> bool:
        return bool(self.ffmpeg_path)

    def extract_audio(
        self,
        video_path: str | Path,
        output_path: str | Path,
        sample_rate: int = 22050,
        source_headers: dict[str, str] | None = None,
    ) -> Path:
        self._ensure_ffmpeg()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [self.ffmpeg_path, "-y"]
        if source_headers:
            header_blob = "".join(f"{key}: {value}\r\n" for key, value in source_headers.items())
            command.extend(["-headers", header_blob])
        command.extend(
            [
                "-i",
                str(video_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                str(output),
            ]
        )
        self._run(command)
        return output

    def extract_frame(
        self,
        video_path: str | Path,
        output_path: str | Path,
        *,
        timestamp_sec: float = 0.0,
    ) -> Path:
        self._ensure_ffmpeg()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [self.ffmpeg_path, "-y"]
        if timestamp_sec > 0:
            command.extend(["-ss", f"{timestamp_sec:.3f}"])
        command.extend(["-i", str(video_path), "-frames:v", "1", str(output)])
        self._run(command)
        return output

    def probe_duration(self, media_path: str | Path) -> float:
        path = Path(media_path)
        if self.ffprobe_path:
            completed = self._run(
                [
                    self.ffprobe_path,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(path),
                ]
            )
            data = json.loads(completed.stdout or "{}")
            duration = data.get("format", {}).get("duration")
            if duration is not None:
                return float(duration)

        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as handle:
                return handle.getnframes() / float(handle.getframerate())
        raise RuntimeError(f"Unable to probe duration for {path}")

    def probe_audio_meta(self, media_path: str | Path) -> dict[str, float | int | None]:
        path = Path(media_path)
        if self.ffprobe_path:
            completed = self._run(
                [
                    self.ffprobe_path,
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=sample_rate,channels:format=duration",
                    "-of",
                    "json",
                    str(path),
                ]
            )
            data = json.loads(completed.stdout or "{}")
            stream = (data.get("streams") or [{}])[0]
            return {
                "duration_sec": float(data.get("format", {}).get("duration", 0) or 0),
                "sample_rate": int(stream.get("sample_rate", 0) or 0),
                "channels": int(stream.get("channels", 0) or 0),
            }

        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as handle:
                return {
                    "duration_sec": handle.getnframes() / float(handle.getframerate()),
                    "sample_rate": handle.getframerate(),
                    "channels": handle.getnchannels(),
                }
        return {"duration_sec": None, "sample_rate": None, "channels": None}

    def probe_video_meta(self, media_path: str | Path) -> dict[str, float | int | None]:
        path = Path(media_path)
        if self.ffprobe_path:
            completed = self._run(
                [
                    self.ffprobe_path,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height:format=duration",
                    "-of",
                    "json",
                    str(path),
                ]
            )
            data = json.loads(completed.stdout or "{}")
            stream = (data.get("streams") or [{}])[0]
            return {
                "duration_sec": float(data.get("format", {}).get("duration", 0) or 0),
                "width": int(stream.get("width", 0) or 0),
                "height": int(stream.get("height", 0) or 0),
            }

        return {"duration_sec": None, "width": None, "height": None}

    def write_srt(self, segments: list[SubtitleSegment], output_path: str | Path) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for index, segment in enumerate(segments, start=1):
            lines.extend(
                [
                    str(index),
                    f"{self._format_srt_time(segment.start_sec)} --> {self._format_srt_time(segment.end_sec)}",
                    segment.text,
                    "",
                ]
            )
        output.write_text("\n".join(lines), encoding="utf-8")
        return output

    def burn_subtitles(
        self,
        video_path: str | Path,
        subtitle_path: str | Path,
        output_path: str | Path,
        style: SubtitleStyle | None = None,
    ) -> Path:
        self._ensure_ffmpeg()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        video_meta = self.probe_video_meta(video_path)
        effective_style, side_margin, _ = self._effective_subtitle_style(
            style or SubtitleStyle(),
            frame_width=video_meta.get("width"),
            frame_height=video_meta.get("height"),
        )
        prepared_subtitle_path, cleanup_subtitle_path = self._prepare_subtitle_file(
            subtitle_path,
            output.parent / f"{output.stem}.wrapped.srt",
            frame_width=video_meta.get("width"),
            frame_height=video_meta.get("height"),
            font_size=effective_style.font_size,
            side_margin=side_margin,
        )
        subtitle_filter = self._subtitle_filter(
            prepared_subtitle_path,
            effective_style,
            frame_width=video_meta.get("width"),
            frame_height=video_meta.get("height"),
        )
        try:
            self._run(
                [
                    self.ffmpeg_path,
                    "-y",
                    "-i",
                    str(video_path),
                    "-vf",
                    subtitle_filter,
                    *self._gpu_video_codec_args(),
                    "-c:a",
                    "copy",
                    str(output),
                ]
            )
        finally:
            if cleanup_subtitle_path and cleanup_subtitle_path.exists():
                cleanup_subtitle_path.unlink(missing_ok=True)
        return output

    def create_subtitle_video(
        self,
        audio_path: str | Path,
        subtitle_path: str | Path,
        output_path: str | Path,
        *,
        resolution: str = "1280x720",
        fps: int = 25,
        style: SubtitleStyle | None = None,
    ) -> Path:
        self._ensure_ffmpeg()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        duration = max(self.probe_duration(audio_path), 1.0)
        frame_width, frame_height = self._parse_resolution(resolution)
        effective_style, side_margin, _ = self._effective_subtitle_style(
            style or SubtitleStyle(),
            frame_width=frame_width,
            frame_height=frame_height,
        )
        prepared_subtitle_path, cleanup_subtitle_path = self._prepare_subtitle_file(
            subtitle_path,
            output.parent / f"{output.stem}.wrapped.srt",
            frame_width=frame_width,
            frame_height=frame_height,
            font_size=effective_style.font_size,
            side_margin=side_margin,
        )
        subtitle_filter = self._subtitle_filter(
            prepared_subtitle_path,
            effective_style,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        try:
            self._run(
                [
                    self.ffmpeg_path,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=0x111111:s={resolution}:r={fps}:d={duration:.3f}",
                    "-i",
                    str(audio_path),
                    "-vf",
                    subtitle_filter,
                    "-shortest",
                    "-pix_fmt",
                    "yuv420p",
                    *self._gpu_video_codec_args(),
                    "-c:a",
                    "aac",
                    str(output),
                ]
            )
        finally:
            if cleanup_subtitle_path and cleanup_subtitle_path.exists():
                cleanup_subtitle_path.unlink(missing_ok=True)
        return output

    def compose_avatar_video(
        self,
        audio_path: str | Path,
        output_path: str | Path,
        *,
        background_video_path: str | Path | None = None,
        background_image_path: str | Path | None = None,
        subtitle_path: str | Path | None = None,
        overlay_text: str | None = None,
        resolution: str = "1920x1080",
        fps: int = 25,
        style: SubtitleStyle | None = None,
    ) -> Path:
        self._ensure_ffmpeg()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        duration = max(self.probe_duration(audio_path), 1.0)
        temp_output = output.with_name(f"{output.stem}.tmp{output.suffix}")
        if temp_output.exists():
            temp_output.unlink()
        filters = [f"scale={resolution}"]
        if subtitle_path:
            filters.append(self._subtitle_filter(subtitle_path, style or SubtitleStyle()))
        if overlay_text:
            safe_text = overlay_text.replace(":", r"\:").replace("'", r"\'")
            filters.append(
                "drawtext="
                f"text='{safe_text}':x=(w-text_w)/2:y=h-140:fontsize=32:fontcolor=white:"
                "box=1:boxcolor=black@0.45:boxborderw=16"
            )
        command = [self.ffmpeg_path, "-y"]
        if background_video_path:
            command.extend(["-stream_loop", "-1", "-i", str(background_video_path)])
        elif background_image_path:
            command.extend(["-loop", "1", "-i", str(background_image_path)])
        else:
            command.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=0x1b2230:s={resolution}:r={fps}:d={duration:.3f}",
                ]
            )
        command.extend(
            [
                "-i",
                str(audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-vf",
                ",".join(filters),
                "-t",
                f"{duration:.3f}",
                "-shortest",
                "-pix_fmt",
                "yuv420p",
                *self._gpu_video_codec_args(),
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(temp_output),
            ]
        )
        try:
            self._run(command, timeout_sec=max(60.0, duration * 4.0 + 30.0))
            temp_output.replace(output)
        except Exception:
            if temp_output.exists():
                temp_output.unlink()
            raise
        return output

    def mix_background_music(
        self,
        video_path: str | Path,
        bgm_path: str | Path,
        output_path: str | Path,
        *,
        bgm_volume: float = 0.2,
    ) -> Path:
        self._ensure_ffmpeg()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        volume = max(0.0, min(float(bgm_volume), 1.0))
        self._run(
            [
                self.ffmpeg_path,
                "-y",
                "-i",
                str(video_path),
                "-stream_loop",
                "-1",
                "-i",
                str(bgm_path),
                "-filter_complex",
                (
                    f"[1:a]volume={volume:.3f}[bgm];"
                    "[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
                ),
                "-map",
                "0:v",
                "-map",
                "[aout]",
                *self._gpu_video_codec_args(),
                "-c:a",
                "aac",
                str(output),
            ]
        )
        return output

    def render_cover_image(
        self,
        video_path: str | Path,
        output_path: str | Path,
        *,
        timestamp_sec: float = 0.0,
        title: str = "",
        highlight_text: str = "",
        font_name: str = "Microsoft YaHei",
        font_size: int = 64,
        font_color: str = "#FFFFFF",
        highlight_color: str = "#F59E0B",
        position: str = "bottom",
    ) -> Path:
        self._ensure_ffmpeg()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        filters = [
            "scale=1080:1920:force_original_aspect_ratio=decrease",
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
            "setsar=1",
        ]
        title_y = {
            "top": "132",
            "center": "(h-text_h)/2-72",
            "bottom": "h-text_h-252",
        }.get(position, "h-text_h-252")
        highlight_y = {
            "top": "252",
            "center": "(h-text_h)/2+84",
            "bottom": "h-text_h-132",
        }.get(position, "h-text_h-132")
        if title.strip():
            filters.append(
                self._drawtext_filter(
                    text=title,
                    y=title_y,
                    font_name=font_name,
                    font_size=font_size,
                    font_color=font_color,
                )
            )
        if highlight_text.strip():
            filters.append(
                self._drawtext_filter(
                    text=highlight_text,
                    y=highlight_y,
                    font_name=font_name,
                    font_size=max(int(font_size * 0.72), 28),
                    font_color=highlight_color,
                    box_color="black@0.15",
                )
            )
        command = [self.ffmpeg_path, "-y"]
        if timestamp_sec > 0:
            command.extend(["-ss", f"{timestamp_sec:.3f}"])
        command.extend(["-i", str(video_path), "-frames:v", "1"])
        if filters:
            command.extend(["-vf", ",".join(filters)])
        command.append(str(output))
        self._run(command)
        return output

    def _subtitle_filter(
        self,
        subtitle_path: str | Path,
        style: SubtitleStyle,
        *,
        frame_width: int | float | None = None,
        frame_height: int | float | None = None,
    ) -> str:
        escaped_path = str(Path(subtitle_path).resolve()).replace("\\", "/").replace(":", r"\:")
        effective_style, side_margin, outline = self._effective_subtitle_style(
            style,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        force_style = (
            f"FontName={effective_style.font_name},"
            f"FontSize={effective_style.font_size},"
            f"PrimaryColour={self._to_ass_color(effective_style.color, fallback='FFFFFF')},"
            f"OutlineColour={self._to_ass_color(effective_style.outline_color, fallback='000000')},"
            f"BorderStyle=1,Outline={outline},Shadow=0,Alignment=2,"
            f"MarginL={side_margin},MarginR={side_margin},MarginV={max(effective_style.bottom_margin, 0)}"
        )
        return f"subtitles='{escaped_path}':force_style='{force_style}'"

    def _effective_subtitle_style(
        self,
        style: SubtitleStyle,
        *,
        frame_width: int | float | None = None,
        frame_height: int | float | None = None,
    ) -> tuple[SubtitleStyle, int, int]:
        font_size = max(int(style.font_size), 12)
        bottom_margin = max(int(style.bottom_margin), 0)
        side_margin = 36

        width = int(frame_width or 0)
        height = int(frame_height or 0)
        if width > 0 and height > 0:
            max_font_by_height = max(18, int(height * 0.055))
            max_font_by_width = max(18, int(width * 0.04))
            font_size = min(font_size, max_font_by_height, max_font_by_width)
            if height > width:
                font_size = max(int(round(font_size * 0.5)), 12)
                min_bottom_margin = max(24, int(height * 0.025), font_size + 14)
            else:
                min_bottom_margin = max(int(height * 0.05), font_size + 18)
            side_margin = max(24, int(width * 0.06))
            bottom_margin = max(bottom_margin, min_bottom_margin)
        else:
            bottom_margin = max(bottom_margin, font_size + 18)

        outline = max(2, int(round(font_size / 14.0)))
        return (
            SubtitleStyle(
                font_name=style.font_name,
                font_size=font_size,
                color=style.color,
                outline_color=style.outline_color,
                bottom_margin=bottom_margin,
            ),
            side_margin,
            outline,
        )

    def _prepare_subtitle_file(
        self,
        subtitle_path: str | Path,
        temp_path: str | Path,
        *,
        frame_width: int | float | None,
        frame_height: int | float | None,
        font_size: int,
        side_margin: int,
    ) -> tuple[Path, Path | None]:
        source_path = Path(subtitle_path).resolve()
        wrapped_text = self._rewrap_srt_content(
            source_path.read_text(encoding="utf-8"),
            frame_width=frame_width,
            frame_height=frame_height,
            font_size=font_size,
            side_margin=side_margin,
        )
        original_text = source_path.read_text(encoding="utf-8")
        if wrapped_text == original_text:
            return source_path, None

        wrapped_path = Path(temp_path).resolve()
        wrapped_path.write_text(wrapped_text, encoding="utf-8")
        return wrapped_path, wrapped_path

    def _rewrap_srt_content(
        self,
        content: str,
        *,
        frame_width: int | float | None,
        frame_height: int | float | None,
        font_size: int,
        side_margin: int,
    ) -> str:
        max_chars_per_line = self._max_subtitle_chars_per_line(
            frame_width=frame_width,
            frame_height=frame_height,
            font_size=font_size,
            side_margin=side_margin,
        )
        blocks = re.split(r"\r?\n\r?\n", content.strip())
        rewritten_blocks: list[str] = []
        changed = False

        for block in blocks:
            lines = [line for line in block.splitlines() if line.strip()]
            if len(lines) < 3:
                rewritten_blocks.append(block.strip())
                continue
            text = "\n".join(lines[2:])
            wrapped_text = self._wrap_subtitle_text(text, max_chars_per_line=max_chars_per_line)
            if wrapped_text != text.strip():
                changed = True
            rewritten_blocks.append("\n".join([lines[0], lines[1], wrapped_text]))

        if not changed:
            return content
        return "\n\n".join(rewritten_blocks) + "\n"

    def _max_subtitle_chars_per_line(
        self,
        *,
        frame_width: int | float | None,
        frame_height: int | float | None,
        font_size: int,
        side_margin: int,
    ) -> int:
        width = int(frame_width or 0)
        height = int(frame_height or 0)
        if width <= 0:
            return 12
        available_width = max(width - side_margin * 2, font_size * 4)
        estimated = int(available_width / max(font_size * 1.65, 1))
        if height > width > 0:
            return max(8, min(estimated, 12))
        return max(10, min(estimated, 18))

    def _wrap_subtitle_text(self, text: str, *, max_chars_per_line: int) -> str:
        stripped_lines = [line.strip() for line in text.splitlines() if line.strip()]
        compact = " ".join(stripped_lines)
        if not compact:
            return ""

        contains_cjk = bool(re.search(r"[\u3400-\u9fff]", compact))
        if contains_cjk:
            compact = re.sub(r"\s+", "", compact)
            if len(compact) <= max_chars_per_line:
                return compact
            max_lines = 3
            chunk_size = max(max_chars_per_line, ceil(len(compact) / max_lines))
            return "\n".join(
                compact[index : index + chunk_size]
                for index in range(0, len(compact), chunk_size)
            )

        words = compact.split()
        if len(words) <= 1 and len(compact) <= max_chars_per_line:
            return compact

        lines: list[str] = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if len(candidate) <= max_chars_per_line:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
        if current:
            lines.append(current)
        return "\n".join(lines)

    def _drawtext_filter(
        self,
        *,
        text: str,
        y: str,
        font_name: str,
        font_size: int,
        font_color: str,
        box_color: str = "black@0.45",
    ) -> str:
        safe_text = (
            text.replace("\\", "\\\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
            .replace(",", r"\,")
        )
        return (
            "drawtext="
            f"font='{font_name}':"
            f"text='{safe_text}':"
            "x=(w-text_w)/2:"
            f"y={y}:"
            f"fontsize={max(font_size, 12)}:"
            f"fontcolor={self._normalize_drawtext_color(font_color)}:"
            "box=1:"
            f"boxcolor={box_color}:"
            "boxborderw=24"
        )

    def _to_ass_color(self, value: str, *, fallback: str) -> str:
        normalized = self._normalize_hex_color(value, fallback=fallback)
        return f"&H00{normalized[4:6]}{normalized[2:4]}{normalized[0:2]}"

    def _normalize_drawtext_color(self, value: str) -> str:
        return f"#{self._normalize_hex_color(value, fallback='FFFFFF')}"

    def _normalize_hex_color(self, value: str, *, fallback: str) -> str:
        normalized = value.strip().lstrip("#")
        if len(normalized) == 3:
            normalized = "".join(ch * 2 for ch in normalized)
        if len(normalized) != 6:
            return fallback
        if any(ch not in "0123456789abcdefABCDEF" for ch in normalized):
            return fallback
        return normalized.upper()

    def _parse_resolution(self, value: str) -> tuple[int | None, int | None]:
        raw = value.strip().lower()
        if "x" not in raw:
            return None, None
        width_text, height_text = raw.split("x", maxsplit=1)
        try:
            width = int(width_text)
            height = int(height_text)
        except ValueError:
            return None, None
        if width <= 0 or height <= 0:
            return None, None
        return width, height

    def _ensure_ffmpeg(self) -> None:
        if not self.ffmpeg_path:
            raise RuntimeError("FFmpeg binary is not available.")

    def _project_tool_binaries(self, name: str) -> list[Path]:
        root = Path(__file__).resolve().parents[4]
        return [
            root / "tools" / "ffmpeg-compat" / "bin" / f"{name}.exe",
            root / "tools" / "ffmpeg" / "bin" / f"{name}.exe",
        ]

    def _resolve_binary(self, explicit: str | Path | None, candidates: list[str | Path]) -> str | None:
        if explicit:
            path = Path(explicit)
            if path.exists():
                return str(path)
        for candidate in candidates:
            if isinstance(candidate, Path):
                resolved = str(candidate) if candidate.exists() else None
            else:
                resolved = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
            if resolved:
                return str(resolved)
        return None

    def _run(
        self,
        command: list[str],
        *,
        timeout_sec: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Command timed out after {timeout_sec:.1f}s: {' '.join(command)}"
            ) from exc
        if completed.returncode != 0:
            raise RuntimeError(
                f"Command failed with exit code {completed.returncode}: {' '.join(command)}\n"
                f"{completed.stderr.strip()}"
            )
        return completed

    def _gpu_video_codec_args(self) -> list[str]:
        return ["-c:v", self._resolve_gpu_video_encoder()]

    def _resolve_gpu_video_encoder(self) -> str:
        if self._gpu_video_encoder:
            return self._gpu_video_encoder

        self._ensure_ffmpeg()
        preferred = os.getenv("SANTISZR_VIDEO_ENCODER", "").strip().lower()
        encoders = self._available_video_encoders()
        candidates = [preferred] if preferred else []
        candidates.extend(["h264_nvenc", "h264_amf", "h264_qsv", "h264_videotoolbox"])
        probe_failures: list[str] = []
        attempted: set[str] = set()

        for encoder in candidates:
            normalized = encoder.strip().lower() if encoder else ""
            if not normalized or normalized in attempted:
                continue
            attempted.add(normalized)
            if normalized not in encoders:
                probe_failures.append(f"{normalized}: not listed by FFmpeg")
                continue
            ok, detail = self._probe_gpu_video_encoder(normalized)
            if ok:
                self._gpu_video_encoder = normalized
                return normalized
            probe_failures.append(f"{normalized}: {detail}")

        failure_detail = "; ".join(probe_failures) if probe_failures else "No encoder probe details are available."
        raise RuntimeError(
            "GPU video encoder is required but none of h264_nvenc/h264_amf/h264_qsv/"
            "h264_videotoolbox is usable in the current FFmpeg runtime. "
            f"FFmpeg: {self.ffmpeg_path}. Probe details: {failure_detail}"
        )

    def _probe_gpu_video_encoder(self, encoder: str) -> tuple[bool, str]:
        completed = subprocess.run(
            [
                self.ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=512x512:d=0.2",
                "-frames:v",
                "1",
                "-an",
                "-c:v",
                encoder,
                "-f",
                "null",
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        if completed.returncode == 0:
            return True, ""
        return False, self._summarize_probe_failure(completed, fallback=f"probe exited with code {completed.returncode}")

    def _available_video_encoders(self) -> set[str]:
        completed = self._run([self.ffmpeg_path, "-hide_banner", "-encoders"])
        encoders: set[str] = set()
        for line in (completed.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("V"):
                encoders.add(parts[1].strip())
        return encoders

    def _summarize_probe_failure(
        self,
        completed: subprocess.CompletedProcess[str],
        *,
        fallback: str,
    ) -> str:
        raw_lines = []
        for chunk in (completed.stderr or "", completed.stdout or ""):
            raw_lines.extend(chunk.splitlines())
        lines = [line.strip() for line in raw_lines if line.strip()]
        if not lines:
            return fallback
        return " | ".join(lines[-4:])

    def _format_srt_time(self, value: float) -> str:
        total_millis = max(int(round(value * 1000)), 0)
        hours, remainder = divmod(total_millis, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1_000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"
