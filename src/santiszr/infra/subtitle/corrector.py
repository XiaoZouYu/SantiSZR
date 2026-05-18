from __future__ import annotations

import re
from pathlib import Path

from santiszr.infra.llm.client import LLMClient


class SubtitleCorrector:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    def can_correct(self) -> bool:
        return self.llm_client.is_configured()

    def correct_file(self, srt_path: str | Path) -> tuple[bool, list[str]]:
        path = Path(srt_path)
        if not path.exists():
            return False, [f"Subtitle correction skipped: file not found - {path}."]
        if not self.can_correct():
            return False, ["Subtitle correction skipped: LLM is not configured."]

        original_content = path.read_text(encoding="utf-8")
        entries = self._parse_entries(original_content)
        if not entries:
            return False, ["Subtitle correction skipped: no valid subtitle entries were found."]

        combined_text = "\n".join(entry["text"] for entry in entries)
        prompt = (
            "请对下面的字幕文本逐行纠错，只修正错别字、多音字和明显识别错误。"
            "不要改变每一行的数量，不要新增解释，不要合并或拆分行。"
            "请直接返回修正后的文本，每行对应原字幕的一行。\n"
            f"{combined_text}"
        )
        response = self.llm_client.generate(
            prompt,
            system_prompt=(
                "You are a Chinese subtitle correction assistant. "
                "Preserve line count and timing alignment. Return plain text lines only."
            ),
            temperature=0.2,
        )
        corrected_lines = [line.strip() for line in response.text.splitlines()]
        if len(corrected_lines) != len(entries):
            if len(corrected_lines) < len(entries):
                corrected_lines.extend(entry["text"] for entry in entries[len(corrected_lines) :])
            else:
                corrected_lines = corrected_lines[: len(entries)]

        corrected_content = self._render_entries(entries, corrected_lines)
        path.write_text(corrected_content, encoding="utf-8")
        backup_path = path.with_suffix(path.suffix + ".backup")
        backup_path.write_text(original_content, encoding="utf-8")
        return True, ["Subtitle corrected via LLM."]

    def _parse_entries(self, content: str) -> list[dict[str, str]]:
        pattern = re.compile(
            r"(\d+)\n"
            r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n"
            r"(.*?)(?=\n\n|\Z)",
            re.DOTALL,
        )
        entries: list[dict[str, str]] = []
        for index, start, end, text in pattern.findall(content):
            entries.append(
                {
                    "index": index,
                    "start": start,
                    "end": end,
                    "text": text.strip(),
                }
            )
        return entries

    def _render_entries(self, entries: list[dict[str, str]], corrected_lines: list[str]) -> str:
        blocks: list[str] = []
        for idx, entry in enumerate(entries):
            blocks.append(
                "\n".join(
                    [
                        entry["index"],
                        f'{entry["start"]} --> {entry["end"]}',
                        corrected_lines[idx],
                    ]
                )
            )
        return "\n\n".join(blocks) + "\n"
