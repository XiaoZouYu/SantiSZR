# SantiSZR API Contracts

This document describes the typed request and result contracts that back the
implemented MVP pipeline and the worker protocol used by the GUI background
layer.

## Shared Models

- `ErrorInfo`
- `TaskStatus`
- `TaskContext`
- `WorkerTaskRequest`
- `WorkerEvent`

## Module Models

- `VideoSource`, `ContentRequest`, `ContentResult`
- `RewriteRequest`, `RewriteResult`
- `TTSRequest`, `TTSResult`
- `SubtitleRequest`, `SubtitleResult`
- `AvatarRequest`, `AvatarResult`
- `PublishRequest`, `PublishResult`
- `GenerateVideoWorkflowRequest`, `GenerateVideoWorkflowResult`

## Worker Task Kinds

- `content`
- `rewrite`
- `tts`
- `subtitle`
- `avatar`
- `full-workflow`

## Worker JSON Line Protocol

- Workers emit one JSON object per stdout line.
- Each event includes:
  - `event`
  - `task_id`
  - `task_kind`
  - `stage`
  - `progress`
  - `message`
- Optional event fields:
  - `payload`
  - `error`
- Supported event values:
  - `started`
  - `progress`
  - `log`
  - `succeeded`
  - `failed`
  - `cancelled`

## Important Current Fields

- `ContentRequest`
  - `download_video`
  - `extract_audio`
- `ContentResult`
  - `source_url`
  - `resolved_url`
  - `transcript_path`
  - `metadata`
  - `notes`
- `RewriteRequest`
  - `model`
  - `workspace`
- `RewriteResult`
  - `provider`
  - `prompt_used`
- `TTSRequest`
  - `workspace`
  - `output_name`
- `SubtitleRequest`
  - `workspace`
  - `output_name`
- `AvatarRequest`
  - `workspace`
  - `subtitle_path`
  - `background_video_path`
  - `overlay_text`
- `GenerateVideoWorkflowRequest`
  - `rewrite_model`
  - `voice_speed`
  - `avatar_engine`
  - `subtitle_burn_in`

## Rules

- every cross-module operation uses a typed request model
- every result includes `success` plus structured error data on failure
- workflow responses aggregate module outputs instead of returning tuples
- default behavior remains locally runnable even when remote dependencies are missing
- model paths resolve from the standardized `D:\SantiSZR\models` layout by default
