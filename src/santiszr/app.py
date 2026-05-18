from __future__ import annotations

from dataclasses import dataclass
import sys

from santiszr.config.settings import AppSettings, load_settings
from santiszr.core.asset_library import MediaLibrary
from santiszr.core.app_state import resolve_saved_workspace
from santiszr.core.llm_config import load_persisted_llm_settings
from santiszr.core.logger import configure_logging, get_logger
from santiszr.core.paths import ensure_runtime_directories
from santiszr.domain.services.avatar_service import AvatarService
from santiszr.domain.services.content_service import ContentService
from santiszr.domain.services.publish_service import PublishService
from santiszr.domain.services.rewrite_service import RewriteService
from santiszr.domain.services.subtitle_service import SubtitleService
from santiszr.domain.services.tts_service import TTSService
from santiszr.domain.services.workflow_service import WorkflowService
from santiszr.gui.state import PipelineState, TaskController
from santiszr.infra.llm.client import LLMClient
from santiszr.infra.subtitle.corrector import SubtitleCorrector


@dataclass(slots=True)
class ServiceRegistry:
    content: ContentService
    rewrite: RewriteService
    tts: TTSService
    subtitle: SubtitleService
    avatar: AvatarService
    workflow: WorkflowService
    publish: PublishService


@dataclass(slots=True)
class AppContext:
    settings: AppSettings
    services: ServiceRegistry
    media_library: MediaLibrary
    state: PipelineState
    task_controller: TaskController


@dataclass(slots=True)
class GuiContext:
    app_context: AppContext
    application: object
    window: object


def bootstrap(settings: AppSettings | None = None) -> AppContext:
    app_settings = settings or load_settings()
    load_persisted_llm_settings(app_settings)
    runtime_paths = ensure_runtime_directories(app_settings)
    saved_workspace = resolve_saved_workspace(app_settings)
    configure_logging(app_settings)
    llm_client = LLMClient(
        api_key=app_settings.llm.api_key,
        api_base=app_settings.llm.api_base,
        model=app_settings.llm.model,
        timeout_sec=app_settings.llm.timeout_sec,
    )
    content_service = ContentService()
    rewrite_service = RewriteService(llm_client=llm_client)
    tts_service = TTSService()
    subtitle_service = SubtitleService(corrector=SubtitleCorrector(llm_client=llm_client))
    avatar_service = AvatarService()
    services = ServiceRegistry(
        content=content_service,
        rewrite=rewrite_service,
        tts=tts_service,
        subtitle=subtitle_service,
        avatar=avatar_service,
        workflow=WorkflowService(
            content_service=content_service,
            rewrite_service=rewrite_service,
            tts_service=tts_service,
            subtitle_service=subtitle_service,
            avatar_service=avatar_service,
        ),
        publish=PublishService(),
    )
    state = PipelineState(
        workspace=str(saved_workspace) if saved_workspace is not None else "",
        preferred_voice=app_settings.tts.default_voice,
        preferred_avatar_model_id=app_settings.avatar.default_model_id,
    )
    task_controller = TaskController(
        state=state,
        app_settings=app_settings,
        python_executable=sys.executable,
        project_root=runtime_paths.root,
    )
    get_logger(__name__).info("Bootstrap completed for %s", app_settings.app_name)
    return AppContext(
        settings=app_settings,
        services=services,
        media_library=MediaLibrary(app_settings),
        state=state,
        task_controller=task_controller,
    )


def create_gui_context(settings: AppSettings | None = None) -> GuiContext:
    from PySide6.QtWidgets import QApplication

    from santiszr.gui.main_window import MainWindow

    app_context = bootstrap(settings=settings)
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName(app_context.settings.app_name)
    application.setOrganizationName("SantiSZR")

    window = MainWindow(app_context=app_context)
    return GuiContext(app_context=app_context, application=application, window=window)


def main(settings: AppSettings | None = None) -> int:
    gui_context = create_gui_context(settings=settings)
    gui_context.window.show()
    return gui_context.application.exec()


def dev_main() -> int:
    settings = load_settings()
    debug_settings = settings.model_copy(
        update={
            "debug": True,
            "app_env": "development",
        }
    )
    return main(settings=debug_settings)


if __name__ == "__main__":
    raise SystemExit(main())
