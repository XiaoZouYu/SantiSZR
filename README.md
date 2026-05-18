# SantiSZR

SantiSZR is a standalone desktop workflow for local short-video production. The
current repository contains the typed schemas, services, worker protocol, and
GUI task orchestration used by the desktop app.

## Runtime Requirements

- Python 3.12
- `uv`
- FFmpeg available from `PATH`, explicitly configured with
  `SANTISZR_FFMPEG_PATH` / `SANTISZR_FFPROBE_PATH`, or bundled under
  `tools/ffmpeg/bin/`
- VoxCPM2 model files under `models/voxcpm/VoxCPM2/`
- bundled VoxCPM Python runtime under `tools/voxcpm_python/python.exe`
- Whisper model directory under `models/whisper/`
- TuiliONNX model directory under `models/tuilionnx/`
- bundled TuiliONNX Python runtime under `tools/tuilionnx_python/python.exe`
- optional LLM API key via `SANTISZR_LLM_API_KEY` or `DEEPSEEK_API_KEY`
- CUDA for VoxCPM2 and TuiliONNX GPU paths unless a specific CPU path is
  documented for your local setup

## Current Local Pipeline

- `content`
  - parse Douyin share text or direct URL
  - support local video, audio, or raw text input
  - download media and extract workspace artifacts
- `rewrite`
  - OpenAI-compatible client
  - local heuristic fallback for offline or unstable environments
- `tts`
  - VoxCPM2 GPU helper / client is the default path
  - reference-audio cloning is the default voice mode
  - CosyVoice-related code is retained only as legacy / optional compatibility
- `subtitle`
  - generate SRT from a configured script when available
  - fall back to heuristic subtitle generation when no script is configured
  - optional FFmpeg burn-in
- `avatar`
  - TuiliONNX-oriented asset adapter rooted at `models/tuilionnx`
  - searches `models/tuilionnx/face/` or an explicitly configured root
- `workflow`
  - `content -> rewrite -> tts -> subtitle -> avatar`
  - optional postprocess steps for subtitle burn-in, BGM, and cover generation
  - typed aggregate result for GUI and service callers
- `gui`
  - pages submit background tasks instead of blocking the UI thread
  - task progress, logs, and errors flow back from worker processes
  - settings dialog includes startup diagnostics for local runtime checks

## Publishing Status

Publishing adapters are not bundled in the current build. The GUI can generate
publish-ready materials such as descriptions, hashtags, and cover assets, but
it cannot log in to Douyin, Xiaohongshu, or WeChat Channels and publish
automatically. The typed publish service remains in place and currently returns
structured `publish_not_configured` failures.

## Startup Checks

Open the settings dialog and run `运行环境自检` to verify the local runtime before
you start a pipeline. The check is path-only and does not load large GPU models.

- `FFmpeg / FFprobe`
  - `✅` means the configured path, bundled binary, or `PATH` entry was found
  - `❌` means no usable executable was found
- `VoxCPM2 Model`
  - `✅` means the model directory exists and includes the key files:
    `config.json`, `tokenizer.json`, `tokenizer_config.json`, one of
    `model.safetensors` / `pytorch_model.bin`, and one of
    `audiovae.pth` / `audiovae.safetensors`
  - `❌` means the model directory is missing or incomplete
- `VoxCPM Python`
  - `✅` means `SANTISZR_VOXCPM_PYTHON` or `tools/voxcpm_python/python.exe`
    resolves to a file
  - `❌` means the helper runtime is missing
- `Whisper Model`
  - `✅` means the directory exists
  - `⚠` means the directory is not configured yet or has not been prepared yet
- `TuiliONNX Model / Python`
  - `✅` means the configured directory or bundled runtime exists
  - `⚠` means the path is not configured or the runtime is missing
- `Publisher`
  - `⚠` always means the publishing adapter is not bundled in this build and
    only material generation is available

## Troubleshooting

- If TTS fails immediately, run `运行环境自检` first and verify `VoxCPM2 Model`
  and `VoxCPM Python`.
- If subtitle extraction or media postprocess fails, verify `FFmpeg` and the
  `models/whisper/` directory.
- If digital human rendering fails, verify `models/tuilionnx/` and
  `tools/tuilionnx_python/python.exe`.
- If the publish area shows failures for every platform, that is expected in
  this build unless you wire an external publisher yourself.

## Commands

```bash
uv sync
uv run santiszr-gui
uv run pytest
uv run ruff check .
uv run ruff format .
```

If `uv` is unavailable in the current shell, the project venv also works:

```powershell
D:\SantiSZR\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
D:\SantiSZR\.venv\Scripts\python.exe -m santiszr.app
```
