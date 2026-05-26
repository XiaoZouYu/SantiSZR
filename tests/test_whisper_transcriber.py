from __future__ import annotations

import os
from pathlib import Path

import pytest

from santiszr.infra.transcription.whisper import WhisperTranscriber


def test_whisper_transcriber_rejects_cpu_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANTISZR_WHISPER_DEVICE", "cpu")

    transcriber = WhisperTranscriber()

    with pytest.raises(RuntimeError, match="CPU mode is disabled"):
        transcriber._resolve_runtime()


def test_whisper_transcriber_requires_gpu_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANTISZR_WHISPER_DEVICE", "cuda")

    transcriber = WhisperTranscriber()
    monkeypatch.setattr(transcriber, "_cuda_runtime_ready", lambda: False)

    with pytest.raises(RuntimeError, match="GPU runtime is unavailable"):
        transcriber._resolve_runtime()


def test_whisper_transcriber_ctranslate2_runtime_exposes_models() -> None:
    transcriber = WhisperTranscriber()

    ctranslate2 = transcriber._ensure_ctranslate2_runtime()

    assert hasattr(ctranslate2, "models")
    assert hasattr(ctranslate2.models, "Whisper")


def test_whisper_windows_dll_search_does_not_mutate_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", r"C:\base\path")
    monkeypatch.delenv("CUDA_PATH", raising=False)
    added_paths: list[str] = []

    transcriber = WhisperTranscriber()
    monkeypatch.setattr(transcriber, "_bundled_cuda_bin_dir", lambda: tmp_path)
    monkeypatch.setattr(os, "add_dll_directory", lambda path: added_paths.append(path) or object(), raising=False)

    transcriber._configure_windows_dll_search()

    assert os.environ["PATH"] == r"C:\base\path"
    assert added_paths == [str(tmp_path.resolve())]
