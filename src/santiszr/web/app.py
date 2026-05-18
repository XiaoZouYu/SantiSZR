from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from santiszr.app import AppContext, bootstrap
from santiszr.config.settings import AppSettings
from santiszr.web.api import create_router
from santiszr.web.task_manager import WebTaskManager


def create_app(settings: AppSettings | None = None, context: AppContext | None = None) -> FastAPI:
    app_context = context or bootstrap(settings=settings)
    task_manager = WebTaskManager(app_context)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        del app
        try:
            yield
        finally:
            task_manager.shutdown()

    app = FastAPI(title="SantiSZR Web API", version="0.1.0", lifespan=lifespan)
    app.state.santiszr_context = app_context
    app.state.santiszr_task_manager = task_manager

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=(
            r"https?://("
            r"localhost|127\.0\.0\.1|"
            r"10\.\d+\.\d+\.\d+|"
            r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|"
            r"192\.168\.\d+\.\d+"
            r")(:\d+)?"
        ),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(create_router())

    return app
