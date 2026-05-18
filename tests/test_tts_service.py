from pathlib import Path

import pytest

from santiszr.domain.schemas.audio import TTSRequest
from santiszr.domain.services.tts_service import TTSService
from santiszr.infra.tts.voxcpm_client import VoxCPMClient
from conftest import write_test_tone


class FakeTTSClient:
    def __init__(self, notes: list[str] | None = None, provider: str = "voxcpm2") -> None:
        self.notes = notes or []
        self.provider = provider
        self.calls: list[dict[str, object]] = []
        self.shutdown_calls = 0

    def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str | Path,
        *,
        reference_audio_path: str | None = None,
        ultimate_clone: bool = False,
        prompt_text: str | None = None,
        speed: float = 1.0,
        sample_rate: int = 22050,
        speaker: str | None = None,
    ) -> tuple[Path, str, list[str]]:
        output = Path(output_path)
        write_test_tone(output, duration_sec=0.8, sample_rate=sample_rate)
        self.calls.append(
            {
                "text": text,
                "voice": voice,
                "output_path": str(output),
                "reference_audio_path": reference_audio_path,
                "ultimate_clone": ultimate_clone,
                "prompt_text": prompt_text,
                "speed": speed,
                "sample_rate": sample_rate,
                "speaker": speaker,
            }
        )
        return output, self.provider, list(self.notes)

    def shutdown_shared_helper(self) -> None:
        self.shutdown_calls += 1


def test_tts_service_generates_local_audio(temp_workspace: Path) -> None:
    service = TTSService(client=FakeTTSClient())
    result = service.synthesize(
        TTSRequest(
            text="local tts sample",
            voice="reference-clone",
            reference_audio_path=str(temp_workspace / "voice-ref.wav"),
            workspace=str(temp_workspace),
            output_name="tts-test",
        )
    )

    assert result.success is True
    assert result.audio_path
    assert Path(result.audio_path).exists()
    assert result.source_text_path
    assert result.reference_audio_path == str(temp_workspace / "voice-ref.wav")
    assert Path(result.source_text_path).read_text(encoding="utf-8") == "local tts sample"
    assert result.meta is not None
    assert result.meta.duration_sec is not None
    assert result.notes is not None
    assert service.client.calls[0]["reference_audio_path"] == str(temp_workspace / "voice-ref.wav")


def test_tts_service_normal_mode_does_not_require_prompt_text(temp_workspace: Path) -> None:
    client = FakeTTSClient()
    service = TTSService(client=client)

    result = service.synthesize(
        TTSRequest(
            text="normal clone",
            voice="reference-clone",
            reference_audio_path=str(temp_workspace / "voice-ref.wav"),
            workspace=str(temp_workspace),
            output_name="normal",
        )
    )

    assert result.success is True
    assert client.calls[0]["ultimate_clone"] is False
    assert client.calls[0]["prompt_text"] is None


def test_tts_service_persists_text_and_client_notes(temp_workspace: Path) -> None:
    client = FakeTTSClient(notes=["Loaded VoxCPM2 model on CUDA."], provider="voxcpm2")
    service = TTSService(client=client)

    result = service.synthesize(
        TTSRequest(
            text="generate audio then continue pipeline",
            voice="reference-clone",
            reference_audio_path=str(temp_workspace / "clone.wav"),
            workspace=str(temp_workspace),
            output_name="narration",
            speed=1.2,
        )
    )

    assert result.success is True
    assert result.provider == "voxcpm2"
    assert result.notes == ["Loaded VoxCPM2 model on CUDA."]
    assert result.reference_audio_path == str(temp_workspace / "clone.wav")
    assert result.source_text_path == str(temp_workspace / "tts" / "narration.txt")
    assert Path(result.source_text_path).read_text(encoding="utf-8") == "generate audio then continue pipeline"
    assert client.calls[0]["voice"] == "reference-clone"
    assert client.calls[0]["reference_audio_path"] == str(temp_workspace / "clone.wav")


def test_tts_service_passes_ultimate_clone_prompt_text(temp_workspace: Path) -> None:
    client = FakeTTSClient()
    service = TTSService(client=client)

    result = service.synthesize(
        TTSRequest(
            text="target text",
            voice="reference-clone",
            reference_audio_path=str(temp_workspace / "clone.wav"),
            ultimate_clone=True,
            prompt_text="recognized reference transcript",
            workspace=str(temp_workspace),
            output_name="ultimate",
        )
    )

    assert result.success is True
    assert client.calls[0]["ultimate_clone"] is True
    assert client.calls[0]["prompt_text"] == "recognized reference transcript"


def test_tts_service_rejects_ultimate_clone_without_prompt_text(temp_workspace: Path) -> None:
    client = FakeTTSClient()
    service = TTSService(client=client)

    result = service.synthesize(
        TTSRequest(
            text="target text",
            voice="reference-clone",
            reference_audio_path=str(temp_workspace / "reference.wav"),
            ultimate_clone=True,
            workspace=str(temp_workspace),
            output_name="ultimate-missing-prompt",
        )
    )

    assert result.success is False
    assert result.error is not None
    assert "prompt_text" in result.error.message
    assert client.calls == []


def test_tts_service_uses_unique_output_name_when_base_exists(temp_workspace: Path) -> None:
    client = FakeTTSClient()
    service = TTSService(client=client)
    tts_dir = temp_workspace / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    write_test_tone(tts_dir / "custom-name.wav", duration_sec=0.4)
    (tts_dir / "custom-name.txt").write_text("old text", encoding="utf-8")

    result = service.synthesize(
        TTSRequest(
            text="new text for another take",
            voice="reference-clone",
            reference_audio_path=str(temp_workspace / "clone.wav"),
            workspace=str(temp_workspace),
            output_name="custom-name",
        )
    )

    assert result.success is True
    assert result.audio_path is not None
    assert result.source_text_path is not None
    assert Path(result.audio_path).name != "custom-name.wav"
    assert Path(result.source_text_path).name != "custom-name.txt"
    assert "custom-name-" in Path(result.audio_path).stem
    assert Path(result.source_text_path).read_text(encoding="utf-8") == "new text for another take"
    assert client.calls[0]["output_path"] == result.audio_path


def test_tts_service_numbers_studio_narration_outputs(temp_workspace: Path) -> None:
    client = FakeTTSClient()
    service = TTSService(client=client)
    tts_dir = temp_workspace / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    write_test_tone(tts_dir / "1.wav", duration_sec=0.4)
    (tts_dir / "1.txt").write_text("first", encoding="utf-8")
    write_test_tone(tts_dir / "3.wav", duration_sec=0.4)
    (tts_dir / "3.txt").write_text("third", encoding="utf-8")
    write_test_tone(tts_dir / "voice-page.wav", duration_sec=0.4)

    result = service.synthesize(
        TTSRequest(
            text="new numbered take",
            voice="reference-clone",
            reference_audio_path=str(temp_workspace / "clone.wav"),
            workspace=str(temp_workspace),
            output_name="studio-narration",
        )
    )

    assert result.success is True
    assert result.audio_path == str(tts_dir / "4.wav")
    assert result.source_text_path == str(tts_dir / "4.txt")
    assert Path(result.source_text_path).read_text(encoding="utf-8") == "new numbered take"
    assert client.calls[0]["output_path"] == str(tts_dir / "4.wav")


def test_tts_service_release_resources_closes_shared_helper() -> None:
    client = FakeTTSClient()
    service = TTSService(client=client)

    service.release_resources()

    assert client.shutdown_calls == 1


def test_voxcpm_client_reads_helper_python_from_env(
    monkeypatch: pytest.MonkeyPatch,
    temp_workspace: Path,
) -> None:
    client = VoxCPMClient()
    helper_python = temp_workspace / "tools" / "voxcpm_python" / "python.exe"
    helper_python.parent.mkdir(parents=True, exist_ok=True)
    helper_python.write_text("", encoding="utf-8")

    monkeypatch.setenv("SANTISZR_VOXCPM_PYTHON", str(helper_python))

    assert client._helper_python() == helper_python.resolve()
