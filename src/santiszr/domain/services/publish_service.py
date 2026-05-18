from __future__ import annotations

from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.publish import (
    PublishBatchRequest,
    PublishBatchResult,
    PublishPlatform,
    PublishRequest,
    PublishResult,
)
from santiszr.infra.publisher import DouyinPublisher, WechatChannelsPublisher, XiaohongshuPublisher


class PublishService:
    def __init__(
        self,
        douyin: DouyinPublisher | None = None,
        xiaohongshu: XiaohongshuPublisher | None = None,
        wechat_channels: WechatChannelsPublisher | None = None,
    ) -> None:
        self._adapters = {
            PublishPlatform.douyin: douyin or DouyinPublisher(),
            PublishPlatform.xiaohongshu: xiaohongshu or XiaohongshuPublisher(),
            PublishPlatform.wechat_channels: wechat_channels or WechatChannelsPublisher(),
        }

    def publish(self, request: PublishRequest) -> PublishResult:
        adapter = self._adapters.get(request.platform)
        if adapter is None:
            return PublishResult(
                success=False,
                platform=request.platform,
                status="failed",
                error=ErrorInfo(
                    code="publish_platform_unsupported",
                    message=f"Unsupported publish platform: {request.platform}",
                ),
            )
        return adapter.publish(request)

    def publish_batch(self, request: PublishBatchRequest) -> PublishBatchResult:
        results: list[PublishResult] = []
        try:
            for platform in request.platforms:
                result = self.publish(
                    PublishRequest(
                        platform=platform,
                        video_path=request.video_path,
                        title=request.title,
                        tags=request.tags,
                        cover_path=request.cover_path,
                        scheduled_at=request.scheduled_at,
                        workspace=request.workspace,
                        account_file=request.account_file,
                        category=request.category,
                        description=request.description,
                        browser_assist=request.browser_assist,
                    )
                )
                results.append(result)
                if not result.success and not request.continue_on_error:
                    break

            if not results:
                return PublishBatchResult(
                    success=False,
                    results=[],
                    summary="No publish platform was selected.",
                    error=ErrorInfo(
                        code="publish_batch_empty",
                        message="No publish platform was selected.",
                    ),
                )

            success = all(result.success for result in results)
            return PublishBatchResult(
                success=success,
                results=results,
                summary=self._build_summary(results),
            )
        except Exception as exc:
            return PublishBatchResult(
                success=False,
                results=results,
                summary=None,
                error=ErrorInfo(code="publish_batch_failed", message=str(exc)),
            )

    def _build_summary(self, results: list[PublishResult]) -> str:
        parts = [
            f"{result.platform.value}: {'success' if result.success else 'failed'}"
            for result in results
        ]
        return "; ".join(parts)
