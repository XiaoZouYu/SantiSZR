from __future__ import annotations

from pathlib import Path
import re

from santiszr.core.paths import ensure_module_dir, sanitize_filename
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.subtitle import SubtitleRequest, SubtitleResult, SubtitleSegment
from santiszr.infra.media.ffmpeg import FFmpegAdapter
from santiszr.infra.subtitle import AssSubtitleRenderer, ScriptSubtitleGenerator, SubtitleCorrector


class SubtitleService:
    _REFERENCE_UNIT_TARGET_CHARS = 18
    _REFERENCE_UNIT_MAX_CHARS = 20
    _REFERENCE_PUNCTUATION_PATTERN = r"([\u3002\uff01\uff1f\uff1b\uff0c\u3001\uff1a.!?;,，、:])"
    _REFERENCE_STRONG_ENDINGS = "\u3002\uff01\uff1f.!?"

    def __init__(
        self,
        ffmpeg: FFmpegAdapter | None = None,
        generator: ScriptSubtitleGenerator | None = None,
        corrector: SubtitleCorrector | None = None,
        ass_renderer: AssSubtitleRenderer | None = None,
    ) -> None:
        self.ffmpeg = ffmpeg or FFmpegAdapter()
        self.generator = generator or ScriptSubtitleGenerator()
        self.corrector = corrector or SubtitleCorrector()
        self.ass_renderer = ass_renderer or AssSubtitleRenderer()

    def generate(self, request: SubtitleRequest) -> SubtitleResult:
        workspace = (
            Path(request.workspace).expanduser().resolve()
            if request.workspace
            else Path(request.audio_path).resolve().parent.parent
        )
        subtitle_dir = ensure_module_dir(workspace, "subtitle")
        notes: list[str] = []
        corrected = False
        quality_ok = True
        generated_by = "heuristic"
        try:
            duration = self.ffmpeg.probe_duration(request.audio_path)
            base_name = sanitize_filename(request.output_name or Path(request.audio_path).stem, fallback="subtitle")
            srt_path = subtitle_dir / f"{base_name}.srt"
            ass_path = subtitle_dir / f"{base_name}.ass"

            segments: list[SubtitleSegment]
            if self.generator.available():
                try:
                    _, generator_notes = self.generator.generate(request.audio_path, srt_path)
                    notes.extend(generator_notes)
                    generated_by = "script"
                    segments = self._parse_srt(srt_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    notes.append(f"Subtitle generator fallback to heuristic: {exc}")
                    segments = self._build_segments(request.reference_text or Path(request.audio_path).stem, duration)
                    self.ffmpeg.write_srt(segments, srt_path)
            else:
                notes.append("Subtitle generator script is unavailable, using heuristic fallback.")
                segments = self._build_segments(request.reference_text or Path(request.audio_path).stem, duration)
                self.ffmpeg.write_srt(segments, srt_path)

            if request.reference_text and request.reference_text.strip():
                rebuilt_segments = self._rebuild_segments_from_reference(
                    request.reference_text,
                    fallback_segments=segments,
                    duration=duration,
                )
                if rebuilt_segments:
                    segments = rebuilt_segments
                    self.ffmpeg.write_srt(segments, srt_path)
                    notes.append("Rebuilt subtitle segments from reference text to keep complete clauses.")

            subtitle_text = srt_path.read_text(encoding="utf-8")
            quality_ok = self._check_quality(subtitle_text, request.max_chars_per_line)
            if not quality_ok:
                notes.append(f"Subtitle quality warning: at least one line exceeds {request.max_chars_per_line} characters.")

            if request.correct_with_ai:
                corrected, correction_notes = self.corrector.correct_file(srt_path)
                notes.extend(correction_notes)
                subtitle_text = srt_path.read_text(encoding="utf-8")
                segments = self._parse_srt(subtitle_text)

            frame_width: int | float | None = None
            frame_height: int | float | None = None
            if request.video_path:
                video_meta = self.ffmpeg.probe_video_meta(request.video_path)
                frame_width = video_meta.get("width")
                frame_height = video_meta.get("height")
            self.ass_renderer.write_ass(
                segments,
                ass_path,
                style=request.style,
                frame_width=frame_width,
                frame_height=frame_height,
            )
            ass_text = ass_path.read_text(encoding="utf-8")
            notes.append("Generated ASS subtitle with template and keyword highlight settings.")

            burned_video_path: Path | None = None
            if request.burn_in:
                target_video = request.video_path
                if target_video:
                    burned_video_path = self.ffmpeg.burn_subtitles(
                        target_video,
                        ass_path,
                        subtitle_dir / f"{base_name}_burned.mp4",
                        style=request.style,
                    )
                else:
                    burned_video_path = self.ffmpeg.create_subtitle_video(
                        request.audio_path,
                        ass_path,
                        subtitle_dir / f"{base_name}_subtitle.mp4",
                        style=request.style,
                    )

            return SubtitleResult(
                success=True,
                srt_path=str(srt_path),
                ass_path=str(ass_path),
                burned_video_path=str(burned_video_path) if burned_video_path else None,
                subtitle_text=subtitle_text,
                ass_text=ass_text,
                segments=segments,
                generated_by=generated_by,
                corrected=corrected,
                quality_ok=quality_ok,
                notes=notes,
            )
        except Exception as exc:
            return SubtitleResult(
                success=False,
                notes=notes,
                error=ErrorInfo(code="subtitle_failed", message=str(exc)),
            )

    def _build_segments(self, text: str, duration: float) -> list[SubtitleSegment]:
        sentences = [item.strip() for item in re.split(r"[。！？!?；;\n]+", text) if item.strip()]
        if not sentences:
            sentences = [text.strip() or "字幕内容"]
        chunks: list[str] = []
        for sentence in sentences:
            if len(sentence) <= 18:
                chunks.append(sentence)
                continue
            for start in range(0, len(sentence), 18):
                chunks.append(sentence[start : start + 18])
        weights = [max(len(chunk), 1) for chunk in chunks]
        total_chars = sum(weights)
        current = 0.0
        segments: list[SubtitleSegment] = []
        for index, chunk in enumerate(chunks):
            weight = weights[index] / total_chars
            chunk_duration = max(0.2, duration * weight)
            start_sec = current
            end_sec = duration if index == len(chunks) - 1 else min(duration, current + chunk_duration)
            segments.append(
                SubtitleSegment(
                    start_sec=round(start_sec, 3),
                    end_sec=round(max(end_sec, start_sec + 0.2), 3),
                    text=chunk,
                )
            )
            current = end_sec
        return segments

    def _rebuild_segments_from_reference(
        self,
        reference_text: str,
        *,
        fallback_segments: list[SubtitleSegment],
        duration: float,
    ) -> list[SubtitleSegment]:
        units = self._split_reference_units(reference_text)
        if not units:
            return []

        start_sec = fallback_segments[0].start_sec if fallback_segments else 0.0
        end_sec = fallback_segments[-1].end_sec if fallback_segments else duration
        total_duration = max(end_sec - start_sec, duration, 0.3 * len(units))
        weights = [max(self._unit_length(unit), 1) for unit in units]
        total_weight = sum(weights)
        current = start_sec
        rebuilt: list[SubtitleSegment] = []

        for index, unit in enumerate(units):
            piece_duration = max(0.35, total_duration * (weights[index] / total_weight))
            next_end = end_sec if index == len(units) - 1 else min(end_sec, current + piece_duration)
            rebuilt.append(
                SubtitleSegment(
                    start_sec=round(current, 3),
                    end_sec=round(max(next_end, current + 0.35), 3),
                    text=unit,
                )
            )
            current = max(next_end, current + 0.35)

        if rebuilt:
            rebuilt[-1].end_sec = round(max(end_sec, rebuilt[-1].start_sec + 0.35), 3)
        return rebuilt

    def _split_reference_units(self, text: str) -> list[str]:
        collapsed = re.sub(r"[\r\n]+", " ", text)
        collapsed = re.sub(r"\s+", " ", collapsed).strip()
        if not collapsed:
            return []

        tokens = re.split(self._REFERENCE_PUNCTUATION_PATTERN, collapsed)
        raw_units: list[str] = []
        current = ""
        for token in tokens:
            if not token:
                continue
            current += token
            if re.fullmatch(self._REFERENCE_PUNCTUATION_PATTERN, token):
                piece = current.strip()
                if piece:
                    raw_units.extend(self._split_long_reference_unit(piece))
                current = ""
        if current.strip():
            raw_units.extend(self._split_long_reference_unit(current.strip()))

        return self._merge_short_units(raw_units)

    def _split_long_reference_unit(self, unit: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", unit).strip()
        if not normalized:
            return []
        if self._unit_length(normalized) <= self._REFERENCE_UNIT_MAX_CHARS:
            return [self._normalize_reference_unit(normalized)]

        parts = [part for part in re.split(r"\s+", normalized) if part]
        if len(parts) > 1:
            chunks: list[str] = []
            current_parts: list[str] = []
            for part in parts:
                candidate = self._join_reference_parts([*current_parts, part])
                if current_parts and self._unit_length(candidate) > self._REFERENCE_UNIT_MAX_CHARS:
                    chunks.extend(self._split_by_visual_length(self._join_reference_parts(current_parts)))
                    current_parts = [part]
                else:
                    current_parts.append(part)
            if current_parts:
                chunks.extend(self._split_by_visual_length(self._join_reference_parts(current_parts)))
            return [chunk for chunk in chunks if chunk]

        return self._split_by_visual_length(self._normalize_reference_unit(normalized))

    def _split_by_visual_length(self, text: str) -> list[str]:
        normalized = self._normalize_reference_unit(text)
        if not normalized:
            return []
        if self._unit_length(normalized) <= self._REFERENCE_UNIT_MAX_CHARS:
            return [normalized]

        chunks: list[str] = []
        current = ""
        for char in normalized:
            current += char
            if self._unit_length(current) >= self._REFERENCE_UNIT_TARGET_CHARS:
                chunks.append(current.strip())
                current = ""

        tail = current.strip()
        if tail:
            if chunks and self._unit_length(tail) <= 4 and self._unit_length(chunks[-1] + tail) <= self._REFERENCE_UNIT_MAX_CHARS:
                chunks[-1] += tail
            else:
                chunks.append(tail)
        return chunks

    def _join_reference_parts(self, parts: list[str]) -> str:
        if any(re.search(r"[\u4e00-\u9fff]", part) for part in parts):
            return "".join(parts)
        return " ".join(parts)

    def _normalize_reference_unit(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()
        if re.search(r"[\u4e00-\u9fff]", normalized):
            normalized = re.sub(r"\s+", "", normalized)
        return normalized

    def _merge_short_units(self, units: list[str]) -> list[str]:
        merged: list[str] = []
        index = 0
        while index < len(units):
            current = units[index].strip()
            if not current:
                index += 1
                continue

            if (
                self._unit_length(current) <= 6
                and index + 1 < len(units)
                and current[-1] not in self._REFERENCE_STRONG_ENDINGS
            ):
                nxt = units[index + 1].strip()
                if self._unit_length(current + nxt) <= self._REFERENCE_UNIT_MAX_CHARS:
                    current = current + nxt
                    index += 1

            if (
                merged
                and self._unit_length(current) <= 4
                and current[-1] not in self._REFERENCE_STRONG_ENDINGS
                and self._unit_length(merged[-1] + current) <= self._REFERENCE_UNIT_MAX_CHARS
            ):
                merged[-1] = merged[-1] + current
            else:
                merged.append(current)
            index += 1
        return merged

    def _unit_length(self, text: str) -> int:
        compact = re.sub(
            r"[\s\u3002\uff0c\uff01\uff1f\uff1b;:：,，\u3001\"'\u201c\u201d\u2018\u2019\uff08\uff09()\u3010\u3011\[\]<>《》/]+",
            "",
            text,
        )
        return len(compact)

    def _parse_srt(self, content: str) -> list[SubtitleSegment]:
        pattern = re.compile(
            r"\d+\n"
            r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n"
            r"(.*?)(?=\n\n|\Z)",
            re.DOTALL,
        )
        segments: list[SubtitleSegment] = []
        for start_raw, end_raw, text in pattern.findall(content):
            segments.append(
                SubtitleSegment(
                    start_sec=self._parse_srt_time(start_raw),
                    end_sec=self._parse_srt_time(end_raw),
                    text=text.strip(),
                )
            )
        return segments

    def _parse_srt_time(self, value: str) -> float:
        hours, minutes, remainder = value.split(":")
        seconds, millis = remainder.split(",")
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0

    def _check_quality(self, content: str, max_chars_per_line: int) -> bool:
        for segment in self._parse_srt(content):
            compact = segment.text.replace("\n", "").strip()
            if len(compact) > max_chars_per_line:
                return False
        return True
