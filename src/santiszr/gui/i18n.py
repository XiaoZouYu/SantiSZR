from __future__ import annotations

from santiszr.domain.schemas.audio import RewriteMode
from santiszr.domain.schemas.common import TaskStatus


EMPTY_TEXT = "—"

_STEP_LABELS = {
    "content": "文案提取",
    "rewrite": "文案改写",
    "rewrite-text": "文案改写",
    "tts": "语音合成",
    "subtitle": "字幕生成",
    "avatar": "数字人渲染",
    "postprocess": "后处理",
    "publish": "发布",
}

_STEP_NUMBERS = {
    "content": 1,
    "rewrite": 2,
    "rewrite-text": 2,
    "tts": 3,
    "subtitle": 4,
    "avatar": 5,
    "postprocess": 6,
    "publish": 7,
}

_TASK_KIND_LABELS = {
    "full-workflow": "1-7. 完整流程",
    "task": "任务",
}

_STAGE_LABELS = {
    "workflow": "1-7. 完整流程",
    "worker": "任务进程",
    "stderr": "错误输出",
}

_STATUS_LABELS = {
    "pending": "待处理",
    "running": "运行中",
    "succeeded": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}

_REWRITE_MODE_LABELS = {
    "correct": "纠错改写",
    "imitate": "风格仿写",
    "custom": "自定义改写",
}

_VOICE_LABELS = {
    "neutral": "中性",
    "bright": "明亮",
    "calm": "沉稳",
    "reference-clone": "参考音频克隆",
}

_MAIN_FLOW_LABELS = (
    "1. 输入原料",
    "2. 生成音频/字幕",
    "3. 数字人加工",
    "4. 发布与运维",
)

_STUDIO_FLOW_LABELS = (
    "1. 链接输入",
    "2. 提取文案",
    "3. 仿写",
    "4. 生成音频",
    "5. 生成视频",
    "6. 生成字幕",
    "7. 字幕/BGM/封面",
    "8. 发布",
)


def display_text(value: str | None, *, default: str = EMPTY_TEXT) -> str:
    text = (value or "").strip()
    return text or default


def _step_label(key: str) -> str | None:
    label = _STEP_LABELS.get(key)
    number = _STEP_NUMBERS.get(key)
    if label is None or number is None:
        return None
    return f"{number}. {label}"


def task_kind_text(task_kind: str | None) -> str:
    key = (task_kind or "").strip().lower()
    if numbered := _step_label(key):
        return numbered
    return _TASK_KIND_LABELS.get(key, display_text(task_kind))


def stage_text(stage: str | None) -> str:
    key = (stage or "").strip().lower()
    if numbered := _step_label(key):
        return numbered
    return _STAGE_LABELS.get(key, display_text(stage))


def status_text(status: TaskStatus | str | None) -> str:
    key = status.value if isinstance(status, TaskStatus) else (status or "")
    key = key.strip().lower()
    return _STATUS_LABELS.get(key, display_text(key))


def rewrite_mode_text(mode: RewriteMode | str) -> str:
    key = mode.value if isinstance(mode, RewriteMode) else str(mode)
    key = key.strip().lower()
    return _REWRITE_MODE_LABELS.get(key, display_text(key))


def voice_text(voice: str | None) -> str:
    key = (voice or "").strip().lower()
    return _VOICE_LABELS.get(key, display_text(voice))


def main_flow_labels() -> tuple[str, ...]:
    return _MAIN_FLOW_LABELS


def studio_flow_labels() -> tuple[str, ...]:
    return _STUDIO_FLOW_LABELS
