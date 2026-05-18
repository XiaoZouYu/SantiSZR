from __future__ import annotations

import atexit
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from santiszr.config.settings import load_settings
from santiszr.infra.media.ffmpeg import FFmpegAdapter


class VoxCPMClient:
    _helper_lock = threading.RLock()
    _helper_process: subprocess.Popen[str] | None = None
    _helper_stderr_thread: threading.Thread | None = None
    _helper_shutdown_registered = False
    _helper_stderr_buffer: list[str] = []
    _helper_stderr_limit = 80

    def __init__(
        self,
        *,
        model_dir: str | Path | None = None,
        ffmpeg: FFmpegAdapter | None = None,
    ) -> None:
        settings = load_settings()
        resolved_model_dir = Path(model_dir) if model_dir else settings.models.voxcpm_model_dir
        self.model_dir = resolved_model_dir.expanduser().resolve() if resolved_model_dir else None
        self.ffmpeg = ffmpeg or FFmpegAdapter()
        self.provider = settings.tts.provider or "voxcpm2"

        with self._helper_lock:
            if not self.__class__._helper_shutdown_registered:
                atexit.register(self._shutdown_helper)
                self.__class__._helper_shutdown_registered = True

    def list_voices(self) -> list[str]:
        return ["reference-clone"]

    def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str | Path,
        *,
        reference_audio_path: str | Path | None = None,
        ultimate_clone: bool = False,
        prompt_text: str | None = None,
        speed: float = 1.0,
        sample_rate: int = 22050,
        speaker: str | None = None,
    ) -> tuple[Path, str, list[str]]:
        del speaker

        clean_text = text.strip()
        if not clean_text:
            raise RuntimeError("VoxCPM2 GPU synthesis requires non-empty text.")

        reference = Path(reference_audio_path).expanduser().resolve() if reference_audio_path else None
        if reference is None:
            raise RuntimeError("VoxCPM2 GPU synthesis requires a reference audio.")
        if not reference.exists() or not reference.is_file():
            raise RuntimeError(f"VoxCPM2 reference audio is missing: {reference}")

        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        process = self._ensure_helper_process()
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("VoxCPM2 helper process is not ready.")

        payload = {
            "text": clean_text,
            "voice": voice,
            "output_path": str(output),
            "reference_audio_path": str(reference),
            "ultimate_clone": bool(ultimate_clone),
            "speed": float(speed),
            "sample_rate": int(sample_rate),
            "provider": self.provider,
        }
        clean_prompt_text = str(prompt_text or "").strip()
        if clean_prompt_text:
            payload["prompt_text"] = clean_prompt_text
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

        response_line = process.stdout.readline()
        if not response_line:
            detail = self._helper_stderr_tail()
            if detail:
                raise RuntimeError(
                    "VoxCPM2 helper process exited before returning a response.\n"
                    f"Recent helper logs:\n{detail}"
                )
            raise RuntimeError("VoxCPM2 helper process exited before returning a response.")

        response = json.loads(response_line)
        if not bool(response.get("ok")):
            error_text = str(response.get("error") or "VoxCPM2 helper synthesis failed.")
            detail = self._helper_stderr_tail()
            if detail and detail not in error_text:
                error_text = f"{error_text}\nRecent helper logs:\n{detail}"
            raise RuntimeError(error_text)

        notes = [str(item) for item in (response.get("notes") or []) if str(item).strip()]
        for note in notes:
            self._log_runtime(note)

        audio_path = Path(str(response.get("audio_path") or output)).expanduser().resolve()
        return audio_path, "voxcpm2", notes

    def _ensure_helper_process(self) -> subprocess.Popen[str]:
        helper_python = self._helper_python()
        if helper_python is None:
            raise RuntimeError("VoxCPM2 helper runtime is missing: tools/voxcpm_python/python.exe")
        if self.model_dir is None:
            raise RuntimeError("VoxCPM2 model directory is not configured.")

        with self._helper_lock:
            process = self.__class__._helper_process
            if process is not None and process.poll() is None:
                return process

            env = os.environ.copy()
            src_path = str(Path(__file__).resolve().parents[3])
            env["PYTHONPATH"] = src_path if not env.get("PYTHONPATH") else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["SANTISZR_VOXCPM_HELPER_MODE"] = "1"
            env["SANTISZR_VOXCPM_MODEL_DIR"] = str(self.model_dir)
            env["SANTISZR_VOXCPM_PYTHON"] = str(helper_python)

            process = subprocess.Popen(
                [str(helper_python), "-u", "-m", "santiszr.infra.tts.voxcpm_helper"],
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
            normalized = self._normalize_helper_stderr_line(line)
            if not normalized:
                continue
            with self._helper_lock:
                self.__class__._helper_stderr_buffer.append(normalized)
                if len(self.__class__._helper_stderr_buffer) > self.__class__._helper_stderr_limit:
                    self.__class__._helper_stderr_buffer = self.__class__._helper_stderr_buffer[
                        -self.__class__._helper_stderr_limit :
                    ]

    def _helper_python(self) -> Path | None:
        configured = str(os.getenv("SANTISZR_VOXCPM_PYTHON") or "").strip()
        if configured:
            candidate = Path(configured).expanduser().resolve()
            return candidate if candidate.exists() else None
        runtime = Path(__file__).resolve().parents[4] / "tools" / "voxcpm_python" / "python.exe"
        return runtime if runtime.exists() else None

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

    @classmethod
    def shutdown_shared_helper(cls) -> None:
        probe = cls.__new__(cls)
        cls._shutdown_helper(probe)

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
