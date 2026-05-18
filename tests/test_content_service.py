from pathlib import Path

from santiszr.domain.schemas.content import ContentRequest, VideoSource
from santiszr.domain.services.content_service import ContentService
from santiszr.infra.downloader.douyin import DouyinDownloadInfo


class FakeTranscriber:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str] | None]] = []
        self.stream_calls: list[tuple[str, dict[str, str] | None]] = []
        self.responses: dict[str, str] = {}
        self.failures: set[str] = set()
        self.ready_calls = 0

    def ensure_ready(self) -> None:
        self.ready_calls += 1

    def transcribe(self, source: str | Path, source_headers: dict[str, str] | None = None) -> str:
        source_key = str(source)
        self.calls.append((source_key, source_headers))
        if source_key in self.failures:
            raise RuntimeError(f"cannot transcribe {source_key}")
        return self.responses[source_key]

    def transcribe_stream(self, source: str | Path, source_headers: dict[str, str] | None = None) -> str:
        source_key = str(source)
        self.stream_calls.append((source_key, source_headers))
        if source_key in self.failures:
            raise RuntimeError(f"cannot stream transcribe {source_key}")
        return self.responses[source_key]


class FakeFFmpeg:
    def __init__(self) -> None:
        self.audio_calls: list[dict[str, object]] = []
        self.frame_calls: list[dict[str, object]] = []

    def extract_audio(
        self,
        video_path: str | Path,
        output_path: str | Path,
        sample_rate: int = 22050,
        source_headers: dict[str, str] | None = None,
    ) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-audio")
        self.audio_calls.append(
            {
                "video_path": str(video_path),
                "output_path": str(output),
                "sample_rate": sample_rate,
                "source_headers": source_headers,
            }
        )
        return output

    def extract_frame(
        self,
        video_path: str | Path,
        output_path: str | Path,
        *,
        timestamp_sec: float = 0.0,
    ) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-frame")
        self.frame_calls.append(
            {
                "video_path": str(video_path),
                "output_path": str(output),
                "timestamp_sec": timestamp_sec,
            }
        )
        return output


class FakeDouyinDownloader:
    def __init__(self) -> None:
        self.info = DouyinDownloadInfo(
            share_url="https://v.douyin.com/demo/",
            resolved_url="https://www.iesdouyin.com/share/video/123456789/",
            video_id="123456789",
            title="demo-title",
            download_url="https://media.example.com/demo.mp4",
        )
        self.download_calls = 0

    def looks_like_douyin(self, text: str) -> bool:
        return "douyin.com" in text

    def parse_share_text(self, text: str) -> tuple[str | None, str]:
        return "https://v.douyin.com/demo/", "share-text"

    def fetch_info(self, source: str) -> DouyinDownloadInfo:
        return self.info

    def media_headers(self) -> dict[str, str]:
        return {"User-Agent": "fake-agent", "Referer": "https://www.douyin.com/"}

    def download(self, download_url: str, output_path: str | Path) -> Path:
        self.download_calls += 1
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake-video")
        return target


def test_content_service_extracts_local_video_with_cached_audio(sample_video: Path, temp_workspace: Path) -> None:
    transcriber = FakeTranscriber()
    ffmpeg = FakeFFmpeg()
    service = ContentService(ffmpeg=ffmpeg, transcriber=transcriber)
    request = ContentRequest(
        source=VideoSource(source_type="local_video", raw_input=str(sample_video)),
        workspace=str(temp_workspace),
        download_video=True,
        extract_audio=True,
    )
    cached_audio = service._transcription_audio_cache_path(
        request=request,
        content_dir=temp_workspace / "content",
        label=sample_video.stem,
    )
    transcriber.responses[str(cached_audio)] = "local video transcript"

    result = service.extract(request)

    assert result.success is True
    assert result.video_path == str(temp_workspace / "content" / sample_video.name)
    assert result.audio_path == str(cached_audio)
    assert result.cover_path == str(temp_workspace / "content" / f"{sample_video.stem}.jpg")
    assert result.transcript_path
    assert Path(result.video_path).exists()
    assert Path(result.audio_path).exists()
    assert Path(result.cover_path).exists()
    assert result.extracted_copy is not None
    assert result.extracted_copy.cleaned_text == "local video transcript"
    assert result.metadata["transcription_mode"] == "local_video"
    assert result.metadata["transcription_audio_path"] == str(cached_audio)
    assert transcriber.calls == [(str(cached_audio), None)]
    assert len(ffmpeg.audio_calls) == 1
    assert len(ffmpeg.frame_calls) == 1


def test_content_service_streams_douyin_from_cached_audio(temp_workspace: Path) -> None:
    downloader = FakeDouyinDownloader()
    transcriber = FakeTranscriber()
    ffmpeg = FakeFFmpeg()
    service = ContentService(downloader=downloader, ffmpeg=ffmpeg, transcriber=transcriber)
    request = ContentRequest(
        source=VideoSource(
            source_type="douyin_share_text",
            raw_input="8.21 婢跺秴鍩楅幍鎾崇磻閹舵牠鐓?https://v.douyin.com/demo/",
        ),
        workspace=str(temp_workspace),
    )
    cached_audio = service._transcription_audio_cache_path(
        request=request,
        content_dir=temp_workspace / "content",
        label=downloader.info.title,
    )
    transcriber.responses[str(cached_audio)] = "stream transcript"

    result = service.extract(request)

    assert result.success is True
    assert result.platform == "douyin"
    assert result.video_path is None
    assert result.audio_path is None
    assert result.cover_path is None
    assert result.extracted_copy is not None
    assert result.extracted_copy.cleaned_text == "stream transcript"
    assert result.metadata["transcription_mode"] == "douyin_stream"
    assert result.metadata["transcription_stream"] is True
    assert result.metadata["transcription_audio_path"] == str(cached_audio)
    assert transcriber.calls == []
    assert transcriber.stream_calls == [(str(cached_audio), None)]
    assert len(ffmpeg.audio_calls) == 1
    assert ffmpeg.audio_calls[0]["source_headers"] == downloader.media_headers()


def test_content_service_prefers_cached_audio_for_remote_douyin_transcription(temp_workspace: Path) -> None:
    downloader = FakeDouyinDownloader()
    transcriber = FakeTranscriber()
    ffmpeg = FakeFFmpeg()
    service = ContentService(downloader=downloader, ffmpeg=ffmpeg, transcriber=transcriber)
    request = ContentRequest(
        source=VideoSource(
            source_type="douyin_share_text",
            raw_input="8.21 复制打开抖音 https://v.douyin.com/demo/",
        ),
        workspace=str(temp_workspace),
        download_video=False,
        extract_audio=False,
        stream_transcription=False,
    )
    cached_audio = service._transcription_audio_cache_path(
        request=request,
        content_dir=temp_workspace / "content",
        label=downloader.info.title,
    )
    transcriber.responses[str(cached_audio)] = "remote transcript"

    result = service.extract(request)

    assert result.success is True
    assert result.platform == "douyin"
    assert result.video_path is None
    assert result.extracted_copy is not None
    assert result.extracted_copy.cleaned_text == "remote transcript"
    assert result.metadata["transcription_mode"] == "douyin_remote"
    assert result.metadata["transcription_audio_path"] == str(cached_audio)
    assert transcriber.calls == [(str(cached_audio), None)]
    assert len(ffmpeg.audio_calls) == 1


def test_content_service_fails_when_douyin_transcription_is_unavailable(temp_workspace: Path) -> None:
    downloader = FakeDouyinDownloader()
    transcriber = FakeTranscriber()
    ffmpeg = FakeFFmpeg()
    service = ContentService(downloader=downloader, ffmpeg=ffmpeg, transcriber=transcriber)
    request = ContentRequest(
        source=VideoSource(
            source_type="douyin_share_text",
            raw_input="8.21 婢跺秴鍩楅幍鎾崇磻閹舵牠鐓?https://v.douyin.com/demo/",
        ),
        workspace=str(temp_workspace),
        download_video=True,
        extract_audio=False,
        stream_transcription=False,
    )
    cached_audio = service._transcription_audio_cache_path(
        request=request,
        content_dir=temp_workspace / "content",
        label=downloader.info.title,
    )
    transcriber.failures.add(str(cached_audio))

    result = service.extract(request)

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "content_transcription_failed"
    assert "cannot transcribe" in result.error.message
    assert result.video_path == str(temp_workspace / "content" / "demo-title.mp4")
    assert result.extracted_copy is None
    assert result.metadata["transcription_mode"] == "douyin_local_fallback"
    assert result.metadata["transcription_success"] is False
    assert any("Fallback text was not used" in note for note in result.notes)


def test_content_service_fails_when_local_video_transcription_is_unavailable(
    sample_video: Path,
    temp_workspace: Path,
) -> None:
    transcriber = FakeTranscriber()
    ffmpeg = FakeFFmpeg()
    service = ContentService(ffmpeg=ffmpeg, transcriber=transcriber)
    request = ContentRequest(
        source=VideoSource(source_type="local_video", raw_input=str(sample_video)),
        workspace=str(temp_workspace),
        extract_audio=False,
    )
    cached_audio = service._transcription_audio_cache_path(
        request=request,
        content_dir=temp_workspace / "content",
        label=sample_video.stem,
    )
    transcriber.failures.add(str(cached_audio))

    result = service.extract(request)

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "content_transcription_failed"
    assert result.video_path == str(sample_video.resolve())
    assert result.metadata["transcription_mode"] == "local_video"
    assert result.metadata["transcription_success"] is False


def test_content_service_reuses_cached_audio_but_reruns_transcription(sample_video: Path, temp_workspace: Path) -> None:
    transcriber = FakeTranscriber()
    ffmpeg = FakeFFmpeg()
    service = ContentService(ffmpeg=ffmpeg, transcriber=transcriber)
    request = ContentRequest(
        source=VideoSource(source_type="local_video", raw_input=str(sample_video)),
        workspace=str(temp_workspace),
        extract_audio=False,
    )
    cached_audio = service._transcription_audio_cache_path(
        request=request,
        content_dir=temp_workspace / "content",
        label=sample_video.stem,
    )
    transcriber.responses[str(cached_audio)] = "cached local video transcript"

    first = service.extract(request)
    second = service.extract(request)

    assert first.success is True
    assert second.success is True
    assert first.extracted_copy is not None
    assert second.extracted_copy is not None
    assert first.extracted_copy.cleaned_text == "cached local video transcript"
    assert second.extracted_copy.cleaned_text == "cached local video transcript"
    assert len(transcriber.calls) == 2
    assert transcriber.calls[0][0] == str(cached_audio)
    assert transcriber.calls[1][0] == str(cached_audio)
    assert transcriber.ready_calls == 2
    assert len(ffmpeg.audio_calls) == 1
    assert second.metadata["transcription_audio_cache_hit"] is True
    assert any("Reused cached transcription audio." in note for note in second.notes)


def test_content_service_uses_fast_path_for_local_video_text_extraction(sample_video: Path, temp_workspace: Path) -> None:
    transcriber = FakeTranscriber()
    ffmpeg = FakeFFmpeg()
    service = ContentService(ffmpeg=ffmpeg, transcriber=transcriber)
    request = ContentRequest(
        source=VideoSource(source_type="local_video", raw_input=str(sample_video)),
        workspace=str(temp_workspace),
        download_video=False,
        extract_audio=False,
    )
    cached_audio = service._transcription_audio_cache_path(
        request=request,
        content_dir=temp_workspace / "content",
        label=sample_video.stem,
    )
    transcriber.responses[str(cached_audio)] = "fast local transcript"

    result = service.extract(request)

    assert result.success is True
    assert result.video_path == str(sample_video.resolve())
    assert result.audio_path is None
    assert result.cover_path is None
    assert transcriber.calls == [(str(cached_audio), None)]
    assert len(ffmpeg.audio_calls) == 1
    assert not (temp_workspace / "content" / sample_video.name).exists()
