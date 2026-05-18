from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from santiszr.core.paths import ensure_module_dir
from santiszr.domain.schemas.audio import RewriteRequest, RewriteResult
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.infra.llm.client import LLMClient


class RewriteService:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    def rewrite(self, request: RewriteRequest) -> RewriteResult:
        try:
            if request.model != "heuristic":
                if not self.llm_client.is_configured():
                    return RewriteResult(
                        success=False,
                        error=ErrorInfo(
                            code="llm_not_configured",
                            message="大模型 API Key 未配置，请先在设置中保存并测试大模型配置。",
                        ),
                    )
                try:
                    result = self._rewrite_with_llm(request)
                    if result.success:
                        return self._persist_result(request, result)
                except Exception as exc:
                    return RewriteResult(
                        success=False,
                        error=ErrorInfo(code="llm_rewrite_failed", message=f"大模型改写失败：{exc}"),
                    )

            result = self._rewrite_with_heuristics(request)
            return self._persist_result(request, result)
        except Exception as exc:
            return RewriteResult(
                success=False,
                error=ErrorInfo(code="rewrite_failed", message=str(exc)),
            )

    def _rewrite_with_llm(self, request: RewriteRequest) -> RewriteResult:
        prompt = self._build_user_prompt(request)
        response = self.llm_client.generate(
            prompt,
            system_prompt=self._build_system_prompt(request),
            model=None if request.model == "auto" else request.model,
            temperature=request.temperature,
        )
        data = self._parse_json(response.text)
        rewritten_text = self._finalize_text(str(data.get("rewritten_text") or ""))
        if not rewritten_text:
            raise RuntimeError("LLM returned empty rewritten_text.")
        title = self._normalize_title(data.get("title"), rewritten_text)
        tags = self._normalize_tags(data.get("tags")) or self._heuristic_tags(rewritten_text)
        return RewriteResult(
            success=True,
            rewritten_text=rewritten_text,
            title=title,
            tags=tags,
            provider=response.provider,
            prompt_used=prompt,
        )

    def _rewrite_with_heuristics(self, request: RewriteRequest) -> RewriteResult:
        normalized = self._normalize_text(request.text)
        paragraphs = self._split_paragraphs(normalized)

        if request.mode.value == "correct":
            rewritten = "\n".join(self._punctuate_sentence(item) for item in paragraphs)
        elif request.mode.value == "imitate":
            rewritten = self._build_imitate_copy(paragraphs)
        else:
            rewritten = self._build_custom_copy(paragraphs, request.prompt or "")

        rewritten = self._finalize_text(rewritten)
        return RewriteResult(
            success=True,
            rewritten_text=rewritten,
            title=self._heuristic_title(rewritten),
            tags=self._heuristic_tags(rewritten),
            provider="heuristic",
            prompt_used=request.prompt,
        )

    def _persist_result(self, request: RewriteRequest, result: RewriteResult) -> RewriteResult:
        if not result.success or not request.workspace or not result.rewritten_text:
            return result

        rewrite_dir = ensure_module_dir(Path(request.workspace).expanduser().resolve(), "rewrite")
        rewritten_text_path = rewrite_dir / "rewritten_text.txt"
        publish_text_path = rewrite_dir / "publish_text.txt"

        rewritten_text_path.write_text(result.rewritten_text, encoding="utf-8")
        publish_text_path.write_text(self._render_publish_text(result.title, result.tags), encoding="utf-8")

        result.rewritten_text_path = str(rewritten_text_path)
        result.publish_text_path = str(publish_text_path)
        return result

    def _build_system_prompt(self, request: RewriteRequest) -> str:
        base = (
            "You are a Chinese short-video copywriting assistant. "
            "Always return strict JSON with keys rewritten_text, title, tags. "
            "The tags value must be a JSON array of hashtag strings."
        )
        if request.mode.value == "correct":
            return (
                base
                + " Focus on correcting ASR typos and punctuation while preserving facts, tone, and structure."
            )
        if request.mode.value == "imitate":
            return (
                base
                + " Rewrite into a stronger spoken short-video script, but preserve core facts, sequence, and intent."
            )
        return (
            base
            + " Follow the user's custom instruction closely while keeping the output suitable for spoken short-video copy."
        )

    def _build_user_prompt(self, request: RewriteRequest) -> str:
        mode_instructions = {
            "correct": (
                "修正错别字、口语转写错误和标点。"
                "不要新增事实，不要改变原意，不要改成论文风。"
            ),
            "imitate": (
                "改写成短视频口播文案。"
                "保留原始事实、顺序和核心观点，增强开头吸引力和口播节奏。"
            ),
            "custom": (
                f"按以下指令改写：{(request.prompt or '提升口播感、结构和传播力').strip()}"
            ),
        }
        return (
            "请处理下面的中文文案，并返回 JSON：\n"
            "{\n"
            '  "rewritten_text": "正文",\n'
            '  "title": "20字以内标题",\n'
            '  "tags": ["#标签1", "#标签2", "#标签3"]\n'
            "}\n"
            f"要求：{mode_instructions[request.mode.value]}\n"
            "标题必须适合短视频发布，标签输出 3 到 5 个。\n"
            f"原文：\n{request.text}"
        )

    def _parse_json(self, text: str) -> dict[str, object]:
        cleaned = text.strip()
        cleaned = re.sub(r"^```json\s*|^```|\s*```$", "", cleaned, flags=re.MULTILINE)
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(0)
        return json.loads(cleaned)

    def _normalize_text(self, text: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"[ \t]+", " ", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        replacements = {
            "抖因": "抖音",
            "价隔": "价格",
            "做品": "作品",
            "帐户": "账户",
            "ҕƵ": "视频",
        }
        for wrong, correct in replacements.items():
            normalized = normalized.replace(wrong, correct)
        return normalized.strip()

    def _split_paragraphs(self, text: str) -> list[str]:
        parts = [item.strip() for item in re.split(r"\n+", text) if item.strip()]
        return parts or [text.strip() or "请补充文案内容。"]

    def _split_sentences(self, text: str) -> list[str]:
        parts = [item.strip() for item in re.split(r"[。！？!?；;\n]+", text) if item.strip()]
        return parts or [text.strip() or "请补充文案内容"]

    def _build_imitate_copy(self, paragraphs: list[str]) -> str:
        first_paragraph = paragraphs[0]
        sentences = self._split_sentences(first_paragraph)
        hook = sentences[0][:18]
        opening = f"{hook}，但真正关键的点，很多人一开始没看明白。"
        body = [self._punctuate_sentence(item) for item in paragraphs]
        body[0] = opening
        return "\n".join(body)

    def _build_custom_copy(self, paragraphs: list[str], custom_prompt: str) -> str:
        guide = custom_prompt.strip("。；; ") or "突出冲突、结果和行动建议"
        head = self._split_sentences(paragraphs[0])[0][:18]
        opening = f"{head}，先把最重要的结论说清楚。"
        remaining = [self._punctuate_sentence(item) for item in paragraphs[1:]]
        return "\n".join([opening, f"改写重点：{guide}。", *remaining]) if remaining else f"{opening}\n改写重点：{guide}。"

    def _punctuate_sentence(self, text: str) -> str:
        sentence = re.sub(r"\s+", "", text)
        if not sentence:
            return "请补充文案内容。"
        if sentence[-1] not in "。！？!?":
            sentence += "。"
        return sentence

    def _finalize_text(self, text: str) -> str:
        lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()]
        if not lines:
            return ""
        finalized = []
        for line in lines:
            compact = re.sub(r"\s+", " ", line).strip()
            if compact and compact[-1] not in "。！？!?":
                compact += "。"
            finalized.append(compact)
        return "\n".join(finalized)

    def _normalize_title(self, value: object, fallback_text: str) -> str:
        title = str(value or "").strip()
        if not title:
            return self._heuristic_title(fallback_text)
        title = re.sub(r"\s+", " ", title).strip("。！？!? ")
        return title[:20] or self._heuristic_title(fallback_text)

    def _heuristic_title(self, text: str) -> str:
        first_line = self._split_paragraphs(text)[0]
        first_sentence = self._split_sentences(first_line)[0]
        title = re.sub(r"\s+", "", first_sentence).strip("。！？!? ")
        return (title[:20] or "文案结果").strip()

    def _heuristic_tags(self, text: str) -> list[str]:
        chinese_terms = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
        stopwords = {
            "我们",
            "你们",
            "他们",
            "这个",
            "那个",
            "如果",
            "就是",
            "因为",
            "所以",
            "内容",
            "视频",
            "文案",
        }
        ranked = [term for term, _ in Counter(chinese_terms).most_common() if term not in stopwords]
        tags = [f"#{term}" for term in ranked[:5]]
        defaults = ["#短视频", "#口播文案", "#内容改写"]
        for default in defaults:
            if len(tags) >= 5:
                break
            if default not in tags:
                tags.append(default)
        deduped: list[str] = []
        for tag in tags:
            if tag not in deduped:
                deduped.append(tag)
        return deduped[:5]

    def _normalize_tags(self, value: object) -> list[str]:
        if isinstance(value, list):
            raw_items = [str(item).strip() for item in value if str(item).strip()]
        elif isinstance(value, str):
            raw_items = [item.strip() for item in re.split(r"[,，\s]+", value) if item.strip()]
        else:
            raw_items = []

        tags: list[str] = []
        for item in raw_items:
            if not item.startswith("#"):
                item = f"#{item.lstrip('#')}"
            if item not in tags:
                tags.append(item)
        return tags[:5]

    def _render_publish_text(self, title: str | None, tags: list[str]) -> str:
        safe_title = (title or "默认标题").strip()
        safe_tags = " ".join(tags[:5]).strip()
        return f"{safe_title}\n{safe_tags}".strip()
