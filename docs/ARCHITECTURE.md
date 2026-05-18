# SantiSZR Architecture

## Goals

- separate GUI concerns from business logic
- keep contracts explicit before implementing integrations
- centralize runtime paths, logging, and task state
- keep SantiSZR runnable without any default dependency on external legacy
  projects
- standardize model assets under `models/`
- run GUI-triggered work off the UI thread

## Layers

### GUI

`src/santiszr/gui/`

- owns windows, page composition, user interaction, and status display
- submits typed task requests to a background task controller
- does not call heavy workflow stages inline

### GUI State

`src/santiszr/gui/state/`

- stores pipeline session state, active task metadata, progress, logs, and last
  results
- hosts the Qt-side task controller
- uses `QThreadPool + QRunnable` to supervise worker subprocesses

### Core

`src/santiszr/core/`

- runtime path resolution
- runtime directory provisioning, including `models/` and model subdirectories
- logging bootstrap
- shared exception and task primitives

### Domain

`src/santiszr/domain/`

- Pydantic schemas for request and result models
- business services for content, rewrite, TTS, subtitle, avatar, publish, and
  workflow
- workflow service emits stage progress through a callback, not through
  GUI-specific hooks

### Infra

`src/santiszr/infra/`

- adapters for downloader, LLM, TTS, FFmpeg, avatar engines, and publishers
- FFmpeg resolves explicit config, optional `tools/ffmpeg/bin/`, and `PATH`
- subtitle generation uses explicit script configuration or repository-local
  scripts when present, then falls back to heuristics
- avatar resolution stays inside `models/tuilionnx` or explicitly configured
  roots
- publishing adapters return structured "not configured" failures until a
  SantiSZR-native publisher is bundled

### Workers

`src/santiszr/workers/`

- subprocess entrypoint for heavy task execution
- receives one task request via stdin
- emits progress and results over stdout JSON lines

## Worker Protocol

- request envelope:
  - `task_id`
  - `task_kind`
  - `payload`
- event envelope:
  - `event`
  - `task_id`
  - `task_kind`
  - `stage`
  - `progress`
  - `message`
  - optional `payload`
  - optional `error`
- supported task kinds:
  - `content`
  - `rewrite`
  - `tts`
  - `subtitle`
  - `avatar`
  - `full-workflow`

## MVP Flow

1. `content` resolves source input and extracts working artifacts.
2. `rewrite` transforms source copy into publishable text.
3. `tts` generates narration audio.
4. `subtitle` creates SRT artifacts and optional burn-in outputs.
5. `avatar` produces the digital human video.
6. `workflow` aggregates outputs and returns a typed result to the GUI.
