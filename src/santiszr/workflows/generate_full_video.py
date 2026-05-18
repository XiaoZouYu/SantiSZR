from __future__ import annotations

from santiszr.domain.schemas.publish import (
    GenerateVideoWorkflowRequest,
    GenerateVideoWorkflowResult,
)
from santiszr.domain.services.workflow_service import WorkflowService


def generate_full_video(
    request: GenerateVideoWorkflowRequest,
) -> GenerateVideoWorkflowResult:
    service = WorkflowService()
    return service.generate_video(request)
