from __future__ import annotations

import atexit
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from santiszr.config.settings import load_settings
from santiszr.infra.media.ffmpeg import FFmpegAdapter


class CosyVoiceClient:
    _load_lock = threading.RLock()
    _shared_model: Any | None = None
    _shared_model_dir: Path | None = None
    _shared_sample_rate: int | None = None
    _warmup_completed = False

    _helper_lock = threading.RLock()
    _helper_process: subprocess.Popen[str] | None = None
    _helper_stderr_thread: threading.Thread | None = None
    _helper_shutdown_registered = False
    _helper_stderr_buffer: list[str] = []
    _helper_stderr_limit = 60

    def __init__(
        self,
        *,
        model_dir: str | Path | None = None,
        ffmpeg: FFmpegAdapter | None = None,
    ) -> None:
        settings = load_settings()
        resolved_model_dir = Path(model_dir) if model_dir else settings.models.cosyvoice_model_dir
        self.model_root = resolved_model_dir.expanduser().resolve() if resolved_model_dir else None
        self.model_name = settings.tts.model_name
        self.prompt_max_seconds = max(float(settings.tts.prompt_max_seconds), 1.0)
        self.instruct_text = settings.tts.instruct_text.strip()
        self.prefer_fp16 = settings.tts.prefer_fp16
        self.ffmpeg = ffmpeg or FFmpegAdapter()

        with self._helper_lock:
            if not self.__class__._helper_shutdown_registered:
                atexit.register(self._shutdown_helper)
                self.__class__._helper_shutdown_registered = True

    def list_voices(self) -> list[str]:
        return ["克隆声音"]

    def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str | Path,
        *,
        reference_audio_path: str | Path | None = None,
        speed: float = 1.0,
        sample_rate: int = 22050,
        speaker: str | None = None,
    ) -> tuple[Path, str, list[str]]:
        if self._should_use_helper():
            return self._synthesize_via_helper(
                text=text,
                voice=voice,
                output_path=output_path,
                reference_audio_path=reference_audio_path,
                speed=speed,
                sample_rate=sample_rate,
                speaker=speaker,
            )
        return self._synthesize_in_process(
            text=text,
            voice=voice,
            output_path=output_path,
            reference_audio_path=reference_audio_path,
            speed=speed,
            sample_rate=sample_rate,
            speaker=speaker,
        )

    def _synthesize_in_process(
        self,
        *,
        text: str,
        voice: str,
        output_path: str | Path,
        reference_audio_path: str | Path | None,
        speed: float,
        sample_rate: int,
        speaker: str | None,
    ) -> tuple[Path, str, list[str]]:
        clean_text = text.strip()
        if not clean_text:
            raise RuntimeError("CosyVoice GPU synthesis requires non-empty text.")

        reference = Path(reference_audio_path).expanduser().resolve() if reference_audio_path else None
        if reference is None:
            raise RuntimeError("CosyVoice GPU synthesis requires a reference audio.")
        if not reference.exists() or not reference.is_file():
            raise RuntimeError(f"CosyVoice reference audio is missing: {reference}")

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        converted_reference, notes = self._prepare_reference_audio(reference, output.parent / ".prompt-cache")
        model, actual_sample_rate, torch_mod, torchaudio_mod, load_wav = self._load_model()
        notes.extend(self._warmup_model(model, torch_mod))

        prompt_speech_16k = load_wav(str(converted_reference), 16000)
        chunks: list[Any] = []
        with torch_mod.inference_mode():
            for item in model.inference_instruct2(
                clean_text,
                self.instruct_text,
                prompt_speech_16k,
                stream=False,
                speed=float(speed),
                text_frontend=False,
            ):
                speech = item.get("tts_speech")
                if speech is None:
                    continue
                chunks.append(speech.detach())

        if not chunks:
            raise RuntimeError("CosyVoice GPU synthesis returned no audio chunks.")

        final_speech = chunks[0] if len(chunks) == 1 else torch_mod.cat(chunks, dim=1)
        torchaudio_mod.save(str(output), final_speech.float().cpu(), actual_sample_rate)

        if sample_rate and int(sample_rate) != int(actual_sample_rate):
            notes.append(
                f"CosyVoice kept its native sample rate {actual_sample_rate} Hz instead of requested {sample_rate} Hz."
            )

        return output, "cosyvoice", [f"Using clone reference: {reference.name}", *notes]

    def _prepare_reference_audio(self, reference: Path, cache_dir: Path) -> tuple[Path, list[str]]:
        self._ensure_ffmpeg()
        cache_dir.mkdir(parents=True, exist_ok=True)
        stat = reference.stat()
        cache_key = hashlib.sha1(
            f"{reference.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{self.prompt_max_seconds:.2f}".encode("utf-8")
        ).hexdigest()[:16]
        cached_prompt = cache_dir / f"prompt-{cache_key}.wav"
        if cached_prompt.exists():
            return cached_prompt, ["Reused cached 16k prompt audio."]

        command = [
            str(self.ffmpeg.ffmpeg_path),
            "-y",
            "-i",
            str(reference),
            "-t",
            f"{self.prompt_max_seconds:.2f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(cached_prompt),
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Failed to prepare CosyVoice reference audio.\n"
                f"Command: {' '.join(command)}\n"
                f"{completed.stderr.strip()}"
            )
        return cached_prompt, ["Prepared 16k prompt audio."]

    def _load_model(self) -> tuple[Any, int, Any, Any, Any]:
        self._configure_runtime_paths()
        self._configure_windows_dll_search()
        torch_mod = self._import_required_module("torch")
        torchaudio_mod = self._import_required_module("torchaudio")
        cosyvoice_module = self._import_required_module("cosyvoice.cli.cosyvoice")
        file_utils_module = self._import_required_module("cosyvoice.utils.file_utils")
        cosyvoice_cls = getattr(cosyvoice_module, "CosyVoice2", None)
        load_wav = getattr(file_utils_module, "load_wav", None)
        if cosyvoice_cls is None or load_wav is None:
            raise RuntimeError("Bundled CosyVoice runtime is incomplete.")
        if not torch_mod.cuda.is_available():
            raise RuntimeError("CosyVoice GPU runtime is unavailable.")

        resolved_model_dir = self._resolve_model_dir()
        with self._load_lock:
            if (
                self.__class__._shared_model is not None
                and self.__class__._shared_model_dir == resolved_model_dir
                and self.__class__._shared_sample_rate is not None
            ):
                return (
                    self.__class__._shared_model,
                    int(self.__class__._shared_sample_rate),
                    torch_mod,
                    torchaudio_mod,
                    load_wav,
                )

            fp16_enabled = self.prefer_fp16 and self._supports_fp16(torch_mod)
            self._log_runtime(
                f"Loading CosyVoice2 GPU model from {resolved_model_dir} (fp16={'on' if fp16_enabled else 'off'})..."
            )
            model = cosyvoice_cls(str(resolved_model_dir), fp16=fp16_enabled)
            sample_rate = int(getattr(model, "sample_rate", 24000) or 24000)
            self.__class__._shared_model = model
            self.__class__._shared_model_dir = resolved_model_dir
            self.__class__._shared_sample_rate = sample_rate
            self.__class__._warmup_completed = False
            return model, sample_rate, torch_mod, torchaudio_mod, load_wav

    def _warmup_model(self, model: Any, torch_mod: Any) -> list[str]:
        with self._load_lock:
            if self.__class__._warmup_completed:
                return []

            try:
                self._log_runtime("CosyVoice GPU model loaded. Running warmup pass...")
                warmup_prompt = torch_mod.zeros(1, 16000)
                with torch_mod.inference_mode():
                    for _ in model.inference_instruct2(
                        "预热",
                        self.instruct_text,
                        warmup_prompt,
                        stream=False,
                        speed=1.0,
                        text_frontend=False,
                    ):
                        break
                self.__class__._warmup_completed = True
                self._log_runtime("CosyVoice GPU warmup complete.")
                return ["CosyVoice GPU warmup complete."]
            except Exception as exc:
                self._log_runtime(f"CosyVoice GPU warmup skipped: {exc}")
                return [f"CosyVoice GPU warmup skipped: {exc}"]

    def _resolve_model_dir(self) -> Path:
        if self.model_root is None:
            raise RuntimeError("CosyVoice model directory is not configured.")

        candidates = [self.model_root, self.model_root / self.model_name]
        for candidate in candidates:
            if candidate.exists() and (candidate / "cosyvoice.yaml").is_file():
                return candidate

        expected = self.model_root / self.model_name
        raise RuntimeError(
            f"CosyVoice model files are missing. Expected {expected} or {self.model_root} to contain cosyvoice.yaml."
        )

    def _configure_runtime_paths(self) -> None:
        runtime_root = self._runtime_root()
        if not runtime_root.exists():
            raise RuntimeError(f"Bundled CosyVoice runtime is missing: {runtime_root}")

        for path in (
            runtime_root,
            runtime_root / "third_party" / "AcademiCodec",
            runtime_root / "third_party" / "Matcha-TTS",
        ):
            if not path.exists():
                continue
            text = str(path)
            if text not in sys.path:
                sys.path.insert(0, text)

        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    def _configure_windows_dll_search(self) -> None:
        if os.name != "nt":
            return

        current_path = os.environ.get("PATH", "")
        for directory in (self._local_venv_bin_dir(), self._bundled_cuda_bin_dir()):
            if not directory.exists():
                continue
            directory_text = str(directory)
            add_dll_directory = getattr(os, "add_dll_directory", None)
            if add_dll_directory is not None:
                add_dll_directory(directory_text)
            if directory_text not in current_path.split(os.pathsep):
                current_path = f"{directory_text}{os.pathsep}{current_path}" if current_path else directory_text
        os.environ["PATH"] = current_path

    def _import_required_module(self, name: str) -> Any:
        try:
            return importlib.import_module(name)
        except Exception as exc:
            raise RuntimeError(f"Required module '{name}' is unavailable: {exc}") from exc

    def _supports_fp16(self, torch_mod: Any) -> bool:
        try:
            major, _minor = torch_mod.cuda.get_device_capability(torch_mod.cuda.current_device())
        except Exception:
            return False
        return int(major) >= 7

    def _ensure_ffmpeg(self) -> None:
        if not self.ffmpeg.ffmpeg_path:
            raise RuntimeError("FFmpeg is required for CosyVoice reference audio preparation.")

    def _should_use_helper(self) -> bool:
        if self._helper_mode_enabled():
            return False
        helper_python = self._helper_python()
        return helper_python is not None

    def _current_runtime_ready(self) -> bool:
        try:
            self._configure_runtime_paths()
            self._configure_windows_dll_search()
            for module_name in (
                "torch",
                "torchaudio",
                "hyperpyyaml",
                "modelscope",
                "soundfile",
                "omegaconf",
                "pynini",
                "cosyvoice.cli.cosyvoice",
                "cosyvoice.utils.file_utils",
            ):
                importlib.import_module(module_name)
        except Exception:
            return False
        try:
            torch_mod = importlib.import_module("torch")
            return bool(torch_mod.cuda.is_available())
        except Exception:
            return False

    def _synthesize_via_helper(
        self,
        *,
        text: str,
        voice: str,
        output_path: str | Path,
        reference_audio_path: str | Path | None,
        speed: float,
        sample_rate: int,
        speaker: str | None,
    ) -> tuple[Path, str, list[str]]:
        process = self._ensure_helper_process()
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("CosyVoice helper process is not ready.")

        payload = {
            "text": text,
            "voice": voice,
            "output_path": str(output_path),
            "reference_audio_path": str(reference_audio_path or ""),
            "speed": float(speed),
            "sample_rate": int(sample_rate),
            "speaker": speaker,
        }
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

        response_line = process.stdout.readline()
        if not response_line:
            detail = self._helper_stderr_tail()
            if detail:
                raise RuntimeError(
                    "CosyVoice helper process exited before returning a response.\n"
                    f"Recent helper logs:\n{detail}"
                )
            raise RuntimeError("CosyVoice helper process exited before returning a response.")

        response = json.loads(response_line)
        if not bool(response.get("ok")):
            error_text = str(response.get("error") or "CosyVoice helper synthesis failed.")
            detail = self._helper_stderr_tail()
            if detail and detail not in error_text:
                error_text = f"{error_text}\nRecent helper logs:\n{detail}"
            raise RuntimeError(error_text)

        output = Path(str(response.get("audio_path") or output_path))
        return output, str(response.get("provider") or "cosyvoice"), list(response.get("notes") or [])

    def _ensure_helper_process(self) -> subprocess.Popen[str]:
        helper_python = self._helper_python()
        if helper_python is None:
            raise RuntimeError("CosyVoice helper runtime is missing.")

        with self._helper_lock:
            process = self.__class__._helper_process
            if process is not None and process.poll() is None:
                return process

            env = os.environ.copy()
            src_path = str(Path(__file__).resolve().parents[3])
            env["PYTHONPATH"] = src_path if not env.get("PYTHONPATH") else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["SANTISZR_TTS_HELPER_MODE"] = "1"

            process = subprocess.Popen(
                [str(helper_python), "-u", "-m", "santiszr.infra.tts.cosyvoice_helper"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(Path(__file__).resolve().parents[4]),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.__class__._helper_process = process
            self.__class__._helper_stderr_buffer = []
            self.__class__._helper_stderr_thread = threading.Thread(
                target=self._consume_helper_stderr,
                args=(process,),
                daemon=True,
            )
            self.__class__._helper_stderr_thread.start()
            return process

    def _consume_helper_stderr(self, process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            if not line:
                continue
            normalized = self._normalize_helper_stderr_line(line)
            if not normalized:
                continue
            with self._helper_lock:
                self.__class__._helper_stderr_buffer.append(normalized)
                if len(self.__class__._helper_stderr_buffer) > self.__class__._helper_stderr_limit:
                    self.__class__._helper_stderr_buffer = self.__class__._helper_stderr_buffer[
                        -self.__class__._helper_stderr_limit :
                    ]
            if self._helper_stderr_verbose():
                sys.stderr.write(normalized + "\n")
                sys.stderr.flush()

    def _helper_python(self) -> Path | None:
        runtime = Path(__file__).resolve().parents[4] / "tools" / "cosyvoice_python" / "python.exe"
        return runtime if runtime.exists() else None

    def _helper_stderr_verbose(self) -> bool:
        return os.getenv("SANTISZR_TTS_VERBOSE_STDERR", "").strip().lower() in {"1", "true", "yes", "on"}

    def _helper_mode_enabled(self) -> bool:
        return os.getenv("SANTISZR_TTS_HELPER_MODE", "").strip().lower() in {"1", "true", "yes", "on"}

    def _shutdown_helper(self) -> None:
        with self._helper_lock:
            process = self.__class__._helper_process
            self.__class__._helper_process = None
            self.__class__._helper_stderr_buffer = []
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)

    def _runtime_root(self) -> Path:
        return Path(__file__).resolve().parents[4] / "tools" / "cosyvoice_runtime"

    def _bundled_cuda_bin_dir(self) -> Path:
        return Path(__file__).resolve().parents[4] / "tools" / "nvidia" / "cuda" / "bin"

    def _local_venv_bin_dir(self) -> Path:
        executable = Path(sys.executable).resolve()
        return executable.parent.parent / "Library" / "bin"

    def _log_runtime(self, message: str) -> None:
        sys.stderr.write(message.rstrip() + "\n")
        sys.stderr.flush()

    def _helper_stderr_tail(self, limit: int = 12) -> str:
        with self._helper_lock:
            lines = list(self.__class__._helper_stderr_buffer[-limit:])
        return "\n".join(line for line in lines if line.strip())

    def _normalize_helper_stderr_line(self, line: str) -> str:
        text = line.replace("\x00", "").strip()
        if not text:
            return ""
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        return text.strip()
