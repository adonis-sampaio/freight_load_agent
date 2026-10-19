"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agent.event_router import router as event_router
from app.memory.database import Database
from config.logging import configure_logging
from config.settings import get_settings


def create_app(database: Database | None = None) -> FastAPI:
    service_database = database or Database()
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        await service_database.open()
        try:
            yield
        finally:
            await service_database.close()

    application = FastAPI(
        title=settings.service_name,
        lifespan=lifespan,
    )
    application.state.database = service_database
    application.include_router(event_router)
    return application


app = create_app()
