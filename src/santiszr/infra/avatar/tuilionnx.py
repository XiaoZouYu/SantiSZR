from __future__ import annotations

import atexit
import json
import os
import re
import subprocess
import threading
from pathlib import Path

from santiszr.config.settings import load_settings
from santiszr.infra.media.ffmpeg import FFmpegAdapter
from santiszr.domain.schemas.subtitle import SubtitleStyle


class TuiliOnnxAdapter:
    _helper_lock = threading.RLock()
    _helper_process: subprocess.Popen[str] | None = None
    _helper_stderr_thread: threading.Thread | None = None
    _helper_shutdown_registered = False
    _helper_stderr_buffer: list[str] = []
    _helper_stderr_limit = 120

    def __init__(
        self,
        ffmpeg: FFmpegAdapter | None = None,
        model_root: str | Path | None = None,
        helper_python: str | Path | None = None,
    ) -> None:
        settings = load_settings()
        configured_root = model_root or settings.avatar.tuilionnx_root or settings.models.tuilionnx_model_dir
        self.model_root = Path(configured_root).expanduser().resolve() if configured_root else None
        configured_python = helper_python or settings.avatar.tuilionnx_python
        self.helper_python = Path(configured_python).expanduser().resolve() if configured_python else None
        self.ffmpeg = ffmpeg or FFmpegAdapter()

        with self._helper_lock:
            if not self.__class__._helper_shutdown_registered:
                atexit.register(self._shutdown_helper)
                self.__class__._helper_shutdown_registered = True

    def list_models(self) -> list[str]:
        return []

    def resolve_model_asset(self, model_id: str) -> Path | None:
        del model_id
        return None

    def render(
        self,
        *,
        audio_path: str | Path,
        model_id: str,
        output_path: str | Path,
        subtitle_path: str | Path | None = None,
        subtitle_style: SubtitleStyle | None = None,
        background_video_path: str | Path | None = None,
        overlay_text: str | None = None,
        resolution: str = "1080p",
        fps: int = 25,
        batch_size: int = 4,
        sync_offset: float = 0.0,
        scale_h: float = 1.6,
        scale_w: float = 3.6,
        compress_inference: bool = False,
        beautify_teeth: bool = False,
        add_ai_watermark: bool = False,
    ) -> tuple[Path, Path | None, list[str]]:
        del model_id, resolution, fps

        reference_video_path = Path(background_video_path).expanduser().resolve() if background_video_path else None
        if reference_video_path is None:
            raise RuntimeError("TuiliONNX GPU rendering requires an uploaded reference video.")
        if not reference_video_path.exists() or not reference_video_path.is_file():
            raise RuntimeError(f"TuiliONNX reference video is missing: {reference_video_path}")

        source_audio_path = Path(audio_path).expanduser().resolve()
        if not source_audio_path.exists() or not source_audio_path.is_file():
            raise RuntimeError(f"TuiliONNX driving audio is missing: {source_audio_path}")

        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        process = self._ensure_helper_process()
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("TuiliONNX helper process is not ready.")

        payload = {
            "audio_path": str(source_audio_path),
            "reference_video_path": str(reference_video_path),
            "output_path": str(output),
            "subtitle_path": str(Path(subtitle_path).expanduser().resolve()) if subtitle_path else None,
            "subtitle_style": subtitle_style.model_dump(mode="json") if subtitle_style else None,
            "overlay_text": overlay_text or "",
            "batch_size": int(batch_size),
            "sync_offset": float(sync_offset),
            "scale_h": float(scale_h),
            "scale_w": float(scale_w),
            "compress_inference": bool(compress_inference),
            "beautify_teeth": bool(beautify_teeth),
            "add_ai_watermark": bool(add_ai_watermark),
        }
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

        response_line = process.stdout.readline()
        if not response_line:
            detail = self._helper_stderr_tail()
            if detail:
                raise RuntimeError(
                    "TuiliONNX helper process exited before returning a response.\n"
                    f"Recent helper logs:\n{detail}"
                )
            raise RuntimeError("TuiliONNX helper process exited before returning a response.")

        response = json.loads(response_line)
        if not bool(response.get("ok")):
            error_text = str(response.get("error") or "TuiliONNX helper render failed.")
            detail = self._helper_stderr_tail()
            if detail and detail not in error_text:
                error_text = f"{error_text}\nRecent helper logs:\n{detail}"
            raise RuntimeError(error_text)

        notes = [str(item) for item in (response.get("notes") or []) if str(item).strip()]
        video_path = Path(str(response.get("video_path") or output)).expanduser().resolve()
        asset_path = Path(str(response.get("reference_video_path") or reference_video_path)).expanduser().resolve()
        return video_path, asset_path, notes

    def _ensure_helper_process(self) -> subprocess.Popen[str]:
        if self.helper_python is None or not self.helper_python.exists():
            detail = str(self.helper_python) if self.helper_python is not None else "not configured"
            raise RuntimeError(
                "TuiliONNX helper runtime is missing. "
                "Configure SANTISZR_TUILIONNX_PYTHON or provide tools/tuilionnx_python/python.exe "
                "(fallback: tools/cosyvoice_python/python.exe). "
                f"Current value: {detail}"
            )
        if self.model_root is None:
            raise RuntimeError("TuiliONNX model directory is not configured.")

        with self._helper_lock:
            process = self.__class__._helper_process
            if process is not None and process.poll() is None:
                return process

            env = os.environ.copy()
            src_path = str(Path(__file__).resolve().parents[3])
            env["PYTHONPATH"] = src_path if not env.get("PYTHONPATH") else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["SANTISZR_TUILIONNX_HELPER_MODE"] = "1"
            env["SANTISZR_TUILIONNX_MODEL_DIR"] = str(self.model_root)
            env["SANTISZR_TUILIONNX_PYTHON"] = str(self.helper_python)

            process = subprocess.Popen(
                [str(self.helper_python), "-u", "-m", "santiszr.infra.avatar.tuilionnx_helper"],
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
            text = self._normalize_helper_stderr_line(line)
            if not text:
                continue
            with self._helper_lock:
                self.__class__._helper_stderr_buffer.append(text)
                if len(self.__class__._helper_stderr_buffer) > self.__class__._helper_stderr_limit:
                    self.__class__._helper_stderr_buffer = self.__class__._helper_stderr_buffer[
                        -self.__class__._helper_stderr_limit :
                    ]

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

    def _helper_stderr_tail(self, limit: int = 14) -> str:
        with self._helper_lock:
            lines = list(self.__class__._helper_stderr_buffer[-limit:])
        return "\n".join(line for line in lines if line.strip())

    def _normalize_helper_stderr_line(self, line: str) -> str:
        text = line.replace("\x00", "").strip()
        if not text:
            return ""
        return re.sub(r"\x1b\[[0-9;]*m", "", text).strip()
