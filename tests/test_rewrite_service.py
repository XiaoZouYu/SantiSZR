from pathlib import Path

from santiszr.domain.schemas.audio import RewriteMode, RewriteRequest
from santiszr.domain.services.rewrite_service import RewriteService
from santiszr.infra.llm.client import LLMResponse


class FakeLLMClient:
    def __init__(self, text: str, *, configured: bool = True, should_raise: bool = False) -> None:
        self.text = text
        self.configured = configured
        self.should_raise = should_raise
        self.calls: list[dict[str, object]] = []

    def is_configured(self) -> bool:
        return self.configured

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.calls.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "model": model,
                "temperature": temperature,
            }
        )
        if self.should_raise:
            raise RuntimeError("upstream llm failure")
        return LLMResponse(text=self.text, provider="fake", model=model or "fake-model")


def test_rewrite_service_heuristic_correct_persists_workspace_files(temp_workspace: Path) -> None:
    service = RewriteService(llm_client=FakeLLMClient("", configured=False))

    result = service.rewrite(
        RewriteRequest(
            text="这个 价隔 其实不高\n而且 做品 质量很好",
            mode=RewriteMode.correct,
            model="heuristic",
            workspace=str(temp_workspace),
        )
    )

    assert result.success is True
    assert result.provider == "heuristic"
    assert result.rewritten_text == "这个价格其实不高。\n而且作品质量很好。"
    assert result.title
    assert len(result.tags) >= 3
    assert result.rewritten_text_path
    assert result.publish_text_path
    assert Path(result.rewritten_text_path).read_text(encoding="utf-8") == result.rewritten_text
    publish_text = Path(result.publish_text_path).read_text(encoding="utf-8")
    assert result.title in publish_text


def test_rewrite_service_uses_llm_json_result(temp_workspace: Path) -> None:
    client = FakeLLMClient(
        '{"rewritten_text":"先把结论说清楚，再展开原因","title":"结论先讲清","tags":["洞察","表达","短视频"]}'
    )
    service = RewriteService(llm_client=client)

    result = service.rewrite(
        RewriteRequest(
            text="原文内容",
            mode=RewriteMode.imitate,
            model="auto",
            workspace=str(temp_workspace),
        )
    )

    assert result.success is True
    assert result.provider == "fake"
    assert result.rewritten_text == "先把结论说清楚，再展开原因。"
    assert result.title == "结论先讲清"
    assert result.tags == ["#洞察", "#表达", "#短视频"]
    assert client.calls


def test_rewrite_service_returns_failure_when_llm_fails(temp_workspace: Path) -> None:
    client = FakeLLMClient("", should_raise=True)
    service = RewriteService(llm_client=client)

    result = service.rewrite(
        RewriteRequest(
            text="很多人一开始没看明白这件事，后面才发现问题在顺序。",
            mode=RewriteMode.imitate,
            model="auto",
            workspace=str(temp_workspace),
        )
    )

    assert result.success is False
    assert result.error
    assert result.error.code == "llm_rewrite_failed"


def test_rewrite_service_returns_failure_when_llm_is_not_configured(temp_workspace: Path) -> None:
    client = FakeLLMClient("", configured=False)
    service = RewriteService(llm_client=client)

    result = service.rewrite(
        RewriteRequest(
            text="很多人一开始没看明白这件事，后面才发现问题在顺序。",
            mode=RewriteMode.imitate,
            model="deepseek-chat",
            workspace=str(temp_workspace),
        )
    )

    assert result.success is False
    assert result.error
    assert result.error.code == "llm_not_configured"
