"""Unit of Work pattern with site context management."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import SessionRead, SessionWrite, get_db_manager

logger = logging.getLogger(__name__)


class UnitOfWorkError(Exception):
    """Base exception for Unit of Work operations."""

    pass


class SiteContextError(UnitOfWorkError):
    """Exception raised when site context cannot be set."""

    pass


@asynccontextmanager
async def write_uow(site_id: UUID) -> AsyncGenerator[SessionWrite]:
    """Unit of Work context manager for write operations.

    Sets up a write transaction with site context:
    1. Begins transaction
    2. Sets LOCAL app.current_site = site_id
    3. Yields session for operations
    4. Commits on success or rolls back on exception

    Args:
        site_id: The site UUID to set as context

    Yields:
        AsyncSession configured for write operations

    Raises:
        SiteContextError: If site context cannot be set
        UnitOfWorkError: If transaction operations fail
    """
    db_manager = get_db_manager()

    async with db_manager.write_session_factory() as session:
        try:
            # Begin transaction
            await session.begin()

            # Set site context
            await _set_site_context(session, site_id)

            logger.debug(
                "Write UoW started", extra={"site_id": str(site_id), "session_id": id(session)}
            )

            yield session

            # Commit transaction
            await session.commit()

            logger.debug(
                "Write UoW committed", extra={"site_id": str(site_id), "session_id": id(session)}
            )

        except Exception as e:
            # Rollback on any exception
            await session.rollback()

            logger.error(
                "Write UoW rolled back",
                extra={"site_id": str(site_id), "session_id": id(session), "error": str(e)},
            )
            raise UnitOfWorkError(f"Write transaction failed: {e}") from e


@asynccontextmanager
async def read_uow(site_id: UUID) -> AsyncGenerator[SessionRead]:
    """Unit of Work context manager for read operations.

    Sets up a read-only transaction with site context:
    1. Begins READ ONLY transaction
    2. Sets LOCAL app.current_site = site_id
    3. Yields session for operations
    4. Automatically commits (read-only)

    Args:
        site_id: The site UUID to set as context

    Yields:
        AsyncSession configured for read operations

    Raises:
        SiteContextError: If site context cannot be set
        UnitOfWorkError: If transaction operations fail
    """
    db_manager = get_db_manager()

    async with db_manager.read_session_factory() as session:
        try:
            # Begin READ ONLY transaction
            await session.begin()
            await session.execute(text("SET TRANSACTION READ ONLY"))

            # Set site context
            await _set_site_context(session, site_id)

            logger.debug(
                "Read UoW started", extra={"site_id": str(site_id), "session_id": id(session)}
            )

            yield session

            # Read-only transaction - no need to commit
            logger.debug(
                "Read UoW completed", extra={"site_id": str(site_id), "session_id": id(session)}
            )

        except Exception as e:
            # Rollback on any exception
            await session.rollback()

            logger.error(
                "Read UoW rolled back",
                extra={"site_id": str(site_id), "session_id": id(session), "error": str(e)},
            )
            raise UnitOfWorkError(f"Read transaction failed: {e}") from e


async def _set_site_context(session: AsyncSession, site_id: UUID) -> None:
    """Set the site context for the current transaction.

    Args:
        session: Database session
        site_id: Site UUID to set as context

    Raises:
        SiteContextError: If context cannot be set
    """
    try:
        # Set LOCAL variable that will be available for the transaction duration
        await session.execute(
            text("SET LOCAL app.current_site = :site_id"), {"site_id": str(site_id)}
        )

        # Verify the setting was applied correctly
        result = await session.execute(text("SELECT current_setting('app.current_site', true)"))
        current_site = result.scalar()

        if current_site != str(site_id):
            raise SiteContextError(
                f"Site context verification failed. Expected: {site_id}, Got: {current_site}"
            )

        logger.debug(
            "Site context set successfully",
            extra={"site_id": str(site_id), "verified": current_site},
        )

    except Exception as e:
        logger.error("Failed to set site context", extra={"site_id": str(site_id), "error": str(e)})
        raise SiteContextError(f"Cannot set site context: {e}") from e


async def get_current_site_context(session: AsyncSession) -> UUID | None:
    """Get the current site context from the session.

    Args:
        session: Database session

    Returns:
        Current site UUID if set, None otherwise
    """
    try:
        result = await session.execute(text("SELECT current_setting('app.current_site', true)"))
        site_id_str = result.scalar()

        if site_id_str and site_id_str != "":
            return UUID(site_id_str)
        return None

    except Exception as e:
        logger.debug("Cannot get site context", extra={"error": str(e)})
        return None


@asynccontextmanager
async def isolated_uow(
    site_id: UUID, read_only: bool = False
) -> AsyncGenerator[AsyncSession]:
    """Generic isolated unit of work that can be used for both read and write.

    This is useful when you need more control over the transaction lifecycle
    or when you want to choose read/write mode dynamically.

    Args:
        site_id: Site UUID to set as context
        read_only: Whether to use read-only transaction

    Yields:
        AsyncSession configured for the specified operation mode

    Raises:
        UnitOfWorkError: If transaction operations fail
    """
    if read_only:
        async with read_uow(site_id) as session:
            yield session
    else:
        async with write_uow(site_id) as session:
            yield session


class UnitOfWorkManager:
    """Manager class for unit of work operations with additional utilities."""

    def __init__(self) -> None:
        self._active_transactions: dict[int, UUID] = {}

    @asynccontextmanager
    async def write_transaction(self, site_id: UUID) -> AsyncGenerator[SessionWrite]:
        """Managed write transaction with tracking."""
        async with write_uow(site_id) as session:
            session_id = id(session)
            self._active_transactions[session_id] = site_id
            try:
                yield session
            finally:
                self._active_transactions.pop(session_id, None)

    @asynccontextmanager
    async def read_transaction(self, site_id: UUID) -> AsyncGenerator[SessionRead]:
        """Managed read transaction with tracking."""
        async with read_uow(site_id) as session:
            session_id = id(session)
            self._active_transactions[session_id] = site_id
            try:
                yield session
            finally:
                self._active_transactions.pop(session_id, None)

    def get_active_transactions(self) -> dict[int, UUID]:
        """Get currently active transactions."""
        return self._active_transactions.copy()

    def get_transaction_count(self) -> int:
        """Get count of active transactions."""
        return len(self._active_transactions)


# Global UoW manager instance
uow_manager: UnitOfWorkManager | None = None


def init_uow_manager() -> UnitOfWorkManager:
    """Initialize global UoW manager."""
    global uow_manager
    uow_manager = UnitOfWorkManager()
    return uow_manager


def get_uow_manager() -> UnitOfWorkManager:
    """Get global UoW manager instance."""
    if uow_manager is None:
        raise RuntimeError("UoW manager not initialized. Call init_uow_manager() first.")
    return uow_manager
