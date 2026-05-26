from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from santiszr.domain.schemas.subtitle import SubtitleStyle
from santiszr.infra.media.ffmpeg import FFmpegAdapter


def test_ffmpeg_adapter_prefers_compat_binary_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compat_dir = tmp_path / "tools" / "ffmpeg-compat" / "bin"
    compat_dir.mkdir(parents=True, exist_ok=True)
    compat_ffmpeg = compat_dir / "ffmpeg.exe"
    compat_ffprobe = compat_dir / "ffprobe.exe"
    compat_ffmpeg.write_bytes(b"")
    compat_ffprobe.write_bytes(b"")

    default_dir = tmp_path / "tools" / "ffmpeg" / "bin"
    default_dir.mkdir(parents=True, exist_ok=True)
    default_ffmpeg = default_dir / "ffmpeg.exe"
    default_ffprobe = default_dir / "ffprobe.exe"
    default_ffmpeg.write_bytes(b"")
    default_ffprobe.write_bytes(b"")

    def fake_project_tool_binaries(self: FFmpegAdapter, name: str) -> list[Path]:
        if name == "ffmpeg":
            return [compat_ffmpeg, default_ffmpeg]
        return [compat_ffprobe, default_ffprobe]

    monkeypatch.setattr(FFmpegAdapter, "_project_tool_binaries", fake_project_tool_binaries)

    adapter = FFmpegAdapter()

    assert adapter.ffmpeg_path == str(compat_ffmpeg)
    assert adapter.ffprobe_path == str(compat_ffprobe)


def test_probe_gpu_video_encoder_uses_supported_probe_frame_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = FFmpegAdapter.__new__(FFmpegAdapter)
    adapter.ffmpeg_path = "ffmpeg.exe"
    adapter.ffprobe_path = "ffprobe.exe"
    adapter._gpu_video_encoder = None

    ok, detail = adapter._probe_gpu_video_encoder("h264_nvenc")

    assert ok is True
    assert detail == ""
    assert "color=c=black:s=512x512:d=0.2" in captured["command"]


def test_resolve_gpu_video_encoder_reports_probe_details() -> None:
    adapter = FFmpegAdapter.__new__(FFmpegAdapter)
    adapter.ffmpeg_path = "D:/tools/ffmpeg-compat/bin/ffmpeg.exe"
    adapter.ffprobe_path = "D:/tools/ffmpeg-compat/bin/ffprobe.exe"
    adapter._gpu_video_encoder = None
    adapter._ensure_ffmpeg = lambda: None  # type: ignore[method-assign]
    adapter._available_video_encoders = lambda: {"h264_nvenc"}  # type: ignore[method-assign]
    adapter._probe_gpu_video_encoder = lambda encoder: (  # type: ignore[method-assign]
        False,
        f"{encoder} failed: driver too old",
    )

    with pytest.raises(RuntimeError) as excinfo:
        adapter._resolve_gpu_video_encoder()

    message = str(excinfo.value)
    assert "D:/tools/ffmpeg-compat/bin/ffmpeg.exe" in message
    assert "h264_nvenc failed: driver too old" in message


def test_compose_avatar_video_limits_duration_and_commits_atomically(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    output_path = tmp_path / "avatar.mp4"

    adapter = FFmpegAdapter.__new__(FFmpegAdapter)
    adapter.ffmpeg_path = "ffmpeg.exe"
    adapter.ffprobe_path = "ffprobe.exe"
    adapter._gpu_video_encoder = None
    adapter._ensure_ffmpeg = lambda: None  # type: ignore[method-assign]
    adapter.probe_duration = lambda media_path: 2.0  # type: ignore[method-assign]
    adapter._gpu_video_codec_args = lambda: ["-c:v", "h264_nvenc"]  # type: ignore[method-assign]

    def fake_run(
        command: list[str], *, timeout_sec: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["timeout_sec"] = timeout_sec
        Path(command[-1]).write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0, "", "")

    adapter._run = fake_run  # type: ignore[method-assign]

    result = adapter.compose_avatar_video(
        audio_path=tmp_path / "audio.wav",
        output_path=output_path,
        background_video_path=tmp_path / "reference.mp4",
        overlay_text="avatar",
        resolution="1920x1080",
        fps=25,
    )

    command = captured["command"]
    assert result == output_path
    assert output_path.exists()
    assert not (tmp_path / "avatar.tmp.mp4").exists()
    assert command[0] == "ffmpeg.exe"
    assert "-map" in command
    assert "0:v:0" in command
    assert "1:a:0" in command
    assert "-t" in command
    assert "2.000" in command
    assert command[-1].endswith("avatar.tmp.mp4")
    assert captured["timeout_sec"] == 60.0


def test_render_cover_image_draws_text_inside_video_frame_before_padding(tmp_path: Path) -> None:
    captured: dict[str, list[str]] = {}
    output_path = tmp_path / "cover.jpg"

    adapter = FFmpegAdapter.__new__(FFmpegAdapter)
    adapter.ffmpeg_path = "ffmpeg.exe"
    adapter.ffprobe_path = "ffprobe.exe"
    adapter._gpu_video_encoder = None
    adapter._ensure_ffmpeg = lambda: None  # type: ignore[method-assign]

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, "", "")

    adapter._run = fake_run  # type: ignore[method-assign]

    result = adapter.render_cover_image(
        video_path=tmp_path / "demo.mp4",
        output_path=output_path,
        timestamp_sec=1.25,
        title="Main title",
        highlight_text="Hot topic",
        position="top",
    )

    command = captured["command"]
    vf_filter = command[command.index("-vf") + 1]

    assert result == output_path
    assert command[0] == "ffmpeg.exe"
    assert "-frames:v" in command
    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in vf_filter
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black" in vf_filter
    assert "setsar=1" in vf_filter
    assert "y=132" in vf_filter
    assert "y=252" in vf_filter
    assert vf_filter.index("drawtext=") < vf_filter.index("pad=1080:1920")
    assert "box=1" not in vf_filter
    assert "borderw=3" in vf_filter
    assert "borderw=2" in vf_filter
    assert command[-1] == str(output_path)


def test_overlay_picture_in_picture_builds_image_overlay_command(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    output_path = tmp_path / "pip.mp4"

    adapter = FFmpegAdapter.__new__(FFmpegAdapter)
    adapter.ffmpeg_path = "ffmpeg.exe"
    adapter.ffprobe_path = "ffprobe.exe"
    adapter._gpu_video_encoder = None
    adapter._ensure_ffmpeg = lambda: None  # type: ignore[method-assign]
    adapter.probe_duration = lambda media_path: 12.0  # type: ignore[method-assign]
    adapter.probe_video_meta = lambda media_path: {"duration_sec": 12.0, "width": 1080, "height": 1920}  # type: ignore[method-assign]
    adapter._gpu_video_codec_args = lambda: ["-c:v", "h264_nvenc"]  # type: ignore[method-assign]

    def fake_run(
        command: list[str], *, timeout_sec: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["timeout_sec"] = timeout_sec
        return subprocess.CompletedProcess(command, 0, "", "")

    adapter._run = fake_run  # type: ignore[method-assign]

    result = adapter.overlay_picture_in_picture(
        video_path=tmp_path / "base.mp4",
        source_path=tmp_path / "overlay.png",
        output_path=output_path,
        start_sec=2.0,
        end_sec=8.0,
        position="bottom_right",
        scale=0.3,
    )

    command = captured["command"]
    filter_complex = command[command.index("-filter_complex") + 1]

    assert result == output_path
    assert command[0] == "ffmpeg.exe"
    assert "-loop" in command
    assert "scale=576:576:force_original_aspect_ratio=decrease" in filter_complex
    assert "setsar=1" in filter_complex
    assert "main_w-overlay_w-24:main_h-overlay_h-24" in filter_complex
    assert "between(t,2.000,8.000)" in filter_complex
    assert "-map" in command
    assert "[vout]" in command
    assert "0:a?" in command
    assert "-t" in command
    assert "12.000" in command
    assert command[-1] == str(output_path)
    assert captured["timeout_sec"] == 78.0


def test_overlay_picture_in_picture_loops_video_overlay(tmp_path: Path) -> None:
    captured: dict[str, list[str]] = {}
    adapter = FFmpegAdapter.__new__(FFmpegAdapter)
    adapter.ffmpeg_path = "ffmpeg.exe"
    adapter.ffprobe_path = "ffprobe.exe"
    adapter._gpu_video_encoder = None
    adapter._ensure_ffmpeg = lambda: None  # type: ignore[method-assign]
    adapter.probe_duration = lambda media_path: 6.0  # type: ignore[method-assign]
    adapter.probe_video_meta = lambda media_path: {"duration_sec": 6.0, "width": 720, "height": 1280}  # type: ignore[method-assign]
    adapter._gpu_video_codec_args = lambda: ["-c:v", "h264_nvenc"]  # type: ignore[method-assign]
    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, "", "")

    adapter._run = fake_run  # type: ignore[method-assign]

    adapter.overlay_picture_in_picture(
        video_path=tmp_path / "base.mp4",
        source_path=tmp_path / "overlay.mp4",
        output_path=tmp_path / "pip.mp4",
        end_sec=None,
        position="top_left",
        scale=0.25,
        loop=True,
    )

    command = captured["command"]
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "-stream_loop" in command
    assert "scale=320:320:force_original_aspect_ratio=decrease" in filter_complex
    assert "overlay=24:24" in filter_complex
    assert "between(t,0.000,6.000)" in filter_complex


def test_overlay_picture_in_picture_applies_style_and_fade(tmp_path: Path) -> None:
    captured: dict[str, list[str]] = {}
    adapter = FFmpegAdapter.__new__(FFmpegAdapter)
    adapter.ffmpeg_path = "ffmpeg.exe"
    adapter.ffprobe_path = "ffprobe.exe"
    adapter._gpu_video_encoder = None
    adapter._ensure_ffmpeg = lambda: None  # type: ignore[method-assign]
    adapter.probe_duration = lambda media_path: 10.0  # type: ignore[method-assign]
    adapter.probe_video_meta = lambda media_path: {"duration_sec": 10.0, "width": 720, "height": 404}  # type: ignore[method-assign]
    adapter._gpu_video_codec_args = lambda: ["-c:v", "h264_nvenc"]  # type: ignore[method-assign]

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, "", "")

    adapter._run = fake_run  # type: ignore[method-assign]

    adapter.overlay_picture_in_picture(
        video_path=tmp_path / "base.mp4",
        source_path=tmp_path / "overlay.png",
        output_path=tmp_path / "pip.mp4",
        start_sec=1.0,
        end_sec=5.0,
        position="top_right",
        scale=0.3,
        border_width=6,
        border_color="#FFAA00",
        shadow=True,
        opacity=0.75,
        animation="fade",
        fade_duration=0.4,
    )

    command = captured["command"]
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "scale=216:216:force_original_aspect_ratio=decrease" in filter_complex
    assert "drawbox=x=0:y=0:w=iw:h=ih:color=0xFFAA00@1.000:t=6" in filter_complex
    assert "colorchannelmixer=aa=0.750" in filter_complex
    assert "fade=t=in:st=1.000:d=0.400:alpha=1" in filter_complex
    assert "fade=t=out:st=4.600:d=0.400:alpha=1" in filter_complex
    assert "[pipstyled]split[pip][shadow_src]" in filter_complex
    assert "boxblur=8:1[shadow]" in filter_complex
    assert "overlay=main_w-overlay_w-16:32" in filter_complex
    assert "[basepip][pip]overlay=main_w-overlay_w-24:24" in filter_complex


def test_subtitle_filter_clamps_style_for_small_portrait_video() -> None:
    adapter = FFmpegAdapter.__new__(FFmpegAdapter)

    filter_text = adapter._subtitle_filter(
        "D:/tmp/demo.srt",
        SubtitleStyle(font_size=72, bottom_margin=12),
        frame_width=240,
        frame_height=480,
    )

    assert "FontSize=38" in filter_text
    assert "MarginV=45" in filter_text
    assert "MarginL=24" in filter_text
    assert "MarginR=24" in filter_text


def test_subtitle_filter_uses_embedded_ass_style() -> None:
    adapter = FFmpegAdapter.__new__(FFmpegAdapter)

    filter_text = adapter._subtitle_filter(
        "D:/tmp/demo.ass",
        SubtitleStyle(font_size=72, bottom_margin=12),
        frame_width=240,
        frame_height=480,
    )

    assert "force_style" not in filter_text
    assert "demo.ass" in filter_text


def test_prepare_subtitle_file_converts_srt_to_ass_for_templates(tmp_path: Path) -> None:
    adapter = FFmpegAdapter.__new__(FFmpegAdapter)
    srt_path = tmp_path / "demo.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nAI changes daily work.\n",
        encoding="utf-8",
    )

    prepared_path, cleanup_path = adapter._prepare_subtitle_file(
        srt_path,
        tmp_path / "demo.wrapped.srt",
        style=SubtitleStyle(template="short_video", highlight_keywords=["AI"], highlight_color="#FF0000"),
        frame_width=1080,
        frame_height=1920,
        font_size=32,
        side_margin=64,
    )

    assert prepared_path.suffix == ".ass"
    assert cleanup_path == prepared_path
    ass_text = prepared_path.read_text(encoding="utf-8")
    assert r"{\b1" in ass_text
    assert r"\c&H0000FF&" in ass_text


def test_effective_subtitle_style_keeps_reasonable_bottom_margin_for_portrait_video() -> None:
    adapter = FFmpegAdapter.__new__(FFmpegAdapter)

    style, side_margin, outline = adapter._effective_subtitle_style(
        SubtitleStyle(font_size=32, bottom_margin=48),
        frame_width=1080,
        frame_height=1920,
    )

    assert style.font_size == 32
    assert style.bottom_margin == 48
    assert side_margin == 64
    assert outline == 2


def test_effective_subtitle_style_keeps_user_size_distinct_for_narrow_portrait_video() -> None:
    adapter = FFmpegAdapter.__new__(FFmpegAdapter)

    small, _, _ = adapter._effective_subtitle_style(
        SubtitleStyle(font_size=32, bottom_margin=72),
        frame_width=404,
        frame_height=720,
    )
    large, _, _ = adapter._effective_subtitle_style(
        SubtitleStyle(font_size=64, bottom_margin=72),
        frame_width=404,
        frame_height=720,
    )

    assert small.font_size == 32
    assert large.font_size == 64


def test_max_subtitle_chars_per_line_is_not_overly_short_for_portrait_video() -> None:
    adapter = FFmpegAdapter.__new__(FFmpegAdapter)

    max_chars = adapter._max_subtitle_chars_per_line(
        frame_width=1080,
        frame_height=1920,
        font_size=32,
        side_margin=64,
    )

    assert 10 <= max_chars <= 14


def test_wrap_subtitle_text_caps_cjk_lines_to_three() -> None:
    adapter = FFmpegAdapter.__new__(FFmpegAdapter)
    text = "失败的时候也别慌先稳住节奏再把下一步讲清楚这样观众更容易跟上"

    wrapped = adapter._wrap_subtitle_text(text, max_chars_per_line=12)

    lines = wrapped.splitlines()
    assert 2 <= len(lines) <= 3
    assert "".join(lines) == text


def test_rewrap_srt_content_splits_long_cjk_line_for_portrait_video() -> None:
    adapter = FFmpegAdapter.__new__(FFmpegAdapter)
    text = "失败的时候也别慌先稳住节奏再把下一步讲清楚"

    wrapped = adapter._rewrap_srt_content(
        f"1\n00:00:00,000 --> 00:00:03,500\n{text}\n",
        frame_width=432,
        frame_height=768,
        font_size=38,
        side_margin=25,
    )

    text_lines = wrapped.strip().splitlines()[2:]
    assert 2 <= len(text_lines) <= 3
    assert "".join(text_lines) == text
