"""Database connection management with separate write/read pools."""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages separate write and read database connections."""

    def __init__(
        self,
        write_url: str,
        read_url: str | None = None,
        pool_size: int = 20,
        max_overflow: int = 0,
        statement_timeout: int = 3000,
        **engine_kwargs: Any,
    ) -> None:
        """Initialize database manager with write and read engines.

        Args:
            write_url: Primary database connection URL
            read_url: Read replica connection URL (defaults to write_url if not provided)
            pool_size: Connection pool size
            max_overflow: Max connections beyond pool_size
            statement_timeout: Statement timeout in milliseconds
            **engine_kwargs: Additional engine configuration
        """
        self.write_url = write_url
        self.read_url = read_url or write_url
        self.statement_timeout = statement_timeout

        # Common engine configuration
        connect_args = {
            "prepared_statement_cache_size": 0,  # Disable prepared statement cache
            "statement_timeout": statement_timeout,
        }
        connect_args.update(engine_kwargs.get("connect_args", {}))

        engine_config = {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "echo": False,
            "connect_args": connect_args,
        }
        engine_config.update(engine_kwargs)

        # Create engines
        self._write_engine: AsyncEngine | None = None
        self._read_engine: AsyncEngine | None = None
        self._write_session_factory: async_sessionmaker[AsyncSession] | None = None
        self._read_session_factory: async_sessionmaker[AsyncSession] | None = None

        # Configuration for lazy initialization
        self._engine_config = engine_config

    @property
    def write_engine(self) -> AsyncEngine:
        """Get write engine (lazy initialization)."""
        if self._write_engine is None:
            self._write_engine = create_async_engine(self.write_url, **self._engine_config)
            logger.info("Initialized write engine", extra={"url": self.write_url})
        return self._write_engine

    @property
    def read_engine(self) -> AsyncEngine:
        """Get read engine (lazy initialization)."""
        if self._read_engine is None:
            # For read engine, add READ ONLY isolation by default
            read_config = self._engine_config.copy()
            self._read_engine = create_async_engine(self.read_url, **read_config)
            logger.info("Initialized read engine", extra={"url": self.read_url})
        return self._read_engine

    @property
    def write_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Get write session factory."""
        if self._write_session_factory is None:
            self._write_session_factory = async_sessionmaker(
                bind=self.write_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        return self._write_session_factory

    @property
    def read_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Get read session factory."""
        if self._read_session_factory is None:
            self._read_session_factory = async_sessionmaker(
                bind=self.read_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        return self._read_session_factory

    async def close(self) -> None:
        """Close all database connections."""
        if self._write_engine is not None:
            await self._write_engine.dispose()
            logger.info("Closed write engine")

        if self._read_engine is not None:
            await self._read_engine.dispose()
            logger.info("Closed read engine")


# Global database manager instance
db_manager: DatabaseManager | None = None


def init_database(
    write_url: str,
    read_url: str | None = None,
    **kwargs: Any,
) -> DatabaseManager:
    """Initialize global database manager.

    Args:
        write_url: Primary database connection URL
        read_url: Read replica connection URL
        **kwargs: Additional configuration for DatabaseManager

    Returns:
        Initialized DatabaseManager instance
    """
    global db_manager
    db_manager = DatabaseManager(write_url, read_url, **kwargs)
    return db_manager


def get_db_manager() -> DatabaseManager:
    """Get global database manager instance.

    Raises:
        RuntimeError: If database manager is not initialized

    Returns:
        DatabaseManager instance
    """
    if db_manager is None:
        raise RuntimeError("Database manager not initialized. Call init_database() first.")
    return db_manager


# Session type aliases for convenience
SessionWrite = AsyncSession
SessionRead = AsyncSession


def now() -> datetime:
    """Get current UTC timestamp.

    Returns:
        Current datetime in UTC timezone
    """
    return datetime.now(UTC)


async def check_database_connection(engine: AsyncEngine) -> dict[str, Any]:
    """Check database connection and return basic info.

    Args:
        engine: Database engine to check

    Returns:
        Dictionary with connection information
    """
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT version(), current_database(), current_user"))
        version, database, user = result.fetchone()

        return {
            "version": version,
            "database": database,
            "user": user,
            "status": "connected",
        }


async def test_rls_configuration(session: AsyncSession, site_id: str) -> bool:
    """Test if RLS (Row Level Security) is properly configured.

    Args:
        session: Database session
        site_id: Site ID to test with

    Returns:
        True if RLS is working correctly
    """
    try:
        # Set the site context
        await session.execute(text("SET LOCAL app.current_site = :site_id"), {"site_id": site_id})

        # Test if setting is properly set
        result = await session.execute(text("SELECT current_setting('app.current_site', true)"))
        current_site = result.scalar()

        return current_site == site_id
    except Exception as e:
        logger.error("RLS configuration test failed", extra={"error": str(e), "site_id": site_id})
        return False
