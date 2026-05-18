from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil

import httpx

from santiszr.core.paths import ensure_module_dir, sanitize_filename
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.content import ContentRequest, ContentResult, ExtractedCopy
from santiszr.infra.downloader.douyin import DouyinDownloader
from santiszr.infra.media.ffmpeg import FFmpegAdapter
from santiszr.infra.transcription import WhisperTranscriber


_VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


class ContentService:
    def __init__(
        self,
        downloader: DouyinDownloader | None = None,
        ffmpeg: FFmpegAdapter | None = None,
        transcriber: object | None = None,
    ) -> None:
        self.downloader = downloader or DouyinDownloader()
        self.ffmpeg = ffmpeg or FFmpegAdapter()
        self.transcriber = transcriber or WhisperTranscriber(ffmpeg=self.ffmpeg)
        self._http = httpx.Client(trust_env=False, follow_redirects=True, timeout=60.0)

    def extract(self, request: ContentRequest) -> ContentResult:
        workspace_dir = Path(request.workspace).expanduser().resolve()
        content_dir = ensure_module_dir(workspace_dir, "content")
        notes: list[str] = []

        try:
            raw_input = request.source.raw_input.strip()
            extracted_text = ""
            title: str | None = None
            source_url: str | None = None
            resolved_url: str | None = None
            video_id: str | None = None
            video_path: Path | None = None
            audio_path: Path | None = None
            cover_path: Path | None = None
            platform = "text"
            metadata: dict[str, str | int | float | bool | None] = {}

            local_path = Path(raw_input)
            if request.source.source_type == "raw_text":
                extracted_text = raw_input
            elif local_path.exists():
                if local_path.suffix.lower() in _VIDEO_SUFFIXES | _AUDIO_SUFFIXES:
                    self._ensure_transcriber_ready()
                platform, title, video_path, audio_path, extracted_text, metadata = self._handle_local_source(
                    request=request,
                    source_path=local_path,
                    content_dir=content_dir,
                    notes=notes,
                )
                if platform in {"local_video", "local_audio"} and not extracted_text:
                    return self._build_transcription_failure(
                        workspace_dir=workspace_dir,
                        platform=platform,
                        source_type=request.source.source_type,
                        source_url=source_url,
                        resolved_url=resolved_url,
                        video_id=video_id,
                        title=title,
                        video_path=video_path,
                        audio_path=audio_path,
                        cover_path=cover_path,
                        metadata=metadata,
                        notes=notes,
                        fallback_text=raw_input,
                    )
            elif self.downloader.looks_like_douyin(raw_input) or request.source.source_type == "douyin_share_text":
                self._ensure_transcriber_ready()
                share_url, share_text = self.downloader.parse_share_text(raw_input)
                info = self.downloader.fetch_info(share_url or raw_input)
                platform = "douyin"
                title = info.title
                source_url = info.share_url
                resolved_url = info.resolved_url
                video_id = info.video_id
                metadata["share_text"] = share_text or ""
                metadata["download_url"] = info.download_url

                prepared_audio = self._prepare_transcription_audio(
                    request=request,
                    content_dir=content_dir,
                    source=info.download_url,
                    label=title or video_id or "douyin-audio",
                    notes=notes,
                    metadata=metadata,
                    source_headers=self.downloader.media_headers(),
                )
                extracted_text = self._transcribe_source(
                    prepared_audio,
                    notes=notes,
                    metadata=metadata,
                    mode="douyin_stream" if request.stream_transcription else "douyin_remote",
                    stream=request.stream_transcription,
                )
                if request.extract_audio:
                    audio_path = prepared_audio
                if request.download_video:
                    output_name = sanitize_filename(title or video_id or "douyin-video", fallback="douyin-video")
                    target_video = content_dir / f"{output_name}.mp4"
                    if target_video.exists():
                        video_path = target_video
                        notes.append("Reused cached downloaded video.")
                    else:
                        video_path = self.downloader.download(info.download_url, target_video)

                if not extracted_text and video_path:
                    extracted_text = self._transcribe_source(
                        prepared_audio,
                        notes=notes,
                        metadata=metadata,
                        mode="douyin_local_fallback",
                    )
                if not extracted_text:
                    return self._build_transcription_failure(
                        workspace_dir=workspace_dir,
                        platform=platform,
                        source_type=request.source.source_type,
                        source_url=source_url,
                        resolved_url=resolved_url,
                        video_id=video_id,
                        title=title,
                        video_path=video_path,
                        audio_path=audio_path,
                        cover_path=cover_path,
                        metadata=metadata,
                        notes=notes,
                        fallback_text=share_text or info.title,
                    )
            elif raw_input.startswith(("http://", "https://")):
                platform = "remote_url"
                source_url = raw_input
                title = Path(raw_input.split("?", 1)[0]).stem or "remote-video"
                if self._looks_like_media_url(raw_input):
                    self._ensure_transcriber_ready()
                    prepared_audio = self._prepare_transcription_audio(
                        request=request,
                        content_dir=content_dir,
                        source=raw_input,
                        label=title,
                        notes=notes,
                        metadata=metadata,
                    )
                    extracted_text = self._transcribe_source(
                        prepared_audio,
                        notes=notes,
                        metadata=metadata,
                        mode="remote_stream" if request.stream_transcription else "remote_audio",
                        stream=request.stream_transcription,
                    )
                    if request.extract_audio:
                        audio_path = prepared_audio
                    if request.download_video:
                        suffix = Path(raw_input.split("?", 1)[0]).suffix or ".mp4"
                        output_name = sanitize_filename(title, fallback="remote-media")
                        video_path = self._download_direct_url(raw_input, content_dir / f"{output_name}{suffix}")
                    if not extracted_text:
                        return self._build_transcription_failure(
                            workspace_dir=workspace_dir,
                            platform=platform,
                            source_type=request.source.source_type,
                            source_url=source_url,
                            resolved_url=resolved_url,
                            video_id=video_id,
                            title=title,
                            video_path=video_path,
                            audio_path=audio_path,
                            cover_path=cover_path,
                            metadata=metadata,
                            notes=notes,
                            fallback_text=raw_input,
                        )
                else:
                    extracted_text = raw_input
                    notes.append("Remote URL did not look like a direct media file, so only the URL was preserved.")
            else:
                extracted_text = raw_input

            if request.extract_audio and audio_path is None and video_path:
                audio_path = self._prepare_transcription_audio(
                    request=request,
                    content_dir=content_dir,
                    source=video_path,
                    label=Path(video_path).stem,
                    notes=notes,
                    metadata=metadata,
                )

            if video_path and self._should_generate_cover(request):
                cover_output = content_dir / f"{Path(video_path).stem}.jpg"
                if cover_output.exists():
                    cover_path = cover_output
                    notes.append("Reused cached cover frame.")
                else:
                    try:
                        cover_path = self.ffmpeg.extract_frame(video_path, cover_output)
                    except Exception as exc:
                        notes.append(f"Cover extraction skipped: {exc}")

            cleaned_text = self._clean_copy(extracted_text or title or raw_input)
            transcript_path = content_dir / "extracted_copy.txt"
            transcript_path.write_text(cleaned_text, encoding="utf-8")

            return ContentResult(
                success=True,
                platform=platform,
                workspace=str(workspace_dir),
                video_id=video_id,
                source_url=source_url,
                resolved_url=resolved_url,
                title=title or cleaned_text[:20],
                video_path=str(video_path) if video_path else None,
                audio_path=str(audio_path) if audio_path else None,
                cover_path=str(cover_path) if cover_path else None,
                transcript_path=str(transcript_path),
                extracted_copy=ExtractedCopy(
                    raw_text=extracted_text or cleaned_text,
                    cleaned_text=cleaned_text,
                    title=title,
                    source=request.source.source_type,
                ),
                metadata=metadata,
                notes=notes,
            )
        except Exception as exc:
            return ContentResult(
                success=False,
                workspace=str(workspace_dir),
                error=ErrorInfo(code="content_extract_failed", message=str(exc)),
            )

    def _handle_local_source(
        self,
        *,
        request: ContentRequest,
        source_path: Path,
        content_dir: Path,
        notes: list[str],
    ) -> tuple[str, str, Path | None, Path | None, str, dict[str, str | int | float | bool | None]]:
        resolved_source = source_path.expanduser().resolve()
        extracted_text = self._read_sidecar_text(source_path)
        metadata: dict[str, str | int | float | bool | None] = {"source_path": str(resolved_source)}
        suffix = source_path.suffix.lower()

        if suffix in _VIDEO_SUFFIXES:
            video_path = self._copy_local_video_if_needed(
                source_path=resolved_source,
                content_dir=content_dir,
                notes=notes,
                enabled=request.download_video,
            )
            prepared_audio = self._prepare_transcription_audio(
                request=request,
                content_dir=content_dir,
                source=resolved_source,
                label=source_path.stem,
                notes=notes,
                metadata=metadata,
            )
            if not extracted_text:
                extracted_text = self._transcribe_source(
                    prepared_audio,
                    notes=notes,
                    metadata=metadata,
                    mode="local_video",
                )
            return (
                "local_video",
                source_path.stem,
                video_path or resolved_source,
                prepared_audio if request.extract_audio else None,
                extracted_text,
                metadata,
            )

        if suffix in _AUDIO_SUFFIXES:
            prepared_audio = self._prepare_transcription_audio(
                request=request,
                content_dir=content_dir,
                source=resolved_source,
                label=source_path.stem,
                notes=notes,
                metadata=metadata,
            )
            if not extracted_text:
                extracted_text = self._transcribe_source(
                    prepared_audio,
                    notes=notes,
                    metadata=metadata,
                    mode="local_audio",
                )
            return (
                "local_audio",
                source_path.stem,
                None,
                prepared_audio if request.extract_audio else None,
                extracted_text,
                metadata,
            )

        if suffix in {".txt", ".md"}:
            text = source_path.read_text(encoding="utf-8")
            return "local_text", source_path.stem, None, None, text, metadata

        return "local_file", source_path.stem, resolved_source, None, extracted_text or source_path.stem, metadata

    def _prepare_transcription_audio(
        self,
        *,
        request: ContentRequest,
        content_dir: Path,
        source: str | Path,
        label: str,
        notes: list[str],
        metadata: dict[str, str | int | float | bool | None],
        source_headers: dict[str, str] | None = None,
    ) -> Path:
        audio_path = self._transcription_audio_cache_path(request=request, content_dir=content_dir, label=label)
        if audio_path.exists():
            metadata["transcription_audio_cache_hit"] = True
            metadata["transcription_audio_path"] = str(audio_path)
            notes.append("Reused cached transcription audio.")
            return audio_path

        extracted_audio = self.ffmpeg.extract_audio(
            source,
            audio_path,
            sample_rate=16000,
            source_headers=source_headers,
        )
        metadata["transcription_audio_cache_hit"] = False
        metadata["transcription_audio_path"] = str(extracted_audio)
        notes.append("Prepared transcription audio cache.")
        return extracted_audio

    def _copy_local_video_if_needed(
        self,
        *,
        source_path: Path,
        content_dir: Path,
        notes: list[str],
        enabled: bool,
    ) -> Path | None:
        if not enabled:
            return None

        target_path = content_dir / f"{sanitize_filename(source_path.stem)}{source_path.suffix.lower()}"
        if target_path.exists():
            try:
                source_stat = source_path.stat()
                target_stat = target_path.stat()
                if (
                    source_stat.st_size == target_stat.st_size
                    and source_stat.st_mtime_ns == target_stat.st_mtime_ns
                ):
                    notes.append("Reused cached local video copy.")
                    return target_path
            except Exception:
                pass

        if source_path != target_path:
            shutil.copy2(source_path, target_path)
        return target_path

    def _download_direct_url(self, url: str, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            return output_path
        with self._http.stream("GET", url) as response:
            response.raise_for_status()
            with output_path.open("wb") as handle:
                for chunk in response.iter_bytes():
                    if chunk:
                        handle.write(chunk)
        return output_path

    def _looks_like_media_url(self, url: str) -> bool:
        return bool(re.search(r"\.(mp4|mov|avi|mkv|webm|mp3|wav|m4a)(?:\?|$)", url, re.IGNORECASE))

    def _read_sidecar_text(self, source_path: Path) -> str:
        for suffix in (".txt", ".srt", ".md"):
            sidecar = source_path.with_suffix(suffix)
            if sidecar.exists():
                return sidecar.read_text(encoding="utf-8")
        return ""

    def _transcribe_source(
        self,
        source: str | Path,
        *,
        notes: list[str],
        metadata: dict[str, str | int | float | bool | None],
        mode: str,
        source_headers: dict[str, str] | None = None,
        stream: bool = False,
    ) -> str:
        transcriber = getattr(self, "transcriber", None)
        if transcriber is None:
            notes.append(f"Transcription skipped for {mode}: transcriber is not configured.")
            return ""

        try:
            if stream:
                stream_method = getattr(transcriber, "transcribe_stream", None)
                if stream_method is None:
                    raise RuntimeError("transcriber does not support streaming transcription.")
                transcript = stream_method(source, source_headers=source_headers)
            else:
                transcript = transcriber.transcribe(source, source_headers=source_headers)
            metadata["transcription_mode"] = mode
            metadata["transcription_source"] = str(source)
            metadata["transcription_stream"] = stream
            metadata["transcription_success"] = True
            metadata["transcription_error"] = None
            self._record_transcription_runtime(transcriber, metadata=metadata, notes=notes)
            return transcript
        except Exception as exc:
            metadata["transcription_mode"] = mode
            metadata["transcription_source"] = str(source)
            metadata["transcription_stream"] = stream
            metadata["transcription_success"] = False
            metadata["transcription_error"] = str(exc)
            self._record_transcription_runtime(transcriber, metadata=metadata, notes=notes)
            notes.append(f"Transcription skipped for {mode}: {exc}")
            return ""

    def _record_transcription_runtime(
        self,
        transcriber: object,
        *,
        metadata: dict[str, str | int | float | bool | None],
        notes: list[str],
    ) -> None:
        runtime = str(getattr(transcriber, "last_runtime", "") or "").strip()
        if runtime:
            metadata["transcription_runtime"] = runtime
        quick_mode = getattr(transcriber, "quick_mode", None)
        if isinstance(quick_mode, bool):
            metadata["transcription_quick_mode"] = quick_mode
        fallback_reason = str(getattr(transcriber, "last_runtime_fallback_reason", "") or "").strip()
        if fallback_reason:
            metadata["transcription_runtime_fallback_reason"] = fallback_reason
        runtime_note = ""
        if runtime:
            runtime_note = f"Transcription runtime: {runtime}"
            if fallback_reason and fallback_reason != "forced by configuration":
                runtime_note = f"{runtime_note} ({fallback_reason})"
        elif fallback_reason:
            runtime_note = f"Transcription runtime detail: {fallback_reason}"
        if runtime_note and runtime_note not in notes:
            notes.append(runtime_note)

    def _should_generate_cover(self, request: ContentRequest) -> bool:
        return request.download_video

    def _ensure_transcriber_ready(self) -> None:
        transcriber = getattr(self, "transcriber", None)
        if transcriber is None:
            raise RuntimeError("Transcriber is not configured.")
        prepare = getattr(transcriber, "ensure_ready", None)
        if callable(prepare):
            prepare()

    def _transcription_audio_cache_path(self, *, request: ContentRequest, content_dir: Path, label: str) -> Path:
        safe_label = sanitize_filename(label, fallback="audio")
        source_key = self._source_cache_key(request)
        cache_dir = content_dir / ".extract-cache" / "audio"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / f"{safe_label}_{source_key[:16]}.wav"

    def _source_cache_key(self, request: ContentRequest) -> str:
        payload = self._source_cache_descriptor(request)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _source_cache_descriptor(self, request: ContentRequest) -> dict[str, object]:
        raw_input = request.source.raw_input.strip()
        local_path = Path(raw_input)
        source_descriptor: dict[str, object]
        if local_path.exists():
            resolved_path = local_path.expanduser().resolve()
            stat = resolved_path.stat()
            source_descriptor = {
                "kind": "local",
                "path": str(resolved_path),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        else:
            source_descriptor = {
                "kind": "text",
                "value": raw_input,
            }
        return {
            "source_type": request.source.source_type,
            "source": source_descriptor,
        }

    def _build_transcription_failure(
        self,
        *,
        workspace_dir: Path,
        platform: str,
        source_type: str,
        source_url: str | None,
        resolved_url: str | None,
        video_id: str | None,
        title: str | None,
        video_path: Path | None,
        audio_path: Path | None,
        cover_path: Path | None,
        metadata: dict[str, str | int | float | bool | None],
        notes: list[str],
        fallback_text: str,
    ) -> ContentResult:
        error_message = (
            str(metadata.get("transcription_error"))
            if metadata.get("transcription_error")
            else "No transcript could be produced from the media source."
        )
        notes.append(f"Fallback text was not used as extracted copy: {fallback_text[:80]}")
        return ContentResult(
            success=False,
            platform=platform,
            workspace=str(workspace_dir),
            video_id=video_id,
            source_url=source_url,
            resolved_url=resolved_url,
            title=title,
            video_path=str(video_path) if video_path else None,
            audio_path=str(audio_path) if audio_path else None,
            cover_path=str(cover_path) if cover_path else None,
            metadata=metadata,
            notes=notes,
            error=ErrorInfo(
                code="content_transcription_failed",
                message=error_message,
                detail={
                    "source_type": source_type,
                    "platform": platform,
                },
            ),
        )

    def _clean_copy(self, text: str) -> str:
        cleaned = re.sub(r"https?://\S+", " ", text)
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = cleaned.replace("复制此链接", " ").replace("打开抖音", " ")
        return cleaned.strip(" ：:，,。.;；!！?？#\n")
