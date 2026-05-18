from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from santiszr.config.settings import load_settings


class ScriptSubtitleGenerator:
    def __init__(
        self,
        *,
        python_executable: str | Path | None = None,
        script_path: str | Path | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        self.python_executable = str(python_executable) if python_executable else self._default_python()
        self.script_path = str(script_path) if script_path else self._default_script_path()
        self.timeout_sec = timeout_sec if timeout_sec is not None else float(os.getenv("SANTISZR_SUBTITLE_TIMEOUT_SEC", "120"))

    def available(self) -> bool:
        return bool(self.script_path and Path(self.script_path).exists())

    def generate(self, audio_path: str | Path, output_path: str | Path) -> tuple[Path, list[str]]:
        if not self.available():
            raise RuntimeError("Subtitle generation script is not available.")

        audio = Path(audio_path)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.python_executable,
            self.script_path,
            "--audio",
            str(audio),
            "--output",
            str(output),
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_sec,
            cwd=str(Path(self.script_path).parent),
            env=self._build_runtime_env(),
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(stderr or f"Subtitle generation script failed with exit code {completed.returncode}.")
        if not output.exists():
            raise RuntimeError("Subtitle generation script finished without producing an SRT file.")
        return output, ["Generated subtitle via external script."]

    def _build_runtime_env(self) -> dict[str, str]:
        settings = load_settings()
        env = os.environ.copy()
        ffmpeg_path = settings.media.ffmpeg_path
        if ffmpeg_path:
            ffmpeg_bin = Path(ffmpeg_path).expanduser().resolve().parent
            env["PATH"] = str(ffmpeg_bin) + os.pathsep + env.get("PATH", "")
        env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        return env

    def _default_python(self) -> str:
        candidates = [
            os.getenv("SANTISZR_SUBTITLE_PYTHON"),
            sys.executable,
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return str(candidate)
        return sys.executable

    def _default_script_path(self) -> str | None:
        configured = os.getenv("SANTISZR_SUBTITLE_SCRIPT")
        if configured:
            return configured
        project_script = Path(__file__).resolve().parents[4] / "scripts" / "generate_srt.py"
        if project_script.exists():
            return str(project_script)
        return None
