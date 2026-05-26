from __future__ import annotations

from array import array
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _configure_stdio() -> None:
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        reconfigure(encoding="utf-8", errors="replace")


def _configure_windows_runtime() -> None:
    if os.name != "nt":
        return

    runtime_python = str(os.getenv("SANTISZR_VOXCPM_PYTHON") or "").strip()
    candidate_dirs: list[Path] = []
    if runtime_python:
        python_path = Path(runtime_python).expanduser().resolve()
        runtime_root = python_path.parent
        candidate_dirs.extend(
            [
                runtime_root,
                runtime_root / "bin",
                runtime_root / "DLLs",
                runtime_root / "Library" / "bin",
            ]
        )

    project_root = Path(__file__).resolve().parents[4]
    candidate_dirs.append(project_root / "tools" / "nvidia" / "cuda" / "bin")

    current_path = os.environ.get("PATH", "")
    path_entries = current_path.split(os.pathsep) if current_path else []
    add_dll_directory = getattr(os, "add_dll_directory", None)

    for directory in candidate_dirs:
        if not directory.exists():
            continue
        directory_text = str(directory)
        if add_dll_directory is not None:
            add_dll_directory(directory_text)
        if directory_text not in path_entries:
            path_entries.insert(0, directory_text)

    os.environ["PATH"] = os.pathsep.join(path_entries)


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


@dataclass(slots=True, frozen=True)
class _InferenceProfile:
    cfg_value: float
    inference_timesteps: int
    retry_badcase: bool

    @property
    def version_tag(self) -> str:
        return (
            f"cfg={self.cfg_value:.2f}|steps={self.inference_timesteps}"
            f"|retry={1 if self.retry_badcase else 0}|trim=1|mode=reference-clone-v2"
        )


@dataclass(slots=True, frozen=True)
class _PreparedReferenceAudio:
    source_path: Path
    prepared_path: Path
    original_duration_sec: float
    prepared_duration_sec: float
    clipped: bool
    converted: bool = False


class VoxCPMRuntime:
    def __init__(self) -> None:
        from santiszr.config.settings import load_settings
        from santiszr.infra.media.ffmpeg import FFmpegAdapter

        settings = load_settings()
        self.profile = _InferenceProfile(
            cfg_value=float(settings.tts.voxcpm_cfg_value),
            inference_timesteps=int(settings.tts.voxcpm_inference_timesteps),
            retry_badcase=bool(settings.tts.voxcpm_retry_badcase),
        )
        configured_model_dir = str(os.getenv("SANTISZR_VOXCPM_MODEL_DIR") or "").strip()
        self.model_dir = (
            Path(configured_model_dir).expanduser().resolve()
            if configured_model_dir
            else Path(settings.models.voxcpm_model_dir).expanduser().resolve()
        )
        project_root = Path(__file__).resolve().parents[4]
        cache_root = (
            Path(settings.cache_dir).expanduser().resolve()
            if settings.cache_dir is not None
            else project_root / "data" / "cache"
        )
        self.reference_max_seconds = max(float(settings.tts.prompt_max_seconds), 1.0)
        self._reference_cache_dir = cache_root / "voxcpm" / "reference_audio"
        self._reference_cache_dir.mkdir(parents=True, exist_ok=True)
        self._ffmpeg = FFmpegAdapter()
        self._model: Any | None = None
        self._prompt_caches: dict[str, dict[str, object]] = {}
        self._prepared_references: dict[str, _PreparedReferenceAudio] = {}
        self._warmup_completed = False

    def synthesize(self, request: dict[str, object]) -> tuple[Path, str, list[str]]:
        text = str(request.get("text") or "").strip()
        if not text:
            raise RuntimeError("VoxCPM2 GPU synthesis requires non-empty text.")

        output_path_raw = str(request.get("output_path") or "").strip()
        if not output_path_raw:
            raise RuntimeError("VoxCPM2 helper requires an output path.")

        reference_audio_raw = str(request.get("reference_audio_path") or "").strip()
        if not reference_audio_raw:
            raise RuntimeError("VoxCPM2 GPU synthesis requires a reference audio.")

        reference_audio_path = Path(reference_audio_raw).expanduser().resolve()
        if not reference_audio_path.exists() or not reference_audio_path.is_file():
            raise RuntimeError(f"VoxCPM2 reference audio is missing: {reference_audio_path}")

        ultimate_clone = bool(request.get("ultimate_clone"))
        prompt_text = str(request.get("prompt_text") or "").strip()
        if ultimate_clone and not prompt_text:
            raise RuntimeError("Ultimate cloning requires prompt_text recognized from reference audio.")

        import torch

        output_path = Path(output_path_raw).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        model, notes = self._load_model()
        chunks = _split_synthesis_text(text)
        if len(chunks) > 1:
            notes.append(f"Chunked VoxCPM2 synthesis into {len(chunks)} segments.")

        prompt_cache: dict[str, object] | None = None
        ultimate_reference_path = reference_audio_path
        if ultimate_clone:
            notes.append("Ultimate cloning mode enabled.")
            notes.append("Using direct VoxCPM2 generate() with full reference audio for precise matching.")
            prepared_reference, prepare_notes = self._prepare_reference_audio(
                reference_audio_path,
                clip_to_max=False,
            )
            notes.extend(prepare_notes)
            ultimate_reference_path = prepared_reference.prepared_path
        else:
            prompt_cache, cache_notes = self._get_prompt_cache(model, reference_audio_path)
            notes.extend(cache_notes)
            notes.extend(self._warmup_model(model, prompt_cache))

        waveforms: list[torch.Tensor] = []
        for chunk in chunks:
            chunk_waveform: torch.Tensor | None = None
            chunk_tokens: torch.Tensor | None = None
            chunk_feat: torch.Tensor | None = None
            try:
                max_chunk_len = _recommended_chunk_max_len(model, chunk)
                if ultimate_clone:
                    chunk_waveform = model.generate(
                        target_text=chunk,
                        reference_wav_path=str(ultimate_reference_path),
                        prompt_wav_path=str(ultimate_reference_path),
                        prompt_text=prompt_text,
                        inference_timesteps=self.profile.inference_timesteps,
                        cfg_value=self.profile.cfg_value,
                        retry_badcase=False,
                        max_len=max_chunk_len,
                        trim_silence_vad=True,
                    )
                else:
                    chunk_waveform, chunk_tokens, chunk_feat = model.generate_with_prompt_cache(
                        target_text=chunk,
                        prompt_cache=prompt_cache,
                        inference_timesteps=self.profile.inference_timesteps,
                        cfg_value=self.profile.cfg_value,
                        retry_badcase=self.profile.retry_badcase,
                        max_len=max_chunk_len,
                    )
                if chunk_waveform.ndim == 1:
                    chunk_waveform = chunk_waveform.unsqueeze(0)
                waveforms.append(chunk_waveform.to(torch.float32).cpu())
            finally:
                del chunk_waveform, chunk_tokens, chunk_feat
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

        final_waveform = _concat_waveforms(waveforms, sample_rate=int(model.sample_rate))
        _save_waveform_wav(output_path, final_waveform, int(model.sample_rate))

        requested_speed = float(request.get("speed") or 1.0)
        if abs(requested_speed - 1.0) > 1e-6:
            notes.append("当前引擎暂不支持直接语速控制，已忽略语速参数。")

        requested_sample_rate = int(request.get("sample_rate") or 0)
        if requested_sample_rate and requested_sample_rate != int(model.sample_rate):
            notes.append(
                f"VoxCPM2 kept its native sample rate {int(model.sample_rate)} Hz instead of requested {requested_sample_rate} Hz."
            )

        return output_path, "voxcpm2", notes

    def _load_model(self) -> tuple[Any, list[str]]:
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("VoxCPM2 GPU runtime is unavailable: CUDA is not available.")

        if self._model is not None:
            return self._model, []

        from santiszr.vendor.voxcpm import load_local_voxcpm2_model, resolve_local_voxcpm2_model_dir

        resolved_model_dir = resolve_local_voxcpm2_model_dir(self.model_dir)
        self._model = load_local_voxcpm2_model(
            resolved_model_dir,
            device="cuda",
            optimize=True,
            load_denoiser=False,
        )
        self.model_dir = resolved_model_dir
        return self._model, [f"Loaded VoxCPM2 model on CUDA from {resolved_model_dir}."]

    def _get_prompt_cache(
        self,
        model: Any,
        reference_audio_path: Path,
    ) -> tuple[dict[str, object], list[str]]:
        prepared_reference, prepare_notes = self._prepare_reference_audio(reference_audio_path)
        key = self._prompt_cache_key(reference_audio_path)
        cached = self._prompt_caches.get(key)
        if cached is not None:
            return cached, [*prepare_notes, f"Reference cache hit: {reference_audio_path.name}"]

        prompt_cache = model.build_prompt_cache(
            reference_wav_path=str(prepared_reference.prepared_path),
            trim_silence_vad=True,
        )
        notes = [*prepare_notes, f"Built reference cache: {reference_audio_path.name}"]
        self._prompt_caches[key] = prompt_cache
        return prompt_cache, notes

    def _warmup_model(self, model: Any, prompt_cache: dict[str, object]) -> list[str]:
        if self._warmup_completed:
            return []

        model.generate_with_prompt_cache(
            target_text="预热",
            prompt_cache=prompt_cache,
            min_len=2,
            max_len=8,
            inference_timesteps=self.profile.inference_timesteps,
            cfg_value=self.profile.cfg_value,
            retry_badcase=False,
        )
        self._warmup_completed = True
        return ["VoxCPM2 GPU warmup complete."]

    def _prompt_cache_key(
        self,
        reference_audio_path: Path,
    ) -> str:
        stat = reference_audio_path.stat()
        raw = (
            f"{reference_audio_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|"
            f"{self.model_dir.resolve()}|{self.profile.version_tag}|refmax={self.reference_max_seconds:.2f}|"
            "mode=reference"
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _prepare_reference_audio(
        self,
        reference_audio_path: Path,
        *,
        clip_to_max: bool = True,
    ) -> tuple[_PreparedReferenceAudio, list[str]]:
        key = self._prepared_reference_key(reference_audio_path, clip_to_max=clip_to_max)
        cached = self._prepared_references.get(key)
        if cached is not None and cached.prepared_path.exists():
            return cached, self._prepared_reference_notes(cached)

        duration_sec = self._probe_audio_duration(reference_audio_path)
        needs_conversion = reference_audio_path.suffix.lower() != ".wav"
        should_clip = clip_to_max and duration_sec > self.reference_max_seconds + 0.05
        if not needs_conversion and not should_clip:
            prepared = _PreparedReferenceAudio(
                source_path=reference_audio_path,
                prepared_path=reference_audio_path,
                original_duration_sec=duration_sec,
                prepared_duration_sec=duration_sec,
                clipped=False,
                converted=False,
            )
            self._prepared_references[key] = prepared
            return prepared, []

        prepared_path = self._reference_cache_dir / f"{key}.wav"
        if not prepared_path.exists():
            if should_clip:
                self._write_clipped_reference_audio(reference_audio_path, prepared_path)
            else:
                self._write_converted_reference_audio(reference_audio_path, prepared_path)

        prepared = _PreparedReferenceAudio(
            source_path=reference_audio_path,
            prepared_path=prepared_path,
            original_duration_sec=duration_sec,
            prepared_duration_sec=min(duration_sec, self.reference_max_seconds) if should_clip else duration_sec,
            clipped=should_clip,
            converted=needs_conversion,
        )
        self._prepared_references[key] = prepared
        return prepared, self._prepared_reference_notes(prepared)

    def _prepared_reference_key(self, reference_audio_path: Path, *, clip_to_max: bool) -> str:
        stat = reference_audio_path.stat()
        raw = (
            f"{reference_audio_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|"
            f"{self.reference_max_seconds:.2f}|clip={1 if clip_to_max else 0}|"
            "format=pcm_s16le-wav"
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _prepared_reference_notes(self, prepared: _PreparedReferenceAudio) -> list[str]:
        notes: list[str] = []
        if prepared.converted:
            notes.append(f"Reference audio converted to PCM WAV for VoxCPM2: {prepared.source_path.name}")
        if prepared.clipped:
            notes.append(
                "Reference audio clipped for GPU-safe caching: "
                f"{prepared.prepared_duration_sec:.1f}s / {prepared.original_duration_sec:.1f}s "
                f"({prepared.source_path.name})"
            )
        return notes

    def _probe_audio_duration(self, reference_audio_path: Path) -> float:
        return float(self._ffmpeg.probe_duration(reference_audio_path))

    def _write_clipped_reference_audio(self, source_path: Path, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.suffix.lower() == ".wav":
            self._write_clipped_wav(source_path, output_path)
            return

        ffmpeg_path = self._ffmpeg.ffmpeg_path
        if not ffmpeg_path:
            raise RuntimeError(
                "FFmpeg is required to clip non-WAV reference audio for VoxCPM2 GPU-safe caching."
            )

        completed = subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-t",
                f"{self.reference_max_seconds:.3f}",
                "-i",
                str(source_path),
                "-vn",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Failed to clip reference audio with FFmpeg: {source_path}\n{completed.stderr.strip()}"
            )

    def _write_converted_reference_audio(self, source_path: Path, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_path = self._ffmpeg.ffmpeg_path
        if not ffmpeg_path:
            raise RuntimeError(
                "FFmpeg is required to convert reference audio for VoxCPM2."
            )

        completed = subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-i",
                str(source_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Failed to convert reference audio with FFmpeg: {source_path}\n{completed.stderr.strip()}"
            )

    def _write_clipped_wav(self, source_path: Path, output_path: Path) -> None:
        with wave.open(str(source_path), "rb") as reader:
            sample_rate = int(reader.getframerate())
            if sample_rate <= 0:
                raise RuntimeError(f"Unable to determine sample rate for reference audio: {source_path}")
            frame_count = max(int(round(self.reference_max_seconds * sample_rate)), 1)
            raw_frames = reader.readframes(frame_count)
            if not raw_frames:
                raise RuntimeError(f"Reference audio is empty after clipping: {source_path}")
            with wave.open(str(output_path), "wb") as writer:
                writer.setnchannels(reader.getnchannels())
                writer.setsampwidth(reader.getsampwidth())
                writer.setframerate(sample_rate)
                writer.writeframes(raw_frames)


_TEXT_CHUNK_LIMIT = 160
_TEXT_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?；;])\s*")
_TEXT_CLAUSE_BOUNDARY_RE = re.compile(r"(?<=[，,：:])\s*")


def _split_synthesis_text(text: str, *, max_chars: int = _TEXT_CHUNK_LIMIT) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [re.sub(r"\s+", " ", item).strip() for item in normalized.split("\n") if item.strip()]
    if not paragraphs:
        collapsed = re.sub(r"\s+", " ", normalized).strip()
        return [collapsed] if collapsed else []

    chunks: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
            continue

        sentences = _split_fragments(paragraph, _TEXT_SENTENCE_BOUNDARY_RE)
        sentence_chunks: list[str] = []
        for sentence in sentences:
            if len(sentence) <= max_chars:
                sentence_chunks.append(sentence)
                continue
            sentence_chunks.extend(_split_fragments(sentence, _TEXT_CLAUSE_BOUNDARY_RE))

        chunks.extend(_merge_fragments(sentence_chunks, max_chars=max_chars))

    return [chunk for chunk in chunks if chunk]


def _split_fragments(text: str, pattern: re.Pattern[str]) -> list[str]:
    parts = [part.strip() for part in pattern.split(text) if part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _merge_fragments(fragments: list[str], *, max_chars: int) -> list[str]:
    merged: list[str] = []
    current = ""

    for fragment in fragments:
        item = fragment.strip()
        if not item:
            continue
        if len(item) > max_chars:
            if current:
                merged.append(current)
                current = ""
            merged.extend(_hard_split_fragment(item, max_chars=max_chars))
            continue
        if not current:
            current = item
            continue
        if len(current) + len(item) <= max_chars:
            current = f"{current}{item}"
            continue
        merged.append(current)
        current = item

    if current:
        merged.append(current)
    return merged


def _hard_split_fragment(fragment: str, *, max_chars: int) -> list[str]:
    text = fragment.strip()
    if not text:
        return []
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]


def _concat_waveforms(waveforms: list["torch.Tensor"], *, sample_rate: int, gap_ms: int = 120) -> "torch.Tensor":
    import torch

    if not waveforms:
        raise RuntimeError("VoxCPM2 synthesis produced no waveform segments.")
    if len(waveforms) == 1:
        return waveforms[0]

    gap_samples = max(int(sample_rate * gap_ms / 1000.0), 0)
    pieces: list[torch.Tensor] = []
    for index, waveform in enumerate(waveforms):
        if index > 0 and gap_samples > 0:
            pieces.append(torch.zeros((waveform.shape[0], gap_samples), dtype=waveform.dtype))
        pieces.append(waveform)
    return torch.cat(pieces, dim=1)


def _save_waveform_wav(output_path: Path, waveform: "torch.Tensor", sample_rate: int) -> None:
    import torch

    if sample_rate <= 0:
        raise RuntimeError(f"Invalid VoxCPM2 sample rate: {sample_rate}")

    if waveform.ndim == 1:
        waveform = waveform.unsqueeze(0)
    if waveform.ndim != 2:
        raise RuntimeError(f"VoxCPM2 produced an unsupported waveform shape: {tuple(waveform.shape)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    audio = waveform.detach().to(dtype=torch.float32).cpu().clamp(-1.0, 1.0)
    pcm = (audio * 32767.0).round().to(torch.int16)
    frames = array("h", pcm.transpose(0, 1).contiguous().view(-1).tolist()).tobytes()

    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(int(pcm.shape[0]))
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(sample_rate))
        wav_file.writeframes(frames)


def _recommended_chunk_max_len(model: Any, text: str) -> int:
    token_length = max(len(model.text_tokenizer(text)), 1)
    return min(max(int(token_length * 6.0 + 24), 72), 720)


def main() -> int:
    _configure_stdio()
    _configure_windows_runtime()
    runtime = VoxCPMRuntime()

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except Exception as exc:
            _emit({"ok": False, "error": f"Invalid helper request: {exc}"})
            continue

        try:
            with contextlib.redirect_stdout(sys.stderr):
                audio_path, provider, notes = runtime.synthesize(request)
        except Exception as exc:
            _emit({"ok": False, "error": str(exc)})
            continue

        _emit(
            {
                "ok": True,
                "audio_path": str(audio_path),
                "provider": provider,
                "notes": list(notes),
            }
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
