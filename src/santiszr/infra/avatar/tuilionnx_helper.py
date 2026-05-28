from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from santiszr.domain.schemas.subtitle import SubtitleStyle


def _is_locked_output_error(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) == 32


def _build_locked_output_fallback(output_path: Path) -> Path:
    token = uuid.uuid4().hex[:8]
    return output_path.with_name(f"{output_path.stem}-locked-{token}{output_path.suffix}")


def _finalize_output(final_source: Path, output_path: Path, notes: list[str]) -> Path:
    destination = output_path
    if output_path.exists():
        try:
            output_path.unlink()
        except OSError as exc:
            if not _is_locked_output_error(exc):
                raise
            destination = _build_locked_output_fallback(output_path)
            notes.append(f"Target file was locked; saved avatar output as {destination.name} instead.")
    shutil.move(str(final_source), str(destination))
    return destination


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

    runtime_python = str(os.getenv("SANTISZR_TUILIONNX_PYTHON") or "").strip()
    candidate_dirs: list[Path] = []
    if runtime_python:
        python_path = Path(runtime_python).expanduser().resolve()
        runtime_root = python_path.parent
        site_packages = runtime_root / "Lib" / "site-packages"
        candidate_dirs.extend(
            [
                runtime_root,
                runtime_root / "bin",
                runtime_root / "DLLs",
                runtime_root / "Library" / "bin",
                site_packages / "onnxruntime" / "capi",
                site_packages / "torch" / "lib",
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


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True, frozen=True)
class _RuntimeKey:
    beautify_teeth: bool
    batch_size: int
    sync_offset: float
    scale_h: float
    scale_w: float
    compress_inference: bool
    ffmpeg_path: str
    video_encoder: str


class AvatarRenderRuntime:
    def __init__(self) -> None:
        from santiszr.config.settings import load_settings
        from santiszr.infra.media.ffmpeg import FFmpegAdapter

        settings = load_settings()
        configured_root = str(os.getenv("SANTISZR_TUILIONNX_MODEL_DIR") or "").strip()
        self.model_root = (
            Path(configured_root).expanduser().resolve()
            if configured_root
            else Path(settings.avatar.tuilionnx_root or settings.models.tuilionnx_model_dir).expanduser().resolve()
        )
        self.ffmpeg = FFmpegAdapter()
        self._runtimes: dict[_RuntimeKey, object] = {}

    def render(self, request: dict[str, object]) -> tuple[Path, Path, list[str]]:
        import torch
        import onnxruntime as ort

        if not torch.cuda.is_available():
            raise RuntimeError("TuiliONNX GPU runtime is unavailable: CUDA is not available.")
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise RuntimeError("TuiliONNX GPU runtime is unavailable: ONNX Runtime CUDAExecutionProvider is missing.")
        if self.ffmpeg.ffmpeg_path is None:
            raise RuntimeError("FFmpeg binary is not available for TuiliONNX rendering.")

        reference_video_path = Path(str(request.get("reference_video_path") or "")).expanduser().resolve()
        if not reference_video_path.exists() or not reference_video_path.is_file():
            raise RuntimeError(f"TuiliONNX reference video is missing: {reference_video_path}")

        audio_path = Path(str(request.get("audio_path") or "")).expanduser().resolve()
        if not audio_path.exists() or not audio_path.is_file():
            raise RuntimeError(f"TuiliONNX driving audio is missing: {audio_path}")

        output_path_raw = str(request.get("output_path") or "").strip()
        if not output_path_raw:
            raise RuntimeError("TuiliONNX helper requires an output path.")
        output_path = Path(output_path_raw).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        subtitle_path_raw = str(request.get("subtitle_path") or "").strip()
        subtitle_path = Path(subtitle_path_raw).expanduser().resolve() if subtitle_path_raw else None
        subtitle_style_payload = request.get("subtitle_style")
        subtitle_style = (
            SubtitleStyle.model_validate(subtitle_style_payload)
            if isinstance(subtitle_style_payload, dict)
            else SubtitleStyle()
        )

        runtime, notes = self._get_runtime(request)
        token = uuid.uuid4().hex[:10]
        temp_dir = output_path.parent / ".tuilionnx"
        temp_dir.mkdir(parents=True, exist_ok=True)
        core_output = temp_dir / f"{output_path.stem}_{token}_core.mp4"
        fps25_path = temp_dir / f"{output_path.stem}_{token}_25fps.mp4"
        frame_temp_prefix = temp_dir / f"{output_path.stem}_{token}_frames"
        audio_temp_path = temp_dir / f"{output_path.stem}_{token}_16k.wav"

        final_source = core_output
        cleanup_targets = [core_output, fps25_path, audio_temp_path, Path(f"{frame_temp_prefix}.avi")]
        try:
            runtime.run(
                video_path=str(reference_video_path),
                video_fps25_path=str(fps25_path),
                video_temp_path=str(frame_temp_prefix),
                audio_path=str(audio_path),
                audio_temp_path=str(audio_temp_path),
                video_out_path=str(core_output),
                compress_inference_check_box=bool(request.get("compress_inference")),
                quality_preset=str(request.get("quality_preset") or "clear"),
                max_reference_edge=_optional_int(request.get("max_reference_edge")),
            )
            notes.append(f"Using uploaded reference video: {reference_video_path.name}")
            notes.append(f"Rendered lip-sync video with encoder {runtime.video_encoder}.")

            if subtitle_path and subtitle_path.exists():
                subtitled_output = temp_dir / f"{output_path.stem}_{token}_subtitle.mp4"
                self.ffmpeg.burn_subtitles(
                    final_source,
                    subtitle_path,
                    subtitled_output,
                    style=subtitle_style,
                )
                cleanup_targets.append(subtitled_output)
                final_source = subtitled_output
                notes.append("Burned subtitles into the lip-sync video.")

            overlay_text = str(request.get("overlay_text") or "").strip()
            if overlay_text:
                notes.append("Overlay text is ignored in TuiliONNX lip-sync mode.")

            actual_output_path = _finalize_output(final_source, output_path, notes)
            return actual_output_path, reference_video_path, notes
        finally:
            for target in cleanup_targets:
                if target == output_path:
                    continue
                if target.exists():
                    try:
                        target.unlink()
                    except OSError:
                        pass

    def _get_runtime(self, request: dict[str, object]) -> tuple[object, list[str]]:
        from santiszr.vendor.tuilionnx import LstmSync

        encoder = self.ffmpeg._resolve_gpu_video_encoder()
        key = _RuntimeKey(
            beautify_teeth=bool(request.get("beautify_teeth")),
            batch_size=int(request.get("batch_size") or 4),
            sync_offset=float(request.get("sync_offset") or 0.0),
            scale_h=float(request.get("scale_h") or 1.6),
            scale_w=float(request.get("scale_w") or 3.6),
            compress_inference=bool(request.get("compress_inference")),
            ffmpeg_path=str(self.ffmpeg.ffmpeg_path),
            video_encoder=encoder,
        )
        cached = self._runtimes.get(key)
        if cached is not None:
            self._assert_runtime_gpu(cached)
            return cached, ["Reused cached TuiliONNX GPU runtime.", *self._runtime_notes(cached)]

        checkpoints_root = self.model_root / "checkpoints"
        human_path = checkpoints_root / ("256.onnx" if key.beautify_teeth else "256_m.onnx")
        hubert_path = checkpoints_root / "chinese-hubert-large"
        runtime = LstmSync(
            human_path=str(human_path),
            hubert_path=str(hubert_path),
            checkpoints_root=str(checkpoints_root),
            batch_size=key.batch_size,
            sync_offset=key.sync_offset,
            scale_h=key.scale_h,
            scale_w=key.scale_w,
            compress_inference_check_box=key.compress_inference,
            ffmpeg_path=key.ffmpeg_path,
            video_encoder=key.video_encoder,
        )
        self._assert_runtime_gpu(runtime)
        self._runtimes[key] = runtime
        return runtime, [f"Loaded TuiliONNX GPU runtime from {checkpoints_root}.", *self._runtime_notes(runtime)]

    def _assert_runtime_gpu(self, runtime: object) -> None:
        assert_gpu_runtime = getattr(runtime, "assert_gpu_runtime", None)
        if callable(assert_gpu_runtime):
            assert_gpu_runtime()

    def _runtime_notes(self, runtime: object) -> list[str]:
        runtime_notes = getattr(runtime, "runtime_notes", None)
        if callable(runtime_notes):
            return [str(item) for item in runtime_notes() if str(item).strip()]
        return []


def main() -> int:
    _configure_stdio()
    _configure_windows_runtime()
    runtime = AvatarRenderRuntime()

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
                video_path, reference_video_path, notes = runtime.render(request)
        except Exception as exc:
            _emit({"ok": False, "error": str(exc)})
            continue

        _emit(
            {
                "ok": True,
                "video_path": str(video_path),
                "reference_video_path": str(reference_video_path),
                "notes": list(notes),
            }
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
