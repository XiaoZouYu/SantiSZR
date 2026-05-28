from pathlib import Path

from santiszr.domain.schemas.avatar import AvatarEngine, AvatarRequest
from santiszr.domain.schemas.subtitle import SubtitleStyle
from santiszr.domain.services.avatar_service import AvatarService
from santiszr.infra.avatar.tuilionnx import TuiliOnnxAdapter


class FakeFFmpegAdapter:
    def probe_duration(self, media_path: str | Path) -> float:
        del media_path
        return 0.8


class FakeTuiliOnnxAdapter:
    def __init__(self) -> None:
        self.render_calls: list[dict[str, object]] = []

    def render(self, **kwargs):  # noqa: ANN003
        output_path = Path(str(kwargs["output_path"]))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-video")
        self.render_calls.append(dict(kwargs))
        return output_path, Path(str(kwargs["background_video_path"])).resolve(), [
            "Using uploaded reference video: sample.mp4",
            "Rendered lip-sync video with encoder h264_nvenc.",
        ]


def test_avatar_service_renders_with_uploaded_reference_video(
    sample_audio: Path,
    sample_video: Path,
    temp_workspace: Path,
) -> None:
    ffmpeg = FakeFFmpegAdapter()
    adapter = FakeTuiliOnnxAdapter()
    subtitle_path = temp_workspace / "subtitle" / "avatar-subtitle.srt"
    subtitle_path.parent.mkdir(parents=True, exist_ok=True)
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:00,800\n数字人口播测试\n",
        encoding="utf-8",
    )

    service = AvatarService(tuilionnx=adapter, ffmpeg=ffmpeg)
    result = service.render(
        AvatarRequest(
            audio_path=str(sample_audio),
            model_id="uploaded-avatar",
            engine=AvatarEngine.tuilionnx,
            workspace=str(temp_workspace),
            subtitle_path=str(subtitle_path),
            subtitle_style=SubtitleStyle(font_size=28, bottom_margin=84),
            reference_video_path=str(sample_video),
            overlay_text="avatar-test",
            quality_preset="hd",
            max_reference_edge=0,
        )
    )

    assert result.success is True
    assert result.video_path
    assert Path(result.video_path).exists()
    assert result.model_asset_path == str(sample_video.resolve())
    assert result.engine_used == "tuilionnx-lipsync"
    assert any("reference video" in note for note in result.notes)
    assert adapter.render_calls[0]["background_video_path"] == str(sample_video)
    assert adapter.render_calls[0]["subtitle_style"].font_size == 28
    assert adapter.render_calls[0]["subtitle_style"].bottom_margin == 84
    assert adapter.render_calls[0]["quality_preset"] == "hd"
    assert adapter.render_calls[0]["max_reference_edge"] == 0
    assert Path(str(adapter.render_calls[0]["output_path"])).name == f"{sample_video.stem}_tuilionnx.mp4"


def test_avatar_service_renders_same_reference_video_to_unique_output_paths(
    sample_audio: Path,
    sample_video: Path,
    temp_workspace: Path,
) -> None:
    service = AvatarService(tuilionnx=FakeTuiliOnnxAdapter(), ffmpeg=FakeFFmpegAdapter())
    request = AvatarRequest(
        audio_path=str(sample_audio),
        model_id="uploaded-avatar",
        engine=AvatarEngine.tuilionnx,
        workspace=str(temp_workspace),
        reference_video_path=str(sample_video),
    )

    first = service.render(request)
    second = service.render(request)

    assert first.success is True
    assert second.success is True
    assert first.video_path is not None
    assert second.video_path is not None
    assert Path(first.video_path).exists()
    assert Path(second.video_path).exists()
    assert Path(first.video_path).name == f"{sample_video.stem}_tuilionnx.mp4"
    assert Path(second.video_path).name == f"{sample_video.stem}_tuilionnx-2.mp4"
    assert first.video_path != second.video_path


def test_avatar_service_requires_reference_video(
    sample_audio: Path,
    temp_workspace: Path,
) -> None:
    service = AvatarService(tuilionnx=FakeTuiliOnnxAdapter(), ffmpeg=FakeFFmpegAdapter())
    result = service.render(
        AvatarRequest(
            audio_path=str(sample_audio),
            model_id="uploaded-avatar",
            engine=AvatarEngine.tuilionnx,
            workspace=str(temp_workspace),
        )
    )

    assert result.success is False
    assert result.error is not None
    assert "uploaded reference video" in result.error.message


def test_tuilionnx_adapter_accepts_only_project_local_configuration(temp_workspace: Path) -> None:
    model_root = temp_workspace / "models" / "tuilionnx"
    helper_python = temp_workspace / "tools" / "cosyvoice_python" / "python.exe"
    helper_python.parent.mkdir(parents=True, exist_ok=True)
    helper_python.write_text("", encoding="utf-8")

    adapter = TuiliOnnxAdapter(
        ffmpeg=FakeFFmpegAdapter(),
        model_root=model_root,
        helper_python=helper_python,
    )

    assert adapter.model_root == model_root.resolve()
    assert adapter.helper_python == helper_python.resolve()
    assert adapter.list_models() == []
    assert adapter.resolve_model_asset("uploaded-avatar") is None


def test_avatar_service_default_uses_tuilionnx_adapter() -> None:
    service = AvatarService(ffmpeg=FakeFFmpegAdapter())

    assert isinstance(service.tuilionnx, TuiliOnnxAdapter)
