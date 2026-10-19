"""Async PostgreSQL connection pool lifecycle."""

import asyncpg

from config.settings import Settings, get_settings


class Database:
    """Own the service's async PostgreSQL pool."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("database connection pool has not been opened")
        return self._pool

    async def open(self) -> None:
        if self._pool is not None:
            return
        database_url = self._settings.database_url
        if database_url is None:
            raise RuntimeError("FREIGHT_DATABASE_URL must be configured")
        self._pool = await asyncpg.create_pool(
            dsn=database_url.get_secret_value(),
            min_size=1,
            max_size=10,
            command_timeout=30,
        )

    async def close(self) -> None:
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None
