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


def test_whisper_transcriber_resolves_direct_local_model(tmp_path: Path) -> None:
    model_dir = tmp_path / "whisper"
    local_model = model_dir / "small"
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        path = local_model / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")

    transcriber = WhisperTranscriber(model_dir=model_dir)

    assert transcriber._resolve_model_source(device="cuda") == str(local_model.resolve())


def test_whisper_transcriber_resolves_huggingface_cache_model(tmp_path: Path) -> None:
    model_dir = tmp_path / "whisper"
    snapshot = model_dir / "models--Systran--faster-whisper-small" / "snapshots" / "abc123"
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        path = snapshot / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")
    ref_path = model_dir / "models--Systran--faster-whisper-small" / "refs" / "main"
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text("abc123", encoding="utf-8")

    transcriber = WhisperTranscriber(model_dir=model_dir)

    assert transcriber._resolve_model_source(device="cuda") == str(snapshot.resolve())


def test_whisper_transcriber_rejects_missing_local_model(tmp_path: Path) -> None:
    transcriber = WhisperTranscriber(model_dir=tmp_path / "whisper")

    with pytest.raises(RuntimeError, match="install-windows-prereqs"):
        transcriber._resolve_model_source(device="cuda")


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
