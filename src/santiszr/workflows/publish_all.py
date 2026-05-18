from __future__ import annotations

from santiszr.domain.schemas.publish import PublishBatchRequest, PublishBatchResult
from santiszr.domain.services.publish_service import PublishService


def publish_all(request: PublishBatchRequest) -> PublishBatchResult:
    service = PublishService()
    return service.publish_batch(request)
