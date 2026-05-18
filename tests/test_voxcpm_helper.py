from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from santiszr.infra.tts.voxcpm_helper import (
    VoxCPMRuntime,
    _InferenceProfile,
    _PreparedReferenceAudio,
    _split_synthesis_text,
)


def test_split_synthesis_text_keeps_short_text_intact() -> None:
    text = "这是一段短文案。"

    assert _split_synthesis_text(text, max_chars=40) == [text]


def test_split_synthesis_text_breaks_long_multiline_copy_into_chunks() -> None:
    text = (
        "很多企业营销失败，第一步就卡在客户搜不到你，或搜到的是负面、无内容，直接导致客户信任为 0。"
        "而 GEO 正是破解这一痛点的核心，它并非单纯的流量入口，而是帮企业在各类 AI 平台建立被精准查到的能力。\n"
        "简单来说，GEO 就像给生意装上精准导航，当有精准需求的客户在 AI 平台搜索相关产品或服务时，"
        "可以让你的信息优先匹配并精准呈现，先让客户看到你，再谈后续转化。"
    )

    chunks = _split_synthesis_text(text, max_chars=80)

    assert len(chunks) >= 2
    assert all(chunk.strip() for chunk in chunks)
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace("\n", "").replace(" ", "")


class _FakePromptModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def build_prompt_cache(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"cache": len(self.calls)}


class _FakeWaveform:
    def __init__(self) -> None:
        self.ndim = 1

    def unsqueeze(self, _dim: int) -> "_FakeWaveform":
        self.ndim = 2
        return self

    def to(self, _dtype: object) -> "_FakeWaveform":
        return self

    def cpu(self) -> "_FakeWaveform":
        return self


class _FakeGenerateModel:
    sample_rate = 16000

    def __init__(self) -> None:
        self.generate_calls: list[dict[str, object]] = []
        self.build_prompt_cache_calls = 0
        self.generate_with_prompt_cache_calls = 0

    def text_tokenizer(self, text: str) -> list[str]:
        return list(text)

    def generate(self, **kwargs: object) -> _FakeWaveform:
        self.generate_calls.append(kwargs)
        return _FakeWaveform()

    def build_prompt_cache(self, **_kwargs: object) -> dict[str, object]:
        self.build_prompt_cache_calls += 1
        raise AssertionError("ultimate clone must not build a prompt cache")

    def generate_with_prompt_cache(self, **_kwargs: object) -> _FakeWaveform:
        self.generate_with_prompt_cache_calls += 1
        raise AssertionError("ultimate clone must not use prompt cache generation")


def _runtime_for_prompt_cache(workspace: Path, prepared_path: Path) -> VoxCPMRuntime:
    runtime = VoxCPMRuntime.__new__(VoxCPMRuntime)
    runtime.profile = _InferenceProfile(cfg_value=2.0, inference_timesteps=10, retry_badcase=True)
    runtime.model_dir = workspace
    runtime.reference_max_seconds = 12.0
    runtime._prompt_caches = {}
    prepared = _PreparedReferenceAudio(
        source_path=prepared_path,
        prepared_path=prepared_path,
        original_duration_sec=1.0,
        prepared_duration_sec=1.0,
        clipped=False,
    )
    runtime._prepare_reference_audio = lambda _path: (prepared, [])  # type: ignore[method-assign]
    return runtime


def test_voxcpm_prompt_cache_uses_reference_only_by_default(temp_workspace: Path) -> None:
    reference_audio = temp_workspace / "ref.wav"
    reference_audio.write_bytes(b"ref")
    runtime = _runtime_for_prompt_cache(temp_workspace, reference_audio)
    model = _FakePromptModel()

    cache, notes = runtime._get_prompt_cache(model, reference_audio)

    assert cache == {"cache": 1}
    assert model.calls == [
        {
            "reference_wav_path": str(reference_audio),
            "trim_silence_vad": True,
        }
    ]
    assert notes == ["Built reference cache: ref.wav"]


def test_voxcpm_synthesize_requires_prompt_text_for_ultimate_clone(temp_workspace: Path) -> None:
    reference_audio = temp_workspace / "ref.wav"
    reference_audio.write_bytes(b"ref")
    runtime = _runtime_for_prompt_cache(temp_workspace, reference_audio)

    with pytest.raises(RuntimeError, match="requires prompt_text"):
        runtime.synthesize(
            {
                "text": "hello",
                "output_path": str(temp_workspace / "out.wav"),
                "reference_audio_path": str(reference_audio),
                "ultimate_clone": True,
            }
        )


def test_voxcpm_synthesize_ultimate_clone_uses_direct_generate(
    monkeypatch: pytest.MonkeyPatch,
    temp_workspace: Path,
) -> None:
    reference_audio = temp_workspace / "ref.wav"
    reference_audio.write_bytes(b"ref")
    output_path = temp_workspace / "out.wav"
    runtime = _runtime_for_prompt_cache(temp_workspace, reference_audio)
    model = _FakeGenerateModel()
    saved: list[tuple[str, object, int]] = []

    fake_torch = SimpleNamespace(
        float32="float32",
        cuda=SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None),
    )
    fake_torchaudio = SimpleNamespace(save=lambda path, waveform, sample_rate: saved.append((path, waveform, sample_rate)))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "torchaudio", fake_torchaudio)
    runtime._load_model = lambda: (model, [])  # type: ignore[method-assign]

    audio_path, provider, notes = runtime.synthesize(
        {
            "text": "welcome target text",
            "output_path": str(output_path),
            "reference_audio_path": str(reference_audio),
            "ultimate_clone": True,
            "prompt_text": "reference transcript should only condition generation",
        }
    )

    assert audio_path == output_path.resolve()
    assert provider == "voxcpm2"
    assert "Using direct VoxCPM2 generate() with full reference audio for precise matching." in notes
    assert model.build_prompt_cache_calls == 0
    assert model.generate_with_prompt_cache_calls == 0
    assert len(model.generate_calls) == 1
    call = model.generate_calls[0]
    assert call["target_text"] == "welcome target text"
    assert call["prompt_text"] == "reference transcript should only condition generation"
    assert call["reference_wav_path"] == str(reference_audio.resolve())
    assert call["prompt_wav_path"] == str(reference_audio.resolve())
    assert call["retry_badcase"] is False
    assert len(saved) == 1
    assert saved[0][0] == str(output_path.resolve())
    assert saved[0][2] == 16000
