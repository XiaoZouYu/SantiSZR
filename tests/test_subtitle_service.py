from pathlib import Path

import pytest

from santiszr.domain.schemas.subtitle import SubtitleRequest, SubtitleStyle
from santiszr.domain.services.subtitle_service import SubtitleService


class FakeSubtitleGenerator:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[tuple[str, str]] = []

    def available(self) -> bool:
        return True

    def generate(self, audio_path: str | Path, output_path: str | Path) -> tuple[Path, list[str]]:
        self.calls.append((str(audio_path), str(output_path)))
        if self.should_fail:
            raise RuntimeError("generator failed")
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "1\n00:00:00,000 --> 00:00:00,500\n很多企业营销失败，第一步就卡在客户\n\n"
            "2\n00:00:00,500 --> 00:00:01,000\n搜不到你，或搜到的是负面内容\n",
            encoding="utf-8",
        )
        return target, ["Generated subtitle via external script."]


class FakeSubtitleCorrector:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def correct_file(self, srt_path: str | Path) -> tuple[bool, list[str]]:
        self.calls.append(str(srt_path))
        path = Path(srt_path)
        path.write_text(
            "1\n00:00:00,000 --> 00:00:00,500\n修正后的第一句\n\n"
            "2\n00:00:00,500 --> 00:00:01,000\n修正后的第二句\n",
            encoding="utf-8",
        )
        return True, ["Subtitle corrected via LLM."]


def test_subtitle_service_generates_srt_and_video(
    sample_audio: Path,
    temp_workspace: Path,
    ffmpeg_adapter,
) -> None:
    try:
        ffmpeg_adapter._resolve_gpu_video_encoder()
    except Exception:
        pytest.skip("A usable GPU video encoder is required for subtitle burn-in video generation.")

    service = SubtitleService(ffmpeg=ffmpeg_adapter, generator=FakeSubtitleGenerator())
    result = service.generate(
        SubtitleRequest(
            audio_path=str(sample_audio),
            reference_text="很多企业营销失败，第一步就卡在客户搜不到你，或搜到的是负面内容。直接导致客户信任为零。",
            burn_in=True,
            workspace=str(temp_workspace),
            output_name="subtitle-test",
        )
    )

    assert result.success is True
    assert result.srt_path
    assert result.burned_video_path
    assert Path(result.srt_path).exists()
    assert Path(result.burned_video_path).exists()
    assert result.generated_by == "script"
    assert any("reference text" in note for note in result.notes)
    assert [segment.text for segment in result.segments] == [
        "很多企业营销失败，",
        "第一步就卡在客户搜不到你，",
        "或搜到的是负面内容。",
        "直接导致客户信任为零。",
    ]


def test_subtitle_service_falls_back_to_heuristic_when_generator_fails(sample_audio: Path, temp_workspace: Path) -> None:
    service = SubtitleService(generator=FakeSubtitleGenerator(should_fail=True))
    result = service.generate(
        SubtitleRequest(
            audio_path=str(sample_audio),
            reference_text="这是第一句。这是第二句。这是第三句。",
            burn_in=False,
            workspace=str(temp_workspace),
            output_name="subtitle-fallback",
        )
    )

    assert result.success is True
    assert result.srt_path
    assert Path(result.srt_path).exists()
    assert result.generated_by == "heuristic"
    assert any("fallback" in note for note in result.notes)
    assert any("reference text" in note for note in result.notes)
    assert len(result.segments) == 3


def test_subtitle_reference_text_without_punctuation_is_split_into_short_units() -> None:
    service = SubtitleService()
    reference_text = (
        "\u4eba\u5de5\u667a\u80fd\u7684\u672a\u6765\u4e4b\u8def\u662f\u4ec0\u4e48 "
        "\u672a\u6765\u4e0d\u662f\u5c5e\u4e8e\u4eba\u5de5\u667a\u80fd "
        "\u800c\u662f\u5c5e\u4e8e\u638c\u63e1\u4e86\u4eba\u5de5\u667a\u80fd\u7684\u4eba "
        "\u6211\u4eec\u9700\u8981\u7528\u66f4\u6e05\u6670\u7684\u65b9\u5f0f\u8868\u8fbe\u89c2\u70b9"
    )

    units = service._split_reference_units(reference_text)

    assert len(units) > 1
    assert all(service._unit_length(unit) <= 20 for unit in units)


def test_subtitle_service_writes_ass_with_keyword_highlight(sample_audio: Path, temp_workspace: Path) -> None:
    service = SubtitleService(generator=FakeSubtitleGenerator())
    result = service.generate(
        SubtitleRequest(
            audio_path=str(sample_audio),
            reference_text="AI helps teams move faster. AI improves daily work.",
            burn_in=False,
            workspace=str(temp_workspace),
            output_name="subtitle-ass",
            style=SubtitleStyle(
                template="short_video",
                highlight_keywords=["AI"],
                highlight_color="#FF0000",
            ),
        )
    )

    assert result.success is True
    assert result.srt_path
    assert result.ass_path
    assert Path(result.srt_path).exists()
    assert Path(result.ass_path).exists()
    ass_text = Path(result.ass_path).read_text(encoding="utf-8")
    assert r"{\b1" in ass_text
    assert r"\c&H0000FF&" in ass_text
    assert "AI" in ass_text


def test_subtitle_service_can_correct_generated_srt(sample_audio: Path, temp_workspace: Path) -> None:
    corrector = FakeSubtitleCorrector()
    service = SubtitleService(generator=FakeSubtitleGenerator(), corrector=corrector)
    result = service.generate(
        SubtitleRequest(
            audio_path=str(sample_audio),
            reference_text="第一句字幕。第二句字幕。",
            burn_in=False,
            workspace=str(temp_workspace),
            output_name="subtitle-correct",
            correct_with_ai=True,
        )
    )

    assert result.success is True
    assert result.corrected is True
    assert "修正后的第一句" in (result.subtitle_text or "")
    assert corrector.calls == [str(temp_workspace / "subtitle" / "subtitle-correct.srt")]
