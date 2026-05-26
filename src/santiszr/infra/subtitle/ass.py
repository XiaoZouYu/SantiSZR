from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from santiszr.domain.schemas.subtitle import SubtitleSegment, SubtitleStyle


@dataclass(frozen=True)
class AssSubtitleTemplate:
    name: str
    primary_color: str
    outline_color: str
    highlight_color: str
    back_color: str = "#000000"
    border_style: int = 1
    bold: bool = True
    outline: int = 3
    shadow: int = 0
    alignment: int = 2
    back_alpha: int = 128


ASS_SUBTITLE_TEMPLATES: dict[str, AssSubtitleTemplate] = {
    "classic": AssSubtitleTemplate(
        name="classic",
        primary_color="#FFFFFF",
        outline_color="#000000",
        highlight_color="#F59E0B",
        bold=False,
        outline=2,
    ),
    "short_video": AssSubtitleTemplate(
        name="short_video",
        primary_color="#FFFFFF",
        outline_color="#111827",
        highlight_color="#FF3B30",
        bold=True,
        outline=3,
        shadow=1,
    ),
    "black_bar": AssSubtitleTemplate(
        name="black_bar",
        primary_color="#FFFFFF",
        outline_color="#000000",
        highlight_color="#FBBF24",
        border_style=3,
        bold=True,
        outline=2,
        back_alpha=96,
    ),
    "knowledge": AssSubtitleTemplate(
        name="knowledge",
        primary_color="#E0F2FE",
        outline_color="#0F172A",
        highlight_color="#22D3EE",
        bold=True,
        outline=2,
    ),
}


class AssSubtitleRenderer:
    style_name = "Default"

    def write_ass(
        self,
        segments: list[SubtitleSegment],
        output_path: str | Path,
        *,
        style: SubtitleStyle,
        frame_width: int | float | None = None,
        frame_height: int | float | None = None,
    ) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            self.render(
                segments,
                style=style,
                frame_width=frame_width,
                frame_height=frame_height,
            ),
            encoding="utf-8",
        )
        return output

    def render(
        self,
        segments: list[SubtitleSegment],
        *,
        style: SubtitleStyle,
        frame_width: int | float | None = None,
        frame_height: int | float | None = None,
    ) -> str:
        template = resolve_ass_template(style.template)
        play_res_x = max(int(frame_width or 1080), 1)
        play_res_y = max(int(frame_height or 1920), 1)
        font_size = max(int(style.font_size), 12)
        margin_v = max(int(style.bottom_margin), 0)
        margin_x = max(24, int(play_res_x * 0.06))
        outline = max(template.outline, int(round(font_size / 16.0)))

        primary_color = style.color or template.primary_color
        outline_color = style.outline_color or template.outline_color
        highlight_color = style.highlight_color or template.highlight_color

        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            f"PlayResX: {play_res_x}",
            f"PlayResY: {play_res_y}",
            "YCbCr Matrix: TV.709",
            "",
            "[V4+ Styles]",
            (
                "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
                "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
                "Alignment,MarginL,MarginR,MarginV,Encoding"
            ),
            (
                f"Style: {self.style_name},{style.font_name},{font_size},"
                f"{to_ass_style_color(primary_color, fallback=template.primary_color)},"
                f"{to_ass_style_color(highlight_color, fallback=template.highlight_color)},"
                f"{to_ass_style_color(outline_color, fallback=template.outline_color)},"
                f"{to_ass_style_color(template.back_color, fallback='#000000', alpha=template.back_alpha)},"
                f"{-1 if template.bold else 0},0,0,0,100,100,0,0,"
                f"{template.border_style},{outline},{template.shadow},{template.alignment},"
                f"{margin_x},{margin_x},{margin_v},1"
            ),
            "",
            "[Events]",
            "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
        ]

        for segment in segments:
            text = self._format_dialogue_text(
                segment.text,
                keywords=style.highlight_keywords,
                highlight_color=highlight_color,
                highlight_outline_color=outline_color,
            )
            lines.append(
                f"Dialogue: 0,{format_ass_time(segment.start_sec)},{format_ass_time(segment.end_sec)},"
                f"{self.style_name},,0,0,0,,{text}"
            )

        return "\n".join(lines) + "\n"

    def _format_dialogue_text(
        self,
        text: str,
        *,
        keywords: list[str],
        highlight_color: str,
        highlight_outline_color: str,
    ) -> str:
        if not keywords:
            return escape_ass_text(text)

        normalized_keywords = sorted(
            [keyword for keyword in keywords if keyword.strip()],
            key=len,
            reverse=True,
        )
        if not normalized_keywords:
            return escape_ass_text(text)

        lower_text = text.lower()
        lower_keywords = [(keyword, keyword.lower()) for keyword in normalized_keywords]
        output: list[str] = []
        index = 0
        highlight_start = (
            r"{\b1"
            f"\\c{to_ass_inline_color(highlight_color, fallback='#FF3B30')}"
            f"\\3c{to_ass_inline_color(highlight_outline_color, fallback='#000000')}"
            r"}"
        )
        highlight_end = rf"{{\r{self.style_name}}}"

        while index < len(text):
            matched_keyword = ""
            for keyword, lower_keyword in lower_keywords:
                if lower_text.startswith(lower_keyword, index):
                    matched_keyword = text[index : index + len(keyword)]
                    break
            if matched_keyword:
                output.append(highlight_start)
                output.append(escape_ass_text(matched_keyword))
                output.append(highlight_end)
                index += len(matched_keyword)
                continue

            output.append(escape_ass_text(text[index]))
            index += 1

        return "".join(output)


def resolve_ass_template(value: str | None) -> AssSubtitleTemplate:
    key = (value or "short_video").strip().lower()
    return ASS_SUBTITLE_TEMPLATES.get(key, ASS_SUBTITLE_TEMPLATES["short_video"])


def parse_srt_segments(content: str) -> list[SubtitleSegment]:
    pattern = re.compile(
        r"\d+\s*\n"
        r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\s*\n"
        r"(.*?)(?=\n\s*\n|\Z)",
        re.DOTALL,
    )
    segments: list[SubtitleSegment] = []
    for start_raw, end_raw, text in pattern.findall(content):
        cleaned_text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if not cleaned_text:
            continue
        segments.append(
            SubtitleSegment(
                start_sec=parse_srt_time(start_raw),
                end_sec=parse_srt_time(end_raw),
                text=cleaned_text,
            )
        )
    return segments


def parse_srt_time(value: str) -> float:
    hours, minutes, remainder = value.split(":")
    seconds, millis = remainder.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0


def format_ass_time(value: float) -> str:
    total_centis = max(int(round(value * 100)), 0)
    hours, remainder = divmod(total_centis, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{centis:02}"


def escape_ass_text(value: str) -> str:
    return (
        value.replace("\\", "/")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", r"\N")
    )


def to_ass_style_color(value: str, *, fallback: str, alpha: int = 0) -> str:
    normalized = normalize_hex_color(value, fallback=fallback)
    clamped_alpha = max(0, min(int(alpha), 255))
    return f"&H{clamped_alpha:02X}{normalized[4:6]}{normalized[2:4]}{normalized[0:2]}"


def to_ass_inline_color(value: str, *, fallback: str) -> str:
    normalized = normalize_hex_color(value, fallback=fallback)
    return f"&H{normalized[4:6]}{normalized[2:4]}{normalized[0:2]}&"


def normalize_hex_color(value: str, *, fallback: str) -> str:
    normalized = (value or fallback).strip().lstrip("#")
    if len(normalized) == 3:
        normalized = "".join(ch * 2 for ch in normalized)
    if len(normalized) != 6:
        normalized = fallback.strip().lstrip("#")
    if any(ch not in "0123456789abcdefABCDEF" for ch in normalized):
        normalized = fallback.strip().lstrip("#")
    return normalized.upper()
