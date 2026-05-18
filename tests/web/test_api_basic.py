from __future__ import annotations

import json
import queue
import time
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.requests import Request

from santiszr.app import AppContext, ServiceRegistry
from santiszr.config.settings import AppSettings
from santiszr.core.asset_library import MediaLibrary
from santiszr.domain.schemas.audio import RewriteResult, TTSResult
from santiszr.domain.schemas.avatar import AvatarResult
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.content import ContentResult, ExtractedCopy
from santiszr.domain.schemas.publish import PublishBatchResult
from santiszr.domain.schemas.subtitle import SubtitleResult
from santiszr.gui.state.session import PipelineState
from santiszr.web.app import create_app


class FakeContentService:
    def __init__(self) -> None:
        self.transcriber = None

    def extract(self, request):  # noqa: ANN001
        return ContentResult(
            success=True,
            platform="local",
            workspace=request.workspace,
            extracted_copy=ExtractedCopy(raw_text="raw", cleaned_text=request.source.raw_input),
        )


class FakeRewriteService:
    def rewrite(self, request):  # noqa: ANN001
        return RewriteResult(success=True, rewritten_text=request.text, provider="fake")


class FakeTTSService:
    def synthesize(self, request):  # noqa: ANN001
        return TTSResult(success=False, error=ErrorInfo(code="not_used", message="not used"))

    def release_resources(self) -> None:
        return None


class FakeSubtitleService:
    def generate(self, request):  # noqa: ANN001
        return SubtitleResult(success=False, error=ErrorInfo(code="not_used", message="not used"))


class FakeAvatarService:
    def render(self, request):  # noqa: ANN001
        if request.audio_path == "D:/tmp/audio.wav":
            return AvatarResult(success=True, video_path="D:/tmp/avatar.mp4", notes=["avatar ok"])
        return AvatarResult(success=False, error=ErrorInfo(code="not_used", message="not used"))


class FakeWorkflowService:
    def generate_video(self, request, *, progress_callback=None, task_id=None):  # noqa: ANN001
        raise AssertionError("not used")


class FakePublishService:
    def publish_batch(self, request):  # noqa: ANN001
        return PublishBatchResult(success=False, summary="not used")


class FakeTranscriber:
    def __init__(self, transcript: str = "recognized reference transcript") -> None:
        self.transcript = transcript
        self.ready_calls = 0
        self.calls: list[str] = []

    def ensure_ready(self) -> None:
        self.ready_calls += 1

    def transcribe(self, source: str) -> str:
        self.calls.append(source)
        return self.transcript


def _client(tmp_path: Path) -> TestClient:
    settings = AppSettings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    context = AppContext(
        settings=settings,
        services=ServiceRegistry(
            content=FakeContentService(),
            rewrite=FakeRewriteService(),
            tts=FakeTTSService(),
            subtitle=FakeSubtitleService(),
            avatar=FakeAvatarService(),
            workflow=FakeWorkflowService(),
            publish=FakePublishService(),
        ),
        media_library=MediaLibrary(settings),
        state=PipelineState(workspace=str(workspace)),
        task_controller=None,
    )
    return TestClient(create_app(context=context))


def _context_from_client(client: TestClient) -> AppContext:
    return client.app.state.santiszr_context


def _wait_for_task(client: TestClient, task_id: str) -> dict:
    deadline = time.time() + 5
    task = {}
    while time.time() < deadline:
        task_response = client.get(f"/api/tasks/{task_id}")
        assert task_response.status_code == 200
        task = task_response.json()
        if task["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    return task


def test_health_and_state_endpoints(tmp_path: Path) -> None:
    client = _client(tmp_path)

    health = client.get("/api/health")
    state = client.get("/api/state")

    assert health.status_code == 200
    assert health.json()["app"] == "SantiSZR"
    assert "runtime_ok" in health.json()
    assert health.json()["llm"]["configured"] is False
    assert health.json()["llm"]["provider"] == "unconfigured"
    assert health.json()["diagnostics"]
    assert {"name", "status", "message", "detail"}.issubset(health.json()["diagnostics"][0])
    assert state.status_code == 200
    assert state.json()["workspace"] == str(tmp_path / "workspace")


def test_llm_settings_can_be_saved_without_exposing_secret(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.put(
        "/api/settings/llm",
        json={
            "api_key": "sk-test-secret",
            "api_base": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
        },
    )
    health = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm"]["configured"] is True
    assert payload["llm"]["key_preview"] == "sk-t...cret"
    assert "sk-test-secret" not in json.dumps(payload, ensure_ascii=False)
    assert health.json()["llm"]["configured"] is True
    assert (tmp_path / "data" / "config" / "llm.json").exists()


def test_workspace_select_accepts_path_and_workspace_bodies(tmp_path: Path) -> None:
    client = _client(tmp_path)
    selected = tmp_path / "selected"
    legacy_selected = tmp_path / "legacy-selected"

    response = client.post("/api/workspaces/select", json={"path": str(selected)})
    legacy_response = client.post("/api/workspaces/select", json={"workspace": str(legacy_selected)})
    recent = client.get("/api/workspaces/recent")

    assert response.status_code == 200
    assert response.json()["workspace"] == str(selected.resolve())
    assert legacy_response.status_code == 200
    assert legacy_response.json()["workspace"] == str(legacy_selected.resolve())
    assert recent.status_code == 200
    assert recent.json()["recent_workspaces"][0] == str(legacy_selected.resolve())


def test_task_submit_accepts_wrapped_payload_and_direct_payload(tmp_path: Path) -> None:
    client = _client(tmp_path)

    wrapped_response = client.post(
        "/api/tasks/content",
        json={
            "payload": {
                "source": {"source_type": "raw_text", "raw_input": "hello web"},
                "workspace": str(tmp_path / "workspace"),
            }
        },
    )

    assert wrapped_response.status_code == 202
    assert wrapped_response.json()["kind"] == "content"
    assert wrapped_response.json()["task_kind"] == "content"
    wrapped_task = _wait_for_task(client, wrapped_response.json()["task_id"])
    assert wrapped_task["status"] == "succeeded"
    assert wrapped_task["task_kind"] == "content"
    assert wrapped_task["logs"]
    assert wrapped_task["result"]["content"]["extracted_copy"]["cleaned_text"] == "hello web"

    direct_response = client.post(
        "/api/tasks/content",
        json={
            "source": {"source_type": "raw_text", "raw_input": "hello direct"},
            "workspace": str(tmp_path / "workspace"),
        },
    )

    assert direct_response.status_code == 202
    direct_task = _wait_for_task(client, direct_response.json()["task_id"])
    assert direct_task["status"] == "succeeded"
    assert direct_task["logs"]
    assert direct_task["result"]["content"]["extracted_copy"]["cleaned_text"] == "hello direct"

    tasks = client.get("/api/tasks")
    assert tasks.status_code == 200
    assert tasks.json()["tasks"][0]["kind"] == "content"
    assert tasks.json()["tasks"][0]["task_kind"] == "content"


def test_upload_accepts_kind_form_field_for_video(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/assets/upload",
        data={"kind": "video"},
        files={"file": ("reference.mp4", b"fake video bytes", "video/mp4")},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["type"] == "video"
    assert payload["name"] == "reference"
    assert payload["path"]
    assert payload["asset"]["category"] == "reference_video"
    assert Path(payload["path"]).parent == (tmp_path / "workspace" / "reference" / "video").resolve()


def test_upload_reference_audio_is_listed_from_current_workspace(tmp_path: Path) -> None:
    client = _client(tmp_path)
    workspace = tmp_path / "selected-workspace"

    select_response = client.post("/api/workspaces/select", json={"path": str(workspace)})
    assert select_response.status_code == 200

    upload_response = client.post(
        "/api/assets/upload",
        data={"kind": "audio", "workspace": str(workspace)},
        files={"file": ("voice.wav", b"fake audio bytes", "audio/wav")},
    )

    payload = upload_response.json()
    assert upload_response.status_code == 200
    assert payload["type"] == "audio"
    assert payload["name"] == "voice"
    assert Path(payload["path"]).parent == workspace.resolve() / "reference" / "audio"

    assets_response = client.get("/api/assets")
    assert assets_response.status_code == 200
    assets = assets_response.json()["assets"]
    reference_assets = [asset for asset in assets if asset["category"] == "reference_audio"]
    assert len(reference_assets) == 1
    assert reference_assets[0]["name"] == "voice"
    assert reference_assets[0]["path"] == payload["path"]
    assert reference_assets[0]["source"] == "reference/audio"


def test_reference_audio_transcript_uses_memory_cache(tmp_path: Path) -> None:
    client = _client(tmp_path)
    context = _context_from_client(client)
    transcriber = FakeTranscriber("参考音频识别文案")
    context.services.content.transcriber = transcriber
    reference_audio = tmp_path / "workspace" / "reference.wav"
    reference_audio.write_bytes(b"fake audio bytes")

    first = client.post(
        "/api/reference-audio/transcript",
        json={"reference_audio_path": str(reference_audio)},
    )
    second = client.post(
        "/api/reference-audio/transcript",
        json={"reference_audio_path": str(reference_audio)},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["transcript"] == "参考音频识别文案"
    assert first.json()["cache_hit"] is False
    assert second.json()["transcript"] == "参考音频识别文案"
    assert second.json()["cache_hit"] is True
    assert transcriber.ready_calls == 1
    assert transcriber.calls == [str(reference_audio.resolve())]


def test_avatar_task_result_contains_nested_video_path(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/tasks/avatar",
        json={
            "audio_path": "D:/tmp/audio.wav",
            "model_id": "uploaded-avatar",
            "reference_video_path": "D:/tmp/reference.mp4",
            "workspace": str(tmp_path / "workspace"),
        },
    )

    assert response.status_code == 202
    task = _wait_for_task(client, response.json()["task_id"])
    assert task["status"] == "succeeded"
    assert task["result"]["avatar"]["video_path"] == "D:/tmp/avatar.mp4"


def test_sse_event_contains_frontend_alias_fields(tmp_path: Path) -> None:
    client = _client(tmp_path)
    task_manager = client.app.state.santiszr_task_manager
    subscriber = task_manager.subscribe()

    try:
        response = client.post(
            "/api/tasks/content",
            json={
                "source": {"source_type": "raw_text", "raw_input": "hello events"},
                "workspace": str(tmp_path / "workspace"),
            },
        )
        assert response.status_code == 202

        event = subscriber.get(timeout=5)
    finally:
        task_manager.unsubscribe(subscriber)

    assert event["kind"] == "content"
    assert event["task_kind"] == "content"
    assert event["status"] in {"queued", "running", "succeeded"}
    assert event["event"] in {"created", "progress", "succeeded"}


def test_events_endpoint_streams_frontend_alias_fields(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path)
    task_manager = client.app.state.santiszr_task_manager
    subscriber: queue.Queue[dict] = queue.Queue()
    subscriber.put(
        {
            "task_id": "task-1",
            "kind": "content",
            "task_kind": "content",
            "event": "created",
            "status": "queued",
            "stage": "",
            "progress": 0.0,
            "message": "Task queued.",
            "payload": {},
            "error": None,
            "created_at": "2026-05-08T00:00:00Z",
        }
    )

    monkeypatch.setattr(task_manager, "subscribe", lambda: subscriber)
    monkeypatch.setattr(task_manager, "unsubscribe", lambda _: None)

    async def read_first_event() -> dict:
        route = next(route for route in client.app.routes if getattr(route, "path", "") == "/api/events")
        messages = iter(
            [
                {"type": "http.request", "body": b"", "more_body": False},
                {"type": "http.disconnect"},
            ]
        )

        async def receive():
            return next(messages)

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/events",
                "headers": [],
                "app": client.app,
            },
            receive,
        )
        response = await route.endpoint(request)
        chunk = await response.body_iterator.__anext__()
        await response.body_iterator.aclose()
        line = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        assert line.startswith("data: ")
        return json.loads(line.removeprefix("data: ").strip())

    import asyncio

    event = asyncio.run(read_first_event())
    assert event is not None
    assert event["kind"] == "content"
    assert event["task_kind"] == "content"
    assert event["event"] == "created"


def test_rejects_file_outside_workspace_and_data_roots(tmp_path: Path) -> None:
    client = _client(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    inside = tmp_path / "workspace" / "inside.txt"
    inside.write_text("ok", encoding="utf-8")

    outside_response = client.get("/api/files", params={"path": str(outside)})
    inside_response = client.get("/api/files", params={"path": str(inside)})

    assert outside_response.status_code == 403
    assert inside_response.status_code == 200
    assert inside_response.text == "ok"


def test_updates_text_file_inside_workspace_only(tmp_path: Path) -> None:
    client = _client(tmp_path)
    subtitle = tmp_path / "workspace" / "subtitle.srt"
    subtitle.write_text("old subtitle", encoding="utf-8")
    video = tmp_path / "workspace" / "video.mp4"
    video.write_bytes(b"not text")
    outside = tmp_path / "outside.srt"
    outside.write_text("secret", encoding="utf-8")

    response = client.put("/api/files", json={"path": str(subtitle), "content": "edited subtitle"})
    non_text_response = client.put("/api/files", json={"path": str(video), "content": "bad"})
    outside_response = client.put("/api/files", json={"path": str(outside), "content": "bad"})

    assert response.status_code == 200
    assert response.json()["path"] == str(subtitle.resolve())
    assert subtitle.read_text(encoding="utf-8") == "edited subtitle"
    assert non_text_response.status_code == 400
    assert outside_response.status_code == 403


def test_task_kind_validation(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post("/api/tasks/unknown", json={"payload": {}})

    assert response.status_code == 422
