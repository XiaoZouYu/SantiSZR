from __future__ import annotations

import asyncio
import queue
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from santiszr.app import AppContext
from santiszr.core.asset_library import AssetCategory, ManagedAsset
from santiszr.core.diagnostics import run_startup_diagnostics
from santiszr.core.gpu_memory import get_cuda_memory_snapshot
from santiszr.core.llm_config import save_persisted_llm_settings
from santiszr.core.paths import ensure_module_dir, resolve_runtime_paths, sanitize_filename
from santiszr.domain.services.postprocess_service import PostProcessService
from santiszr.domain.services.rewrite_service import RewriteService
from santiszr.infra.llm.client import LLMClient
from santiszr.infra.subtitle.corrector import SubtitleCorrector
from santiszr.web.files import resolve_safe_file
from santiszr.web.schemas import (
    AssetDeleteResponse,
    AssetInfo,
    AssetListResponse,
    DiagnosticInfo,
    FileWriteRequest,
    FileWriteResponse,
    HealthResponse,
    LLMSettingsRequest,
    LLMSettingsResponse,
    LLMStatusInfo,
    LLMTestRequest,
    LLMTestResponse,
    PublishMaterialsPrepareRequest,
    PublishMaterialsPrepareResponse,
    ReferenceTranscriptRequest,
    ReferenceTranscriptResponse,
    StateResponse,
    TaskListResponse,
    TaskSubmitRequest,
    TaskSubmitResponse,
    UploadResponse,
    WebTaskKind,
    WorkspaceResponse,
    WorkspaceSelectRequest,
)
from santiszr.web.task_manager import TaskConflictError, WebTaskManager
from santiszr.web.workspaces import (
    WorkspaceAsset,
    get_current_workspace,
    get_recent_workspaces,
    scan_workspace_assets,
    select_workspace as select_web_workspace,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
TEXT_SUFFIXES = {".txt", ".srt", ".ass", ".md", ".json"}


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        context = _context(request)
        checks = run_startup_diagnostics(context.settings)
        runtime_ok = all(check.status != "error" for check in checks)
        return HealthResponse(
            app=context.settings.app_name,
            version="0.1.0",
            workspace=context.state.workspace,
            runtime_ok=runtime_ok,
            diagnostics=_diagnostics_for_health(checks),
            llm=_llm_status_for_health(context),
        )

    @router.put("/settings/llm", response_model=LLMSettingsResponse)
    def update_llm_settings(body: LLMSettingsRequest, request: Request) -> LLMSettingsResponse:
        context = _context(request)
        api_base = body.api_base.strip().rstrip("/") or "https://api.deepseek.com/v1"
        model = body.model.strip() or "deepseek-chat"

        if body.api_key is not None:
            api_key = body.api_key.strip()
            context.settings.llm.api_key = api_key or None
        context.settings.llm.api_base = api_base
        context.settings.llm.model = model
        save_persisted_llm_settings(context.settings)
        _rebuild_llm_services(context)
        return LLMSettingsResponse(llm=_llm_status_for_health(context))

    @router.post("/settings/llm/test", response_model=LLMTestResponse)
    def test_llm_settings(body: LLMTestRequest, request: Request) -> LLMTestResponse:
        context = _context(request)
        api_key = (body.api_key or context.settings.llm.api_key or "").strip()
        api_base = (body.api_base or context.settings.llm.api_base).strip().rstrip("/")
        model = (body.model or context.settings.llm.model).strip()
        client = LLMClient(api_key=api_key, api_base=api_base, model=model, timeout_sec=min(context.settings.llm.timeout_sec, 20.0))
        if not client.is_configured():
            return LLMTestResponse(ok=False, provider="unconfigured", model=model, message="请先填写 API Key。")
        try:
            response = client.generate("只回复：连接成功", system_prompt="你是接口连通性测试助手。", temperature=0.0)
        except Exception as exc:
            return LLMTestResponse(ok=False, provider=client.provider_name(), model=model, message=f"连接失败：{exc}")
        return LLMTestResponse(
            ok=True,
            provider=response.provider,
            model=response.model,
            message="连接成功。",
        )

    @router.get("/state", response_model=StateResponse)
    def state(request: Request) -> StateResponse:
        context = _context(request)
        task_manager = _task_manager(request)
        return StateResponse(
            workspace=context.state.workspace,
            current_task=task_manager.current_task(),
            recent_tasks=task_manager.recent_tasks(limit=20),
            artifacts=_artifacts(context),
        )

    @router.post("/workspaces/select", response_model=WorkspaceResponse)
    def select_workspace(body: WorkspaceSelectRequest, request: Request) -> WorkspaceResponse:
        context = _context(request)
        try:
            summary = select_web_workspace(context.settings, body.resolved_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        context.state.workspace = summary.path
        recent = [item.path for item in get_recent_workspaces(context.settings)]
        return WorkspaceResponse(workspace=summary.path, recent_workspaces=recent)

    @router.get("/workspaces/recent", response_model=WorkspaceResponse)
    def recent_workspaces(request: Request) -> WorkspaceResponse:
        context = _context(request)
        current = get_current_workspace(context.settings)
        recent = [item.path for item in get_recent_workspaces(context.settings)]
        return WorkspaceResponse(workspace=current.path if current else "", recent_workspaces=recent)

    @router.get("/assets", response_model=AssetListResponse)
    def assets(request: Request) -> AssetListResponse:
        context = _context(request)
        workspace = context.state.workspace
        return AssetListResponse(workspace=workspace, assets=_list_assets(context))

    @router.post("/assets/upload", response_model=UploadResponse)
    async def upload_asset(
        request: Request,
        file: Annotated[UploadFile, File()],
        asset_type: Annotated[str | None, Form()] = None,
        kind: Annotated[str | None, Form()] = None,
        workspace: Annotated[str | None, Form()] = None,
    ) -> UploadResponse:
        context = _context(request)
        filename = file.filename or "upload.bin"
        suffix = Path(filename).suffix.lower()
        normalized_type = _normalize_upload_type(asset_type, kind)

        if normalized_type in {"pip_image", "pip_video"}:
            pip_kind = "image" if normalized_type == "pip_image" else "video"
            allowed = IMAGE_SUFFIXES if pip_kind == "image" else VIDEO_SUFFIXES
            if suffix not in allowed:
                raise HTTPException(status_code=400, detail=f"Unsupported {normalized_type} file type: {suffix}")
            workspace_path = _upload_workspace(context, workspace)
            upload_dir = workspace_path / "pip" / pip_kind
            upload_dir.mkdir(parents=True, exist_ok=True)
            destination = _unique_destination(upload_dir, filename)
            await _write_upload(file, destination)
            return _upload_response(_path_to_asset_info(destination, normalized_type, f"pip/{pip_kind}"), normalized_type)

        if normalized_type in {"audio", "video"}:
            workspace_path = _upload_workspace(context, workspace)
            upload_dir = workspace_path / "reference" / normalized_type
            upload_dir.mkdir(parents=True, exist_ok=True)
            destination = _unique_destination(upload_dir, filename)
            await _write_upload(file, destination)
            category = "reference_audio" if normalized_type == "audio" else "reference_video"
            return _upload_response(_path_to_asset_info(destination, category, f"reference/{normalized_type}"), normalized_type)

        if normalized_type not in {"image", "text"}:
            raise HTTPException(status_code=400, detail="asset_type must be audio, video, image, or text.")
        allowed = IMAGE_SUFFIXES if normalized_type == "image" else TEXT_SUFFIXES
        if suffix not in allowed:
            raise HTTPException(status_code=400, detail=f"Unsupported {normalized_type} file type: {suffix}")

        workspace_path = _upload_workspace(context, workspace)
        upload_dir = workspace_path / "uploads" / normalized_type
        upload_dir.mkdir(parents=True, exist_ok=True)
        destination = _unique_destination(upload_dir, filename)
        await _write_upload(file, destination)
        return _upload_response(_path_to_asset_info(destination, normalized_type, "workspace"), normalized_type)

    @router.delete("/assets", response_model=AssetDeleteResponse)
    def delete_asset(path: str, request: Request) -> AssetDeleteResponse:
        context = _context(request)
        try:
            file_path = resolve_safe_file(path, context.state.workspace, context.settings)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        file_path.unlink(missing_ok=True)
        sibling_text = file_path.with_suffix(".txt")
        if sibling_text != file_path:
            sibling_text.unlink(missing_ok=True)
        return AssetDeleteResponse(path=str(file_path))

    @router.post("/reference-audio/transcript", response_model=ReferenceTranscriptResponse)
    async def reference_audio_transcript(
        body: ReferenceTranscriptRequest,
        request: Request,
    ) -> ReferenceTranscriptResponse:
        context = _context(request)
        try:
            audio_path = resolve_safe_file(body.reference_audio_path, body.workspace or context.state.workspace, context.settings)
            transcript, cache_hit = await asyncio.to_thread(_resolve_reference_audio_transcript, context, audio_path)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return ReferenceTranscriptResponse(
            reference_audio_path=str(audio_path),
            transcript=transcript,
            cache_hit=cache_hit,
        )

    @router.post("/publish/prepare", response_model=PublishMaterialsPrepareResponse)
    async def prepare_publish_materials(
        body: PublishMaterialsPrepareRequest,
        request: Request,
    ) -> PublishMaterialsPrepareResponse:
        context = _context(request)
        try:
            return await asyncio.to_thread(_prepare_publish_materials, context, body)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/tasks/{kind}", response_model=TaskSubmitResponse, status_code=202)
    def submit_task(kind: WebTaskKind, body: TaskSubmitRequest, request: Request) -> TaskSubmitResponse:
        try:
            task = _task_manager(request).submit(kind, body.payload)
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return TaskSubmitResponse(task_id=task.task_id, kind=kind, status=task.status)

    @router.get("/tasks", response_model=TaskListResponse)
    def tasks(request: Request) -> TaskListResponse:
        return TaskListResponse(tasks=_task_manager(request).list_tasks())

    @router.get("/tasks/{task_id}")
    def task(task_id: str, request: Request):
        record = _task_manager(request).get_task(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        return record

    @router.post("/tasks/{task_id}/cancel")
    def cancel_task(task_id: str, request: Request):
        record = _task_manager(request).cancel(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        return record

    @router.get("/events")
    async def events(request: Request):
        task_manager = _task_manager(request)
        subscriber = task_manager.subscribe()

        async def stream():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.to_thread(subscriber.get, True, 15.0)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                task_manager.unsubscribe(subscriber)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.get("/files")
    def files(path: str, request: Request):
        context = _context(request)
        try:
            file_path = resolve_safe_file(path, context.state.workspace, context.settings)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(str(file_path), filename=file_path.name)

    @router.put("/files")
    def write_file(payload: FileWriteRequest, request: Request):
        context = _context(request)
        try:
            file_path = resolve_safe_file(payload.path, context.state.workspace, context.settings)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        if file_path.suffix.lower() not in TEXT_SUFFIXES:
            raise HTTPException(status_code=400, detail="Only text files can be updated.")

        file_path.write_text(payload.content, encoding="utf-8")
        stat = file_path.stat()
        return FileWriteResponse(
            path=str(file_path),
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        )

    return router


def _context(request: Request) -> AppContext:
    return request.app.state.santiszr_context


def _task_manager(request: Request) -> WebTaskManager:
    return request.app.state.santiszr_task_manager


def _diagnostics_for_health(checks: object) -> list[DiagnosticInfo]:
    diagnostics = [
        DiagnosticInfo(
            name=check.name,
            status=check.status,
            message=check.message,
            detail=check.detail,
        )
        for check in checks
    ]

    snapshot = get_cuda_memory_snapshot()
    if snapshot is None:
        diagnostics.append(
            DiagnosticInfo(
                name="GPU",
                status="warning",
                message="GPU memory status unavailable.",
                detail="nvidia-smi was not available or did not return memory information.",
            )
        )
    else:
        diagnostics.append(
            DiagnosticInfo(
                name="GPU",
                status="ok",
                message="GPU memory status available.",
                detail=f"{snapshot.free_mb}/{snapshot.total_mb} MB free ({snapshot.source})",
            )
        )
    return diagnostics


def _llm_status_for_health(context: AppContext) -> LLMStatusInfo:
    settings = context.settings.llm
    configured = bool(settings.api_key)
    api_base = settings.api_base.rstrip("/")
    provider = "deepseek" if "deepseek" in api_base.lower() else api_base or "custom"
    return LLMStatusInfo(
        configured=configured,
        provider=provider if configured else "unconfigured",
        model=settings.model,
        api_base=api_base,
        key_preview=_preview_secret(settings.api_key),
        message="大模型已配置，文案改写和字幕纠错可调用 AI。"
        if configured
        else "未配置大模型 API Key，文案改写不可用，字幕大模型纠错会跳过。",
    )


def _preview_secret(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip()
    if len(cleaned) <= 8:
        return "****"
    return f"{cleaned[:4]}...{cleaned[-4:]}"


def _rebuild_llm_services(context: AppContext) -> None:
    client = LLMClient(
        api_key=context.settings.llm.api_key,
        api_base=context.settings.llm.api_base,
        model=context.settings.llm.model,
        timeout_sec=context.settings.llm.timeout_sec,
    )
    context.services.rewrite = RewriteService(llm_client=client)
    context.services.subtitle.corrector = SubtitleCorrector(llm_client=client)
    context.services.workflow.rewrite_service = context.services.rewrite
    context.services.workflow.subtitle_service = context.services.subtitle


def _artifacts(context: AppContext) -> dict[str, str]:
    state = context.state
    items = {
        "source_video": state.source_video_path,
        "audio": state.audio_path,
        "subtitle": state.subtitle_path,
        "avatar_video": state.avatar_video_path,
        "final_video": state.final_video_path,
    }
    return {key: value for key, value in items.items() if value}


def _list_assets(context: AppContext) -> list[AssetInfo]:
    assets: list[AssetInfo] = []
    workspace = context.state.workspace.strip()
    if workspace:
        try:
            snapshot = scan_workspace_assets(workspace, context.settings)
        except Exception:
            snapshot = None
        if snapshot is not None:
            for item in (
                *snapshot.reference_audio,
                *snapshot.reference_videos,
                *snapshot.generated_audio,
                *snapshot.subtitles,
                *snapshot.avatar_videos,
                *snapshot.styled_videos,
                *snapshot.pip_assets,
                *snapshot.covers,
                *snapshot.drafts,
            ):
                assets.append(_workspace_asset_to_info(item))

    return sorted(assets, key=lambda item: item.modified_at or "", reverse=True)


def _normalize_upload_type(asset_type: str | None, kind: str | None) -> str:
    raw_value = (asset_type or kind or "audio").strip().lower()
    aliases = {
        "reference_video": "video",
        "reference-video": "video",
        "video": "video",
        "pip_image": "pip_image",
        "pip-image": "pip_image",
        "pip_video": "pip_video",
        "pip-video": "pip_video",
        "audio": "audio",
        "image": "image",
        "text": "text",
    }
    return aliases.get(raw_value, raw_value)


def _upload_workspace(context: AppContext, workspace: str | None) -> Path:
    raw_workspace = (workspace or context.state.workspace or "").strip()
    if raw_workspace:
        return Path(raw_workspace).expanduser().resolve()
    return resolve_runtime_paths(context.settings).data / "uploads"


def _upload_response(asset: AssetInfo, asset_type: str) -> UploadResponse:
    return UploadResponse(
        asset=asset,
        asset_id=asset.asset_id,
        category=asset.category,
        path=asset.path,
        name=asset.name,
        type=asset_type,
        size_bytes=asset.size_bytes,
    )


def _resolve_reference_audio_transcript(context: AppContext, audio_path: Path) -> tuple[str, bool]:
    cache_key = context.state.reference_transcript_key(str(audio_path))
    cached = context.state.reference_transcript_cache.get(cache_key, "").strip()
    if cached:
        return cached, True

    transcriber = getattr(context.services.content, "transcriber", None)
    if transcriber is None:
        from santiszr.infra.transcription import WhisperTranscriber

        transcriber = WhisperTranscriber()
        try:
            context.services.content.transcriber = transcriber
        except Exception:
            pass

    ensure_ready = getattr(transcriber, "ensure_ready", None)
    if callable(ensure_ready):
        ensure_ready()

    transcript = str(transcriber.transcribe(str(audio_path)) or "").strip()
    if not transcript:
        raise ValueError("参考音频文字识别为空，无法启用极致克隆。")

    context.state.reference_transcript_cache[cache_key] = transcript
    return transcript, False


def _prepare_publish_materials(
    context: AppContext,
    request: PublishMaterialsPrepareRequest,
) -> PublishMaterialsPrepareResponse:
    workspace = Path(request.workspace).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise FileNotFoundError(f"Workspace does not exist: {workspace}")

    video_path = resolve_safe_file(request.video_path, workspace, context.settings)
    publish_dir = ensure_module_dir(workspace, "publish")
    notes: list[str] = []

    source_text = request.source_text.strip()
    title = request.title.strip()
    description = request.description.strip()
    tags = _normalize_publish_tags(request.tags)
    if request.generate_with_ai:
        ai_title, ai_description, ai_tags, ai_note = _generate_publish_copy_with_ai(
            context,
            source_text,
            title,
            description,
            tags,
        )
        if ai_note:
            notes.append(ai_note)
        title = ai_title or title
        description = ai_description or description
        tags = ai_tags or tags

    if not title:
        title = _fallback_publish_title(source_text, video_path.stem)
        notes.append("使用本地规则生成标题。")
    if not description:
        description = _fallback_publish_description(source_text, title)
        notes.append("使用本地规则生成描述。")
    if not tags:
        tags = _fallback_publish_tags(source_text)
        notes.append("使用本地规则生成标签。")

    two_line_input = _render_publish_text(title, tags)
    publish_text_path = publish_dir / "output.txt"
    publish_text_path.write_text(two_line_input, encoding="utf-8")

    cover_title = request.cover_title.strip()
    cover_highlight = request.cover_highlight.strip()
    if request.generate_with_ai:
        ai_cover_title, ai_cover_highlight, ai_note = _generate_cover_copy_with_ai(
            context,
            source_text or description or title,
            title,
            tags,
        )
        if ai_note:
            notes.append(ai_note)
        cover_title = ai_cover_title or cover_title
        cover_highlight = ai_cover_highlight or cover_highlight

    cover_title = cover_title or title[:12]
    cover_highlight = cover_highlight or (tags[0] if tags else title[:4])[:4]
    cover_path: Path | None = None
    if request.generate_cover:
        cover_path = publish_dir / "output_cover_with_text.png"
        PostProcessService().ffmpeg.render_cover_image(
            video_path=video_path,
            output_path=cover_path,
            timestamp_sec=request.cover_timestamp_sec,
            title=cover_title,
            highlight_text=cover_highlight,
            position="top",
        )
        notes.append("已从当前视频抽帧并叠加标题/高亮词生成封面。")

    context.state.rewritten_title = title
    context.state.tags = [f"#{tag}" for tag in tags]

    return PublishMaterialsPrepareResponse(
        title=title,
        description=description,
        tags=tags,
        two_line_input=two_line_input,
        publish_text_path=str(publish_text_path),
        cover_path=str(cover_path) if cover_path else None,
        cover_title=cover_title,
        cover_highlight=cover_highlight,
        notes=notes,
    )


def _generate_publish_copy_with_ai(
    context: AppContext,
    source_text: str,
    fallback_title: str,
    fallback_description: str,
    fallback_tags: list[str],
) -> tuple[str, str, list[str], str]:
    client = getattr(context.services.rewrite, "llm_client", None) or LLMClient(
        api_key=context.settings.llm.api_key,
        api_base=context.settings.llm.api_base,
        model=context.settings.llm.model,
        timeout_sec=context.settings.llm.timeout_sec,
    )
    if not client.is_configured():
        return "", "", [], "未配置大模型，跳过 AI 发布文案生成。"
    prompt = (
        "请为下面短视频内容生成发布资料。\n"
        "要求：只返回 JSON，格式为 {\"title\":\"标题\",\"description\":\"描述\",\"tags\":[\"标签1\",\"标签2\"]}。\n"
        "标题要贴合视频内容，不超过 28 个中文字符；描述 60 到 120 个中文字符，像真人发布文案；标签 3 到 6 个，不要带 #。\n"
        "不要使用文件名、路径或模型输出名做标题；description 必须重新组织表达，不要原样照抄视频内容或音频文案。\n\n"
        f"当前标题：{fallback_title}\n"
        f"当前描述：{fallback_description[:500]}\n"
        f"当前标签：{' '.join(fallback_tags)}\n"
        f"视频内容：{source_text[:2000]}"
    )
    try:
        response = client.generate(
            prompt,
            system_prompt="你是短视频发布文案助手，只输出可解析 JSON。",
            model=context.settings.llm.model,
            temperature=0.65,
        )
        record = _parse_json_object(response.text)
        title = str(record.get("title") or "").strip()
        description = str(record.get("description") or "").strip()
        tags = _normalize_publish_tags(record.get("tags"))
        if description and _same_compact_text(description, source_text):
            description = ""
        if title or description or tags:
            return title[:40], description[:220], tags, "已使用 AI 生成发布标题、描述和标签。"
    except Exception as exc:
        return "", "", [], f"AI 发布文案生成失败，已回退本地规则：{exc}"
    return "", "", [], "AI 发布文案返回为空，已回退本地规则。"


def _generate_cover_copy_with_ai(
    context: AppContext,
    source_text: str,
    title: str,
    tags: list[str],
) -> tuple[str, str, str]:
    client = getattr(context.services.rewrite, "llm_client", None) or LLMClient(
        api_key=context.settings.llm.api_key,
        api_base=context.settings.llm.api_base,
        model=context.settings.llm.model,
        timeout_sec=context.settings.llm.timeout_sec,
    )
    if not client.is_configured():
        return "", "", "未配置大模型，跳过 AI 封面文案生成。"
    prompt = (
        "请为短视频封面生成两个字段：headline 和 highlight。\n"
        "要求：只返回 JSON，格式为 {\"headline\":\"封面标题\",\"highlight\":\"高亮词\"}。\n"
        "headline 不超过 12 个中文字符，highlight 不超过 4 个中文字符。\n\n"
        f"发布标题：{title}\n"
        f"标签：{' '.join(tags)}\n"
        f"视频内容：{source_text[:1600]}"
    )
    try:
        response = client.generate(
            prompt,
            system_prompt="你是短视频封面文案助手，只输出可解析 JSON。",
            model=context.settings.llm.model,
            temperature=0.75,
        )
        record = _parse_json_object(response.text)
        headline = str(record.get("headline") or "").strip()[:12]
        highlight = str(record.get("highlight") or "").strip()[:4]
        if headline or highlight:
            return headline, highlight, "已使用 AI 生成封面标题和高亮词。"
    except Exception as exc:
        return "", "", f"AI 封面文案生成失败，已回退本地规则：{exc}"
    return "", "", "AI 封面文案返回为空，已回退本地规则。"


def _parse_json_object(text: str) -> dict[str, object]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        data = json.loads(match.group(0)) if match else {}
    return data if isinstance(data, dict) else {}


def _same_compact_text(left: str, right: str) -> bool:
    left_compact = re.sub(r"\s+", "", left).strip()
    right_compact = re.sub(r"\s+", "", right).strip()
    return bool(left_compact and right_compact and left_compact == right_compact)


def _normalize_publish_tags(value: object) -> list[str]:
    if isinstance(value, list):
        raw_items = [str(item) for item in value]
    elif isinstance(value, str):
        raw_items = re.split(r"[\s,，、#]+", value)
    else:
        raw_items = []
    tags: list[str] = []
    for item in raw_items:
        tag = str(item).strip().strip("#").strip()
        if not tag or tag in tags:
            continue
        tags.append(tag)
        if len(tags) >= 10:
            break
    return tags


def _fallback_publish_title(source_text: str, fallback: str) -> str:
    cleaned = re.sub(r"\s+", " ", source_text).strip()
    if cleaned:
        return cleaned[:28]
    return fallback or "默认标题"


def _fallback_publish_description(source_text: str, title: str) -> str:
    cleaned = re.sub(r"\s+", " ", source_text).strip()
    clean_title = title.strip() or (cleaned[:18] if cleaned else "本期内容")
    if cleaned:
        return f"这条视频围绕“{clean_title}”展开，提炼核心观点和关键判断，适合想快速理解重点、找到行动方向的人观看。"
    return clean_title


def _fallback_publish_tags(source_text: str) -> list[str]:
    words = re.findall(r"[\u4e00-\u9fff]{2,6}", source_text)
    stopwords = {"这个", "一个", "我们", "你们", "他们", "就是", "因为", "所以", "视频", "内容"}
    tags: list[str] = []
    for word in words:
        if word in stopwords or word in tags:
            continue
        tags.append(word)
        if len(tags) >= 5:
            break
    for default in ["短视频", "数字人", "口播"]:
        if len(tags) >= 5:
            break
        if default not in tags:
            tags.append(default)
    return tags


def _render_publish_text(title: str, tags: list[str]) -> str:
    tag_line = " ".join(f"#{tag.strip().strip('#')}" for tag in tags if tag.strip())
    return f"{title.strip()}\n{tag_line}".strip()


async def _upload_to_media_library(
    context: AppContext,
    file: UploadFile,
    filename: str,
    category: AssetCategory,
) -> ManagedAsset:
    cache_dir = resolve_runtime_paths(context.settings).cache / "web-uploads"
    cache_dir.mkdir(parents=True, exist_ok=True)
    temp_path = _unique_destination(cache_dir, filename)
    await _write_upload(file, temp_path)
    try:
        return context.media_library.import_file(category, temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


async def _write_upload(file: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)


def _unique_destination(directory: Path, filename: str) -> Path:
    base = sanitize_filename(Path(filename).stem, fallback="upload")
    suffix = Path(filename).suffix.lower()
    candidate = directory / f"{base}{suffix}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{base}-{index}{suffix}"
        index += 1
    return candidate


def _managed_asset_to_info(asset: ManagedAsset) -> AssetInfo:
    path = Path(asset.path)
    modified_at = _modified_at(path)
    return AssetInfo(
        asset_id=asset.asset_id,
        category=asset.category.value,
        name=asset.display_name,
        path=asset.path,
        size_bytes=asset.size_bytes,
        modified_at=modified_at,
        source="media-library",
    )


def _path_to_asset_info(path: Path, category: str, source: str) -> AssetInfo:
    stat = path.stat()
    return AssetInfo(
        asset_id=str(path.resolve()),
        category=category,
        name=path.stem,
        path=str(path.resolve()),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        source=source,
    )


def _workspace_asset_to_info(asset: WorkspaceAsset) -> AssetInfo:
    return AssetInfo(
        asset_id=asset.id,
        category=asset.kind,
        name=asset.display_name,
        path=asset.path,
        size_bytes=asset.size,
        modified_at=datetime.fromtimestamp(asset.mtime).isoformat(),
        source=asset.source_dir or "workspace",
        linked_text_path=asset.linked_text_path,
        linked_text_ref=asset.linked_text_ref,
        text_preview=asset.text_preview,
    )


def _modified_at(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return None
