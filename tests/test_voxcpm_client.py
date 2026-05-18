from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from santiszr.infra.tts.voxcpm_client import VoxCPMClient
from conftest import write_test_tone


class _FakeProcess:
    def __init__(self, response: dict[str, object]) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(json.dumps(response, ensure_ascii=False) + "\n")


def test_voxcpm_client_requires_reference_audio_before_helper(
    monkeypatch: pytest.MonkeyPatch,
    temp_workspace: Path,
) -> None:
    client = VoxCPMClient(model_dir=temp_workspace)

    monkeypatch.setattr(
        client,
        "_ensure_helper_process",
        lambda: (_ for _ in ()).throw(AssertionError("helper should not be started")),
    )

    with pytest.raises(RuntimeError, match="reference audio"):
        client.synthesize(
            text="hello",
            voice="reference-clone",
            output_path=temp_workspace / "tts" / "demo.wav",
            reference_audio_path=None,
        )


def test_voxcpm_client_fails_when_helper_runtime_is_missing(temp_workspace: Path) -> None:
    reference_audio = write_test_tone(temp_workspace / "ref.wav")
    client = VoxCPMClient(model_dir=temp_workspace)
    client._helper_python = lambda: None  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="helper runtime is missing"):
        client.synthesize(
            text="hello",
            voice="reference-clone",
            output_path=temp_workspace / "tts" / "demo.wav",
            reference_audio_path=reference_audio,
        )


def test_voxcpm_client_sends_request_to_helper_and_returns_notes(
    monkeypatch: pytest.MonkeyPatch,
    temp_workspace: Path,
) -> None:
    reference_audio = write_test_tone(temp_workspace / "ref.wav")
    client = VoxCPMClient(model_dir=temp_workspace)
    process = _FakeProcess(
        {
            "ok": True,
            "audio_path": str(temp_workspace / "tts" / "demo.wav"),
            "provider": "voxcpm2",
            "notes": ["Loaded VoxCPM2 model on CUDA.", "Reference cache hit: ref.wav"],
        }
    )
    logged: list[str] = []

    monkeypatch.setattr(client, "_ensure_helper_process", lambda: process)
    monkeypatch.setattr(client, "_log_runtime", lambda message: logged.append(message))

    audio_path, provider, notes = client.synthesize(
        text="hello world",
        voice="reference-clone",
        output_path=temp_workspace / "tts" / "demo.wav",
        reference_audio_path=reference_audio,
        sample_rate=48000,
    )

    payload = json.loads(process.stdin.getvalue().strip())
    assert payload["text"] == "hello world"
    assert payload["voice"] == "reference-clone"
    assert payload["reference_audio_path"] == str(reference_audio.resolve())
    assert payload["ultimate_clone"] is False
    assert "prompt_text" not in payload
    assert audio_path == (temp_workspace / "tts" / "demo.wav").resolve()
    assert provider == "voxcpm2"
    assert notes == ["Loaded VoxCPM2 model on CUDA.", "Reference cache hit: ref.wav"]
    assert logged == notes


def test_voxcpm_client_sends_ultimate_clone_payload(
    monkeypatch: pytest.MonkeyPatch,
    temp_workspace: Path,
) -> None:
    reference_audio = write_test_tone(temp_workspace / "ref.wav")
    client = VoxCPMClient(model_dir=temp_workspace)
    process = _FakeProcess(
        {
            "ok": True,
            "audio_path": str(temp_workspace / "tts" / "demo.wav"),
            "provider": "voxcpm2",
            "notes": [],
        }
    )

    monkeypatch.setattr(client, "_ensure_helper_process", lambda: process)

    client.synthesize(
        text="hello world",
        voice="reference-clone",
        output_path=temp_workspace / "tts" / "demo.wav",
        reference_audio_path=reference_audio,
        ultimate_clone=True,
        prompt_text="recognized transcript",
    )

    payload = json.loads(process.stdin.getvalue().strip())
    assert payload["ultimate_clone"] is True
    assert payload["prompt_text"] == "recognized transcript"
