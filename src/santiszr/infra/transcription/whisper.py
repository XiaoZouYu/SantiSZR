from __future__ import annotations

import importlib
import importlib.util
import io
import os
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

from santiszr.config.settings import load_settings
from santiszr.infra.media.ffmpeg import FFmpegAdapter


_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


class WhisperTranscriber:
    def __init__(
        self,
        *,
        model_name: str = "small",
        model_dir: str | Path | None = None,
        ffmpeg: FFmpegAdapter | None = None,
    ) -> None:
        settings = load_settings()
        resolved_model_dir = Path(model_dir) if model_dir else settings.models.whisper_model_dir
        self.model_name = model_name
        self.model_dir = resolved_model_dir.expanduser().resolve() if resolved_model_dir else None
        self.ffmpeg = ffmpeg or FFmpegAdapter()
        self._model: Any | None = None
        self._warmup_completed = False
        self.last_runtime: str = "unknown"
        self.last_runtime_fallback_reason: str | None = None
        self.quick_mode = self._env_flag("SANTISZR_WHISPER_QUICK_MODE", default=True)
        self.preferred_device = os.getenv("SANTISZR_WHISPER_DEVICE", "cuda").strip().lower() or "cuda"
        self.preferred_compute_type = os.getenv("SANTISZR_WHISPER_COMPUTE_TYPE", "").strip().lower() or None
        self.warmup_enabled = self._env_flag("SANTISZR_WHISPER_WARMUP", default=True)
        self._dll_search_handles: list[Any] = []
        self._dll_search_paths: set[str] = set()

    def transcribe(
        self,
        source: str | Path,
        *,
        source_headers: dict[str, str] | None = None,
        language: str = "zh",
    ) -> str:
        source_value = str(source)
        media_input: str | Path | io.BytesIO
        model = self._load_model()

        if self._is_local_media(source_value):
            media_input = str(Path(source_value).resolve())
        else:
            media_input = self._extract_audio_buffer(source_value, source_headers=source_headers)

        segments, _ = model.transcribe(
            media_input,
            language=language,
            beam_size=1 if self.quick_mode else 5,
            best_of=1 if self.quick_mode else 5,
            vad_filter=True,
            condition_on_previous_text=not self.quick_mode,
            temperature=0,
        )
        text_parts = [segment.text.strip() for segment in segments if getattr(segment, "text", "").strip()]
        transcript = " ".join(text_parts).strip()
        if not transcript:
            raise RuntimeError("Whisper transcription returned empty text.")
        return transcript

    def transcribe_stream(
        self,
        source: str | Path,
        *,
        source_headers: dict[str, str] | None = None,
        language: str = "zh",
    ) -> str:
        return self.transcribe(source, source_headers=source_headers, language=language)

    def ensure_ready(self) -> Any:
        return self._load_model()

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        self._configure_windows_dll_search()

        try:
            self._ensure_ctranslate2_runtime()
            faster_whisper = importlib.import_module("faster_whisper")
        except ImportError as exc:
            raise RuntimeError("faster_whisper is not installed.") from exc

        whisper_model = getattr(faster_whisper, "WhisperModel", None)
        if whisper_model is None:
            raise RuntimeError("faster_whisper.WhisperModel is unavailable.")

        device, compute_type, runtime_reason = self._resolve_runtime()
        model_source = self._resolve_model_source(device=device)
        kwargs: dict[str, Any] = {"device": device, "compute_type": compute_type}
        if self.model_dir:
            self.model_dir.mkdir(parents=True, exist_ok=True)
            kwargs["download_root"] = str(self.model_dir)

        try:
            self._log_runtime(f"Loading Whisper model '{model_source}' on {device}...")
            self._model = whisper_model(model_source, **kwargs)
            self.last_runtime = device
            self.last_runtime_fallback_reason = runtime_reason
            if device == "cuda" and self.warmup_enabled and not self._warmup_completed:
                self._warmup_model(self._model)
            return self._model
        except Exception as exc:
            self._model = None
            raise RuntimeError(f"Failed to initialize Whisper on {device}: {exc}") from exc

    def _resolve_runtime(self) -> tuple[str, str, str | None]:
        if self.preferred_device == "cpu":
            raise RuntimeError("Whisper CPU mode is disabled. Transcription requires GPU.")

        if self.preferred_device not in {"auto", "cuda"}:
            raise RuntimeError(f"Unsupported Whisper device setting: {self.preferred_device}")

        if not self._cuda_runtime_ready():
            raise RuntimeError("Whisper GPU runtime is unavailable.")

        return "cuda", self.preferred_compute_type or "float16", None

    def _resolve_model_source(self, *, device: str) -> str:
        if self.model_dir:
            self.model_dir.mkdir(parents=True, exist_ok=True)
            local_model_dir = self.model_dir / self.model_name
            if local_model_dir.exists():
                return str(local_model_dir)
        return self.model_name

    def _cuda_runtime_ready(self) -> bool:
        try:
            ctranslate2 = self._ensure_ctranslate2_runtime()
            if ctranslate2.get_cuda_device_count() <= 0:
                return False
        except Exception:
            return False

        required_dlls = {"cublas64_12.dll", "cublasLt64_12.dll"}
        search_dirs = []
        cuda_path = os.environ.get("CUDA_PATH")
        if cuda_path:
            search_dirs.append(Path(cuda_path) / "bin")
        for env_path in os.environ.get("PATH", "").split(os.pathsep):
            if env_path:
                search_dirs.append(Path(env_path))
        search_dirs.append(self._bundled_cuda_bin_dir())
        search_dirs.append(Path(__file__).resolve().parent)

        for directory in search_dirs:
            if not directory.exists():
                continue
            if all((directory / dll).exists() for dll in required_dlls):
                return True
        return False

    def _configure_windows_dll_search(self) -> None:
        if os.name != "nt":
            return

        candidate_dirs = []
        cuda_path = os.environ.get("CUDA_PATH", "").strip()
        if cuda_path:
            candidate_dirs.append(Path(cuda_path) / "bin")
        candidate_dirs.append(self._bundled_cuda_bin_dir())

        add_dll_directory = getattr(os, "add_dll_directory", None)
        if add_dll_directory is None:
            return

        for directory in candidate_dirs:
            if not directory.exists():
                continue
            directory_text = str(directory.resolve())
            if directory_text in self._dll_search_paths:
                continue
            self._dll_search_paths.add(directory_text)
            try:
                self._dll_search_handles.append(add_dll_directory(directory_text))
            except OSError:
                continue
            self._prepend_cuda_bin_to_path(directory)

    def _prepend_cuda_bin_to_path(self, directory: Path) -> None:
        if not (directory / "cublas64_12.dll").exists():
            return

        directory_text = str(directory.resolve())
        current_path = os.environ.get("PATH", "")
        path_entries = current_path.split(os.pathsep) if current_path else []
        normalized_entries = {entry.lower() for entry in path_entries}
        if directory_text.lower() in normalized_entries:
            return
        os.environ["PATH"] = f"{directory_text}{os.pathsep}{current_path}" if current_path else directory_text

    def _bundled_cuda_bin_dir(self) -> Path:
        return Path(__file__).resolve().parents[4] / "tools" / "nvidia" / "cuda" / "bin"

    def _ensure_ctranslate2_runtime(self) -> Any:
        existing_module = sys.modules.get("ctranslate2")
        shim_init = Path(__file__).resolve().parents[3] / "ctranslate2" / "__init__.py"
        shim_package_dir = shim_init.parent

        if existing_module is not None:
            module_file = getattr(existing_module, "__file__", "")
            try:
                if module_file and Path(module_file).resolve() == shim_init.resolve():
                    self._ensure_ctranslate2_models(existing_module)
                    return existing_module
            except Exception:
                pass

        if not shim_init.exists():
            module = importlib.import_module("ctranslate2")
            self._ensure_ctranslate2_models(module)
            return module

        for module_name in list(sys.modules):
            if module_name == "ctranslate2" or module_name.startswith("ctranslate2."):
                sys.modules.pop(module_name, None)

        spec = importlib.util.spec_from_file_location(
            "ctranslate2",
            shim_init,
            submodule_search_locations=[str(shim_package_dir)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load the bundled ctranslate2 shim from {shim_init}.")

        module = importlib.util.module_from_spec(spec)
        sys.modules["ctranslate2"] = module
        spec.loader.exec_module(module)
        self._ensure_ctranslate2_models(module)
        return module

    def _ensure_ctranslate2_models(self, module: Any) -> None:
        if getattr(module, "models", None) is not None:
            return
        try:
            models_module = importlib.import_module("ctranslate2.models")
            setattr(module, "models", models_module)
        except Exception as exc:
            raise RuntimeError(
                "ctranslate2.models is unavailable. Reinstall the Python environment or run install-windows-prereqs.bat."
            ) from exc

    def _warmup_model(self, model: Any) -> None:
        warmup_audio = self._silent_wav_buffer()

        try:
            self._log_runtime("Whisper GPU model loaded. Running warmup pass...")
            segments, _ = model.transcribe(
                warmup_audio,
                language="zh",
                beam_size=1,
                best_of=1,
                vad_filter=False,
                condition_on_previous_text=False,
                temperature=0,
            )
            for _ in segments:
                pass
            self._warmup_completed = True
            self._log_runtime("Whisper GPU warmup complete.")
        except Exception as exc:
            raise RuntimeError(f"Whisper GPU warmup failed: {exc}") from exc

    def _silent_wav_buffer(self, *, sample_rate: int = 16000, duration_sec: float = 0.25) -> io.BytesIO:
        frame_count = max(int(sample_rate * duration_sec), 1)
        silence = b"\x00\x00" * frame_count
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(silence)
        buffer.seek(0)
        return buffer

    def _log_runtime(self, message: str) -> None:
        sys.stderr.write(message.rstrip() + "\n")
        sys.stderr.flush()

    def _extract_audio_buffer(
        self,
        source: str,
        *,
        source_headers: dict[str, str] | None = None,
    ) -> io.BytesIO:
        if not self.ffmpeg.ffmpeg_path:
            raise RuntimeError("FFmpeg is required for media transcription.")

        command = [str(self.ffmpeg.ffmpeg_path), "-hide_banner", "-loglevel", "error", "-i", source]
        if source_headers:
            header_blob = "".join(f"{key}: {value}\r\n" for key, value in source_headers.items())
            command = [str(self.ffmpeg.ffmpeg_path), "-hide_banner", "-loglevel", "error", "-headers", header_blob, "-i", source]
        command.extend(
            [
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "wav",
                "pipe:1",
            ]
        )
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=False,
        )
        return io.BytesIO(completed.stdout)

    def _is_local_media(self, source: str) -> bool:
        path = Path(source)
        return path.exists() and path.is_file()

    def _env_flag(self, name: str, *, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() not in {"0", "false", "no", "off"}
