from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Literal

from pydantic import BaseModel, Field

from santiszr.config.models import WindowSettings


class LLMSettings(BaseModel):
    api_key: str | None = None
    api_base: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    timeout_sec: float = 60.0


class TTSSettings(BaseModel):
    provider: str = "voxcpm2"
    base_url: str = "http://127.0.0.1:9880"
    timeout_sec: float = 120.0
    default_voice: str = "reference-clone"
    health_path: str = "/health"
    startup_timeout_sec: float = 30.0
    startup_command: str | None = None
    model_name: str = "VoxCPM2"
    prompt_max_seconds: float = 29.5
    instruct_text: str = (
        "请尽量保持参考音频说话人的声音特征，用自然、亲切、口语化的短视频讲解风格朗读这段内容。"
    )
    prefer_fp16: bool = True
    voxcpm_cfg_value: float = 2.0
    voxcpm_inference_timesteps: int = 10
    voxcpm_retry_badcase: bool = True


class MediaSettings(BaseModel):
    ffmpeg_path: Path | None = None
    ffprobe_path: Path | None = None


class ModelSettings(BaseModel):
    root_dir: Path | None = None
    cosyvoice_model_dir: Path | None = None
    voxcpm_model_dir: Path | None = None
    whisper_model_dir: Path | None = None
    tuilionnx_model_dir: Path | None = None


class AvatarSettings(BaseModel):
    tuilionnx_root: Path | None = None
    tuilionnx_python: Path | None = None
    default_model_id: str = "uploaded-avatar"


class PublishSettings(BaseModel):
    external_publisher_root: Path | None = None
    python_executable: Path | None = None


class AppSettings(BaseModel):
    app_name: str = "SantiSZR"
    app_env: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    data_dir: Path | None = None
    cache_dir: Path | None = None
    log_dir: Path | None = None
    main_window: WindowSettings = Field(default_factory=WindowSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    media: MediaSettings = Field(default_factory=MediaSettings)
    models: ModelSettings = Field(default_factory=ModelSettings)
    avatar: AvatarSettings = Field(default_factory=AvatarSettings)
    publish: PublishSettings = Field(default_factory=PublishSettings)


def load_settings() -> AppSettings:
    project_root = _default_project_root()
    model_root = _parse_path(os.getenv("SANTISZR_MODEL_ROOT")) or project_root / "models"
    default_tuilionnx_python = _first_existing_path(
        project_root / "tools" / "tuilionnx_python" / "python.exe",
        project_root / "tools" / "cosyvoice_python" / "python.exe",
    )
    cosyvoice_model_dir = _parse_path(os.getenv("SANTISZR_COSYVOICE_MODEL_DIR")) or (model_root / "cosyvoice")
    voxcpm_model_dir = _parse_path(os.getenv("SANTISZR_VOXCPM_MODEL_DIR")) or (model_root / "voxcpm" / "VoxCPM2")
    whisper_model_dir = _parse_path(os.getenv("SANTISZR_WHISPER_MODEL_DIR")) or (model_root / "whisper")
    tuilionnx_model_dir = _parse_path(os.getenv("SANTISZR_TUILIONNX_MODEL_DIR")) or (model_root / "tuilionnx")
    tuilionnx_root_override = _parse_path(os.getenv("SANTISZR_TUILIONNX_ROOT"))

    return AppSettings(
        app_name=os.getenv("SANTISZR_APP_NAME", "SantiSZR"),
        app_env=os.getenv("SANTISZR_APP_ENV", "development"),
        debug=_parse_bool(os.getenv("SANTISZR_DEBUG"), default=False),
        log_level=os.getenv("SANTISZR_LOG_LEVEL", "INFO"),
        log_format=os.getenv("SANTISZR_LOG_FORMAT", "text"),
        data_dir=_parse_path(os.getenv("SANTISZR_DATA_DIR")),
        cache_dir=_parse_path(os.getenv("SANTISZR_CACHE_DIR")),
        log_dir=_parse_path(os.getenv("SANTISZR_LOG_DIR")),
        main_window=WindowSettings(
            title=os.getenv("SANTISZR_MAIN_WINDOW__TITLE", "SantiSZR"),
            width=int(os.getenv("SANTISZR_MAIN_WINDOW__WIDTH", "1280")),
            height=int(os.getenv("SANTISZR_MAIN_WINDOW__HEIGHT", "800")),
            min_width=int(os.getenv("SANTISZR_MAIN_WINDOW__MIN_WIDTH", "1100")),
            min_height=int(os.getenv("SANTISZR_MAIN_WINDOW__MIN_HEIGHT", "720")),
        ),
        llm=LLMSettings(
            api_key=os.getenv("SANTISZR_LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY"),
            api_base=os.getenv("SANTISZR_LLM_API_BASE", "https://api.deepseek.com/v1"),
            model=os.getenv("SANTISZR_LLM_MODEL", "deepseek-chat"),
            timeout_sec=float(os.getenv("SANTISZR_LLM_TIMEOUT_SEC", "60")),
        ),
        tts=TTSSettings(
            provider=os.getenv("SANTISZR_TTS_PROVIDER", "voxcpm2"),
            base_url=os.getenv("SANTISZR_TTS_BASE_URL", "http://127.0.0.1:9880"),
            timeout_sec=float(os.getenv("SANTISZR_TTS_TIMEOUT_SEC", "120")),
            default_voice=os.getenv("SANTISZR_TTS_DEFAULT_VOICE", "reference-clone"),
            health_path=os.getenv("SANTISZR_TTS_HEALTH_PATH", "/health"),
            startup_timeout_sec=float(os.getenv("SANTISZR_TTS_STARTUP_TIMEOUT_SEC", "30")),
            startup_command=os.getenv("SANTISZR_TTS_STARTUP_COMMAND"),
            model_name=os.getenv("SANTISZR_TTS_MODEL_NAME", "VoxCPM2"),
            prompt_max_seconds=float(os.getenv("SANTISZR_TTS_PROMPT_MAX_SECONDS", "29.5")),
            instruct_text=os.getenv(
                "SANTISZR_TTS_INSTRUCT_TEXT",
                "请尽量保持参考音频说话人的声音特征，用自然、亲切、口语化的短视频讲解风格朗读这段内容。",
            ),
            prefer_fp16=_parse_bool(os.getenv("SANTISZR_TTS_PREFER_FP16"), default=True),
            voxcpm_cfg_value=float(os.getenv("SANTISZR_TTS_VOXCPM_CFG_VALUE", "2.0")),
            voxcpm_inference_timesteps=int(os.getenv("SANTISZR_TTS_VOXCPM_INFERENCE_TIMESTEPS", "10")),
            voxcpm_retry_badcase=_parse_bool(os.getenv("SANTISZR_TTS_VOXCPM_RETRY_BADCASE"), default=True),
        ),
        media=MediaSettings(
            ffmpeg_path=_parse_path(os.getenv("SANTISZR_FFMPEG_PATH")),
            ffprobe_path=_parse_path(os.getenv("SANTISZR_FFPROBE_PATH")),
        ),
        models=ModelSettings(
            root_dir=model_root,
            cosyvoice_model_dir=cosyvoice_model_dir,
            voxcpm_model_dir=voxcpm_model_dir,
            whisper_model_dir=whisper_model_dir,
            tuilionnx_model_dir=tuilionnx_model_dir,
        ),
        avatar=AvatarSettings(
            tuilionnx_root=tuilionnx_root_override or tuilionnx_model_dir,
            tuilionnx_python=_parse_path(os.getenv("SANTISZR_TUILIONNX_PYTHON")) or default_tuilionnx_python,
            default_model_id=os.getenv("SANTISZR_TUILIONNX_DEFAULT_MODEL", "uploaded-avatar"),
        ),
        publish=PublishSettings(
            external_publisher_root=_parse_path(os.getenv("SANTISZR_EXTERNAL_PUBLISHER_ROOT")),
            python_executable=_parse_path(os.getenv("SANTISZR_PUBLISH_PYTHON"))
            or _parse_path(sys.executable),
        ),
    )


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_path(value: str | None) -> Path | None:
    return Path(value) if value else None


def _first_existing_path(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]
