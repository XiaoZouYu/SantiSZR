from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from santiszr.infra.subtitle.corrector import SubtitleCorrector


class FakeLLMClient:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.prompts: list[str] = []

    def is_configured(self) -> bool:
        return True

    def generate(self, prompt: str, *, system_prompt: str, temperature: float):  # noqa: ANN001
        self.prompts.append(f"{system_prompt}\n{prompt}")
        return SimpleNamespace(text=self.response_text)


def test_subtitle_corrector_strips_trailing_punctuation_per_entry(tmp_path: Path) -> None:
    srt_path = tmp_path / "demo.srt"
    srt_path.write_text(
        "\n".join(
            [
                "1",
                "00:00:00,000 --> 00:00:01,840",
                "\u5927\u5bb6\u597d\uff0c\u6211\u662f\u674e\u5fd7\u9e3f\uff0c",
                "",
                "2",
                "00:00:01,840 --> 00:00:05,521",
                "\u4e00\u540d\u9000\u4f0d\u519b\u4eba\u3002",
                "",
            ]
        ),
        encoding="utf-8",
    )

    corrector = SubtitleCorrector(
        llm_client=FakeLLMClient(
            "\u5927\u5bb6\u597d\uff0c\u6211\u662f\u674e\u5fd7\u9e3f\uff0c\n"
            "\u4e00\u540d\u9000\u4f0d\u519b\u4eba\u3002"
        )
    )

    corrected, _ = corrector.correct_file(srt_path)

    assert corrected is True
    assert "\u5927\u5bb6\u597d\uff0c\u6211\u662f\u674e\u5fd7\u9e3f\n" in srt_path.read_text(encoding="utf-8")
    assert "\u4e00\u540d\u9000\u4f0d\u519b\u4eba\n" in srt_path.read_text(encoding="utf-8")
    assert "\uff0c\n\n2" not in srt_path.read_text(encoding="utf-8")
    assert "\u3002\n" not in srt_path.read_text(encoding="utf-8")
    assert "without punctuation" in corrector.llm_client.prompts[0]
