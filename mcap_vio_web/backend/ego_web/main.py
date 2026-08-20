from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import router
from .db import Database
from .runner import Runner
from .scheduler import Scheduler, TaskRunner
from .settings import Settings
from .task_service import TaskService


def create_app(
    *,
    settings: Settings | None = None,
    runner: TaskRunner | None = None,
) -> FastAPI:
    app_settings = settings or Settings()
    database = Database(app_settings.database_path)
    task_service = TaskService(database, app_settings)
    task_runner = runner or Runner(app_settings)
    scheduler = Scheduler(
        database,
        task_runner,
        max_concurrency=app_settings.max_concurrency,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        scheduler.recover_unfinished()
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(title="EGO Device Data Annotation Platform", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.database = database
    app.state.task_service = task_service
    app.state.scheduler = scheduler
    app.include_router(router)
    return app


app = create_app()
