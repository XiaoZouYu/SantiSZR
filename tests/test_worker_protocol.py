from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from santiszr.workers.protocol import (
    WorkerEvent,
    WorkerEventType,
    WorkerTaskKind,
    WorkerTaskRequest,
    encode_json_line,
    parse_task_request,
    parse_worker_event,
)


def test_worker_task_request_roundtrip() -> None:
    request = WorkerTaskRequest(
        task_id="task-1",
        task_kind=WorkerTaskKind.tts,
        payload={"text": "hello", "voice": "neutral"},
    )

    encoded = encode_json_line(request)
    decoded = parse_task_request(encoded)

    assert decoded.task_id == "task-1"
    assert decoded.task_kind is WorkerTaskKind.tts
    assert decoded.payload["voice"] == "neutral"


def test_worker_event_roundtrip() -> None:
    event = WorkerEvent(
        event=WorkerEventType.progress,
        task_id="task-2",
        task_kind=WorkerTaskKind.full_workflow,
        stage="subtitle",
        progress=0.75,
        message="Subtitle stage completed.",
        payload={"subtitle_path": "D:/tmp/demo.srt"},
    )

    encoded = encode_json_line(event)
    decoded = parse_worker_event(encoded)

    assert decoded.event is WorkerEventType.progress
    assert decoded.stage == "subtitle"
    assert decoded.progress == 0.75


def test_worker_runner_accepts_utf8_payloads(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    src_path = str(project_root / "src")
    env["PYTHONPATH"] = src_path if not env.get("PYTHONPATH") else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    request = WorkerTaskRequest(
        task_id="task-utf8",
        task_kind=WorkerTaskKind.content,
        payload={
            "source": {
                "source_type": "raw_text",
                "raw_input": "测试文案，含中文",
            },
            "workspace": str(tmp_path / "workspace"),
            "download_video": True,
            "extract_audio": True,
        },
    )

    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "santiszr.workers.runner"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        cwd=str(project_root),
        env=env,
    )
    stdout, stderr = process.communicate(encode_json_line(request), timeout=60)

    assert process.returncode == 0, stderr or stdout
    events = [parse_worker_event(line) for line in stdout.splitlines() if line.strip()]

    assert events[0].event is WorkerEventType.started
    assert events[-1].event is WorkerEventType.succeeded
    assert events[-1].payload["content"]["extracted_copy"]["cleaned_text"] == "测试文案，含中文"
