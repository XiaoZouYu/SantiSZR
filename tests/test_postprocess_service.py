from pathlib import Path

from santiszr.domain.schemas.postprocess import (
    BGMSelection,
    CoverRequest,
    CoverStyle,
    PictureInPictureRequest,
    PostProcessRequest,
)
from santiszr.domain.schemas.subtitle import SubtitleStyle
from santiszr.domain.services.postprocess_service import PostProcessService


class FakeFFmpegAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def burn_subtitles(
        self,
        video_path: str | Path,
        subtitle_path: str | Path,
        output_path: str | Path,
        style: SubtitleStyle | None = None,
    ) -> Path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"subtitle-video")
        self.calls.append(
            (
                "subtitle",
                {
                    "video_path": str(video_path),
                    "subtitle_path": str(subtitle_path),
                    "output_path": str(target),
                    "style": style,
                },
            )
        )
        return target

    def overlay_picture_in_picture(
        self,
        video_path: str | Path,
        source_path: str | Path,
        output_path: str | Path,
        *,
        start_sec: float = 0.0,
        end_sec: float | None = None,
        position: str = "top_right",
        scale: float = 0.28,
        border_width: int = 0,
        border_color: str = "#FFFFFF",
        shadow: bool = False,
        opacity: float = 1.0,
        animation: str = "none",
        fade_duration: float = 0.35,
        loop: bool = True,
        mute: bool = True,
    ) -> Path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"pip-video")
        self.calls.append(
            (
                "pip",
                {
                    "video_path": str(video_path),
                    "source_path": str(source_path),
                    "output_path": str(target),
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "position": position,
                    "scale": scale,
                    "border_width": border_width,
                    "border_color": border_color,
                    "shadow": shadow,
                    "opacity": opacity,
                    "animation": animation,
                    "fade_duration": fade_duration,
                    "loop": loop,
                    "mute": mute,
                },
            )
        )
        return target

    def mix_background_music(
        self,
        video_path: str | Path,
        bgm_path: str | Path,
        output_path: str | Path,
        *,
        bgm_volume: float = 0.2,
    ) -> Path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"bgm-video")
        self.calls.append(
            (
                "bgm",
                {
                    "video_path": str(video_path),
                    "bgm_path": str(bgm_path),
                    "output_path": str(target),
                    "bgm_volume": bgm_volume,
                },
            )
        )
        return target

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
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"cover-image")
        self.calls.append(
            (
                "cover",
                {
                    "video_path": str(video_path),
                    "output_path": str(target),
                    "timestamp_sec": timestamp_sec,
                    "title": title,
                    "highlight_text": highlight_text,
                    "font_name": font_name,
                    "font_size": font_size,
                    "font_color": font_color,
                    "highlight_color": highlight_color,
                    "position": position,
                },
            )
        )
        return target

    def probe_duration(self, media_path: str | Path) -> float:
        return 8.0


def test_postprocess_service_runs_subtitle_bgm_and_cover_in_order(temp_workspace: Path) -> None:
    ffmpeg = FakeFFmpegAdapter()
    service = PostProcessService(ffmpeg=ffmpeg)

    video_path = temp_workspace / "avatar" / "result.mp4"
    subtitle_path = temp_workspace / "subtitle" / "result.srt"
    bgm_dir = temp_workspace / "assets" / "bgm"
    bgm_file = bgm_dir / "calm.mp3"

    video_path.parent.mkdir(parents=True, exist_ok=True)
    subtitle_path.parent.mkdir(parents=True, exist_ok=True)
    bgm_dir.mkdir(parents=True, exist_ok=True)

    video_path.write_bytes(b"video")
    subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    bgm_file.write_bytes(b"bgm")

    result = service.process(
        PostProcessRequest(
            video_path=str(video_path),
            subtitle_path=str(subtitle_path),
            burn_subtitles=True,
            subtitle_style=SubtitleStyle(font_name="DemoFont", font_size=28, bottom_margin=48),
            bgm=BGMSelection(bgm_directory=str(bgm_dir), random_choice=True, volume=0.35),
            cover=CoverRequest(
                enabled=True,
                title="Main title",
                highlight_text="Hot topic",
                style=CoverStyle(font_name="CoverFont", font_size=52, position="top"),
            ),
            workspace=str(temp_workspace),
            output_name="demo-post",
        )
    )

    assert result.success is True
    assert result.steps_applied == ["subtitle", "bgm", "cover"]
    assert result.subtitle_video_path == str(temp_workspace / "postprocess" / "demo-post_subtitle.mp4")
    assert result.bgm_video_path == str(temp_workspace / "postprocess" / "demo-post_bgm.mp4")
    assert result.final_video_path == result.bgm_video_path
    assert result.cover_image_path == str(temp_workspace / "postprocess" / "demo-post_cover.jpg")
    assert result.cover_source_path == str(video_path.resolve())
    assert result.bgm_source_path == str(bgm_file.resolve())
    assert [name for name, _ in ffmpeg.calls] == ["subtitle", "bgm", "cover"]
    assert ffmpeg.calls[1][1]["video_path"] == str(temp_workspace / "postprocess" / "demo-post_subtitle.mp4")
    assert ffmpeg.calls[1][1]["bgm_volume"] == 0.35
    assert ffmpeg.calls[2][1]["timestamp_sec"] == 4.0
    assert any("random BGM" in note for note in result.notes)


def test_postprocess_service_runs_picture_in_picture_before_subtitles(temp_workspace: Path) -> None:
    ffmpeg = FakeFFmpegAdapter()
    service = PostProcessService(ffmpeg=ffmpeg)

    video_path = temp_workspace / "avatar" / "result.mp4"
    pip_path = temp_workspace / "uploads" / "image" / "chart.png"
    subtitle_path = temp_workspace / "subtitle" / "result.srt"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    pip_path.parent.mkdir(parents=True, exist_ok=True)
    subtitle_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")
    pip_path.write_bytes(b"pip")
    subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")

    result = service.process(
        PostProcessRequest(
            video_path=str(video_path),
            picture_in_picture=PictureInPictureRequest(
                enabled=True,
                source_path=str(pip_path),
                start_sec=2.0,
                end_sec=6.5,
                position="bottom_right",
                scale=0.32,
                border_width=4,
                border_color="#FF0000",
                shadow=True,
                opacity=0.82,
                animation="fade",
                fade_duration=0.5,
            ),
            subtitle_path=str(subtitle_path),
            burn_subtitles=True,
            workspace=str(temp_workspace),
            output_name="demo-pip",
        )
    )

    assert result.success is True
    assert result.steps_applied == ["pip", "subtitle"]
    assert result.pip_video_path == str(temp_workspace / "postprocess" / "demo-pip_pip.mp4")
    assert result.subtitle_video_path == str(temp_workspace / "postprocess" / "demo-pip_subtitle.mp4")
    assert result.final_video_path == result.subtitle_video_path
    assert result.pip_source_path == str(pip_path.resolve())
    assert [name for name, _ in ffmpeg.calls] == ["pip", "subtitle"]
    assert ffmpeg.calls[0][1]["border_width"] == 4
    assert ffmpeg.calls[0][1]["border_color"] == "#FF0000"
    assert ffmpeg.calls[0][1]["shadow"] is True
    assert ffmpeg.calls[0][1]["opacity"] == 0.82
    assert ffmpeg.calls[0][1]["animation"] == "fade"
    assert ffmpeg.calls[0][1]["fade_duration"] == 0.5
    assert ffmpeg.calls[1][1]["video_path"] == str(temp_workspace / "postprocess" / "demo-pip_pip.mp4")


def test_postprocess_service_selects_named_bgm_and_supports_custom_cover_source(temp_workspace: Path) -> None:
    ffmpeg = FakeFFmpegAdapter()
    service = PostProcessService(ffmpeg=ffmpeg)

    video_path = temp_workspace / "avatar" / "result.mp4"
    clean_video_path = temp_workspace / "avatar" / "clean.mp4"
    bgm_dir = temp_workspace / "assets" / "bgm"
    bgm_file = bgm_dir / "focus-track.wav"

    video_path.parent.mkdir(parents=True, exist_ok=True)
    bgm_dir.mkdir(parents=True, exist_ok=True)

    video_path.write_bytes(b"video")
    clean_video_path.write_bytes(b"clean-video")
    bgm_file.write_bytes(b"bgm")

    result = service.process(
        PostProcessRequest(
            video_path=str(video_path),
            bgm=BGMSelection(bgm_directory=str(bgm_dir), bgm_name="focus-track", volume=0.2),
            cover=CoverRequest(
                enabled=True,
                source_video_path=str(clean_video_path),
                timestamp_sec=1.25,
                output_name="custom-cover",
            ),
            workspace=str(temp_workspace),
            output_name="named-bgm",
        )
    )

    assert result.success is True
    assert result.steps_applied == ["bgm", "cover"]
    assert result.bgm_source_path == str(bgm_file.resolve())
    assert result.cover_source_path == str(clean_video_path.resolve())
    assert result.cover_image_path == str(temp_workspace / "postprocess" / "custom-cover.jpg")
    assert ffmpeg.calls[0][0] == "bgm"
    assert ffmpeg.calls[1][0] == "cover"
    assert ffmpeg.calls[1][1]["timestamp_sec"] == 1.25


def test_postprocess_service_returns_original_video_when_no_step_is_enabled(temp_workspace: Path) -> None:
    service = PostProcessService(ffmpeg=FakeFFmpegAdapter())
    video_path = temp_workspace / "avatar" / "result.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    result = service.process(
        PostProcessRequest(
            video_path=str(video_path),
            workspace=str(temp_workspace),
        )
    )

    assert result.success is True
    assert result.final_video_path == str(video_path.resolve())
    assert result.steps_applied == []
    assert result.notes == ["No postprocess step was applied."]
