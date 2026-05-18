from __future__ import annotations

from pathlib import Path

from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.publish import (
    PublishBatchRequest,
    PublishPlatform,
    PublishRequest,
    PublishResult,
)
from santiszr.domain.services.publish_service import PublishService
from santiszr.infra.publisher import base as publisher_base
from santiszr.infra.publisher.base import CommandExecution
from santiszr.infra.publisher.douyin import DouyinPublisher
from santiszr.infra.publisher.wechat_channels import WechatChannelsPublisher
from santiszr.infra.publisher.xiaohongshu import XiaohongshuPublisher


class FakeRunner:
    def __init__(self, result: CommandExecution | None = None) -> None:
        self.result = result or CommandExecution(returncode=0, stdout="ok", stderr="")
        self.calls: list[dict[str, object]] = []

    def run(self, command: list[str], *, cwd: Path) -> CommandExecution:
        self.calls.append({"command": command, "cwd": cwd})
        return self.result


class FakePlatformPublisher:
    def __init__(self, result: PublishResult) -> None:
        self.result = result
        self.requests: list[PublishRequest] = []

    def publish(self, request: PublishRequest) -> PublishResult:
        self.requests.append(request)
        return self.result


def test_publishers_return_structured_not_configured_failures(temp_workspace: Path) -> None:
    video_path = temp_workspace / "video.mp4"
    cover_path = temp_workspace / "cover.png"
    video_path.write_bytes(b"video")
    cover_path.write_bytes(b"cover")

    publishers = [
        (PublishPlatform.douyin, DouyinPublisher(runner=FakeRunner())),
        (PublishPlatform.xiaohongshu, XiaohongshuPublisher(runner=FakeRunner())),
        (PublishPlatform.wechat_channels, WechatChannelsPublisher(runner=FakeRunner())),
    ]

    for platform, publisher in publishers:
        result = publisher.publish(
            PublishRequest(
                platform=platform,
                video_path=str(video_path),
                cover_path=str(cover_path),
                title="Demo title",
                tags=["alpha", "beta"],
            )
        )

        assert result.success is False
        assert result.status == "failed"
        assert result.error is not None
        assert result.error.code == "publish_not_configured"
        assert "only generate publishing materials" in result.error.message
        assert result.command == []
        assert any("not bundled" in note for note in result.notes)
        assert publisher.runner.calls == []  # type: ignore[attr-defined]


def test_publish_service_can_publish_batch_and_keep_structured_failures(temp_workspace: Path) -> None:
    video_path = temp_workspace / "video.mp4"
    video_path.write_bytes(b"video")

    service = PublishService(
        douyin=DouyinPublisher(),  # type: ignore[arg-type]
        xiaohongshu=XiaohongshuPublisher(),  # type: ignore[arg-type]
        wechat_channels=WechatChannelsPublisher(),  # type: ignore[arg-type]
    )

    result = service.publish_batch(
        PublishBatchRequest(
            platforms=[
                PublishPlatform.douyin,
                PublishPlatform.xiaohongshu,
                PublishPlatform.wechat_channels,
            ],
            video_path=str(video_path),
            title="Demo",
            tags=["tag"],
            continue_on_error=True,
        )
    )

    assert result.success is False
    assert [item.platform for item in result.results] == [
        PublishPlatform.douyin,
        PublishPlatform.xiaohongshu,
        PublishPlatform.wechat_channels,
    ]
    assert result.summary == "douyin: failed; xiaohongshu: failed; wechat_channels: failed"
    assert all(item.error and item.error.code == "publish_not_configured" for item in result.results)


def test_unconfigured_publishers_can_open_browser_assist_when_requested(
    temp_workspace: Path,
    monkeypatch,
) -> None:
    video_path = temp_workspace / "video.mp4"
    cover_path = temp_workspace / "cover.png"
    video_path.write_bytes(b"video")
    cover_path.write_bytes(b"cover")
    calls: list[dict[str, object]] = []

    def fake_publish(request: PublishRequest, *, video_path: Path, cover_path: Path | None) -> PublishResult:
        calls.append({"request": request, "video_path": video_path, "cover_path": cover_path})
        return PublishResult(
            success=True,
            platform=request.platform,
            status="browser_opened",
            command=["browser-assist", request.platform.value],
            notes=["opened"],
        )

    monkeypatch.setattr(publisher_base.browser_publish_assistant, "publish", fake_publish)
    publisher = DouyinPublisher(runner=FakeRunner())

    result = publisher.publish(
        PublishRequest(
            platform=PublishPlatform.douyin,
            video_path=str(video_path),
            cover_path=str(cover_path),
            title="Demo title",
            tags=["tag"],
            browser_assist=True,
        )
    )

    assert result.success is True
    assert result.status == "browser_opened"
    assert calls[0]["video_path"] == video_path.resolve()
    assert calls[0]["cover_path"] == cover_path.resolve()
    assert publisher.runner.calls == []  # type: ignore[attr-defined]


def test_publish_service_stops_early_when_continue_on_error_disabled() -> None:
    douyin = FakePlatformPublisher(
        PublishResult(
            success=False,
            platform=PublishPlatform.douyin,
            status="failed",
            error=ErrorInfo(code="publish_not_configured", message="not configured"),
        )
    )
    xhs = FakePlatformPublisher(
        PublishResult(success=True, platform=PublishPlatform.xiaohongshu, status="published")
    )
    service = PublishService(
        douyin=douyin,  # type: ignore[arg-type]
        xiaohongshu=xhs,  # type: ignore[arg-type]
    )

    result = service.publish_batch(
        PublishBatchRequest(
            platforms=[PublishPlatform.douyin, PublishPlatform.xiaohongshu],
            video_path="D:/tmp/output.mp4",
            title="Demo",
            continue_on_error=False,
        )
    )

    assert result.success is False
    assert len(result.results) == 1
    assert result.results[0].platform is PublishPlatform.douyin
    assert len(xhs.requests) == 0


def test_configured_publishers_execute_platform_scripts(temp_workspace: Path) -> None:
    publisher_root = temp_workspace / "publisher"
    (publisher_root / "uploader" / "xiaohongshu_uploader").mkdir(parents=True)
    (publisher_root / "upload_video_to_douyin.py").write_text("print('douyin')", encoding="utf-8")
    (publisher_root / "uploader" / "xiaohongshu_uploader" / "main.py").write_text("print('xhs')", encoding="utf-8")
    (publisher_root / "upload_video_to_tencent.py").write_text("print('wechat')", encoding="utf-8")
    video_path = temp_workspace / "video.mp4"
    cover_path = temp_workspace / "cover.png"
    video_path.write_bytes(b"video")
    cover_path.write_bytes(b"cover")

    douyin_runner = FakeRunner()
    xhs_runner = FakeRunner()
    wechat_runner = FakeRunner()

    request = PublishRequest(
        platform=PublishPlatform.douyin,
        video_path=str(video_path),
        cover_path=str(cover_path),
        title="Demo title",
        tags=["#alpha", "beta"],
        account_file="account.json",
        category="财经",
    )
    douyin = DouyinPublisher(project_root=publisher_root, python_executable="python", runner=douyin_runner)
    xhs = XiaohongshuPublisher(project_root=publisher_root, python_executable="python", runner=xhs_runner)
    wechat = WechatChannelsPublisher(project_root=publisher_root, python_executable="python", runner=wechat_runner)

    douyin_result = douyin.publish(request)
    xhs_result = xhs.publish(request.model_copy(update={"platform": PublishPlatform.xiaohongshu}))
    wechat_result = wechat.publish(request.model_copy(update={"platform": PublishPlatform.wechat_channels}))

    assert douyin_result.success is True
    assert xhs_result.success is True
    assert wechat_result.success is True
    assert douyin_runner.calls[0]["command"] == [
        "python",
        str(publisher_root / "upload_video_to_douyin.py"),
        str(video_path.resolve()),
        str(cover_path.resolve()),
        "Demo title",
        "alpha,beta",
    ]
    assert xhs_runner.calls[0]["command"] == [
        "python",
        str(publisher_root / "uploader" / "xiaohongshu_uploader" / "main.py"),
        "--title",
        "Demo title",
        "--file_path",
        str(video_path.resolve()),
        "--tags",
        "alpha,beta",
        "--account_file",
        "account.json",
        "--thumbnail_path",
        str(cover_path.resolve()),
    ]
    assert wechat_runner.calls[0]["command"] == [
        "python",
        str(publisher_root / "upload_video_to_tencent.py"),
        "--file_path",
        str(video_path.resolve()),
        "--title",
        "Demo title",
        "--tags",
        "alpha,beta",
        "--account_file",
        "account.json",
        "--category",
        "财经",
        "--cover_path",
        str(cover_path.resolve()),
    ]


def test_publisher_reports_script_failures(temp_workspace: Path) -> None:
    publisher_root = temp_workspace / "publisher"
    publisher_root.mkdir()
    (publisher_root / "upload_video_to_douyin.py").write_text("raise SystemExit(1)", encoding="utf-8")
    video_path = temp_workspace / "video.mp4"
    video_path.write_bytes(b"video")
    runner = FakeRunner(CommandExecution(returncode=3, stdout="out", stderr="boom"))
    publisher = DouyinPublisher(project_root=publisher_root, python_executable="python", runner=runner)

    result = publisher.publish(
        PublishRequest(
            platform=PublishPlatform.douyin,
            video_path=str(video_path),
            title="Demo",
            tags=["tag"],
        )
    )

    assert result.success is False
    assert result.status == "failed"
    assert result.stdout == "out"
    assert result.stderr == "boom"
    assert result.error is not None
    assert result.error.code == "publish_failed"


def test_configured_publisher_reports_missing_script(temp_workspace: Path) -> None:
    publisher_root = temp_workspace / "publisher"
    publisher_root.mkdir()
    video_path = temp_workspace / "video.mp4"
    video_path.write_bytes(b"video")
    runner = FakeRunner()
    publisher = DouyinPublisher(project_root=publisher_root, python_executable="python", runner=runner)

    result = publisher.publish(
        PublishRequest(
            platform=PublishPlatform.douyin,
            video_path=str(video_path),
            title="Demo",
        )
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "publish_script_missing"
    assert runner.calls == []
