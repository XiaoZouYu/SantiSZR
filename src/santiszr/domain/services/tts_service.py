from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from santiszr.domain.schemas.audio import TTSRequest, TTSResult
from santiszr.domain.schemas.audio import AudioMeta
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.core.paths import ensure_module_dir, sanitize_filename
from santiszr.infra.media.ffmpeg import FFmpegAdapter
from santiszr.infra.tts.voxcpm_client import VoxCPMClient


class TTSService:
    def __init__(
        self,
        client: VoxCPMClient | None = None,
        ffmpeg: FFmpegAdapter | None = None,
    ) -> None:
        self.ffmpeg = ffmpeg or FFmpegAdapter()
        self.client = client or VoxCPMClient(ffmpeg=self.ffmpeg)

    def synthesize(self, request: TTSRequest) -> TTSResult:
        workspace = Path(request.workspace).expanduser().resolve() if request.workspace else Path.cwd() / "data" / "adhoc"
        tts_dir = ensure_module_dir(workspace, "tts")
        try:
            prompt_text, preparation_notes = self._resolve_prompt_text_for_ultimate_clone(request)
            base_name = self._resolve_request_output_base(tts_dir, request)
            text_path = tts_dir / f"{base_name}.txt"
            text_path.write_text(request.text, encoding="utf-8")
            output_path, provider, notes = self.client.synthesize(
                text=request.text,
                voice=request.voice,
                output_path=tts_dir / f"{base_name}.wav",
                reference_audio_path=request.reference_audio_path,
                ultimate_clone=request.ultimate_clone,
                prompt_text=prompt_text,
                speed=request.speed,
                sample_rate=request.sample_rate,
                speaker=request.speaker,
            )
            notes = [*preparation_notes, *notes]
            meta_info = self.ffmpeg.probe_audio_meta(output_path)
            return TTSResult(
                success=True,
                audio_path=str(output_path),
                source_text_path=str(text_path),
                reference_audio_path=request.reference_audio_path,
                meta=AudioMeta(**meta_info),
                voice=request.voice,
                provider=provider,
                notes=notes,
            )
        except Exception as exc:
            return TTSResult(
                success=False,
                error=ErrorInfo(code="tts_failed", message=str(exc)),
            )

    def release_resources(self) -> None:
        shutdown = getattr(self.client, "shutdown_shared_helper", None)
        if callable(shutdown):
            shutdown()

    def _resolve_request_output_base(self, tts_dir: Path, request: TTSRequest) -> str:
        requested_output_name = str(request.output_name or "").strip()
        if requested_output_name == "studio-narration":
            return self._next_numeric_output_base(tts_dir)

        requested_base = sanitize_filename(requested_output_name or request.voice or "narration", fallback="narration")
        return self._resolve_output_base(tts_dir, requested_base)

    def _resolve_output_base(self, tts_dir: Path, base_name: str) -> str:
        wav_path = tts_dir / f"{base_name}.wav"
        text_path = tts_dir / f"{base_name}.txt"
        if not wav_path.exists() and not text_path.exists():
            return base_name

        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")[:-3]
        return sanitize_filename(f"{base_name}-{timestamp}", fallback=base_name)

    def _next_numeric_output_base(self, tts_dir: Path) -> str:
        max_index = 0
        for candidate in tts_dir.iterdir():
            if candidate.suffix.lower() not in {".wav", ".txt"}:
                continue
            stem = candidate.stem.strip()
            if not stem.isdigit():
                continue
            max_index = max(max_index, int(stem))
        return str(max_index + 1)

    def _resolve_prompt_text_for_ultimate_clone(self, request: TTSRequest) -> tuple[str | None, list[str]]:
        if not request.ultimate_clone:
            return request.prompt_text, []

        reference_audio_raw = str(request.reference_audio_path or "").strip()
        if not reference_audio_raw:
            raise RuntimeError("Ultimate cloning requires a reference audio.")

        prompt_text = str(request.prompt_text or "").strip()
        if not prompt_text:
            raise RuntimeError(
                "Ultimate cloning requires prompt_text prepared from the reference audio before synthesis."
            )

        return prompt_text, [
            "Ultimate cloning enabled.",
            "Reference transcript provided for ultimate cloning.",
        ]
