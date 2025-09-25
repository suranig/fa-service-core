"""Migration management utilities."""

import asyncio
import logging
import os

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

logger = logging.getLogger(__name__)


def get_alembic_config(database_url: str | None = None) -> Config:
    """Get Alembic configuration.

    Args:
        database_url: Optional database URL override

    Returns:
        Alembic Config object
    """
    # Get the directory containing alembic.ini
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    alembic_cfg = Config(os.path.join(script_dir, "alembic.ini"))

    if database_url:
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

    return alembic_cfg


async def create_database_if_not_exists(database_url: str) -> None:
    """Create database if it doesn't exist.

    Args:
        database_url: Database URL
    """
    # Extract database name from URL
    from urllib.parse import urlparse

    parsed = urlparse(database_url)

    # Create connection to 'postgres' database to create target database
    admin_url = database_url.replace(f"/{parsed.path[1:]}", "/postgres")

    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")

    try:
        async with engine.connect() as conn:
            # Check if database exists
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :dbname"),
                {"dbname": parsed.path[1:]},
            )

            if not result.fetchone():
                # Create database
                await conn.execute(text(f'CREATE DATABASE "{parsed.path[1:]}"'))
                logger.info(f"Created database: {parsed.path[1:]}")
            else:
                logger.info(f"Database already exists: {parsed.path[1:]}")
    finally:
        await engine.dispose()


async def setup_extensions(database_url: str) -> None:
    """Setup required PostgreSQL extensions.

    Args:
        database_url: Database URL
    """
    engine = create_async_engine(database_url)

    try:
        async with engine.connect() as conn:
            # Enable required extensions
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "citext"'))
            await conn.commit()
            logger.info("PostgreSQL extensions enabled")
    finally:
        await engine.dispose()


async def apply_rls_policies(database_url: str) -> None:
    """Apply Row Level Security policies.

    Args:
        database_url: Database URL
    """
    from .models import RLS_POLICIES

    engine = create_async_engine(database_url)

    try:
        async with engine.connect() as conn:
            # Split policies and execute them
            policies = [p.strip() for p in RLS_POLICIES.split(";") if p.strip()]

            for policy in policies:
                try:
                    await conn.execute(text(policy))
                    logger.debug(f"Applied policy: {policy[:50]}...")
                except Exception as e:
                    # Some policies might already exist, that's ok
                    logger.debug(f"Policy application skipped: {e}")

            await conn.commit()
            logger.info("RLS policies applied")
    finally:
        await engine.dispose()


def create_migration(message: str, database_url: str | None = None) -> None:
    """Create a new migration.

    Args:
        message: Migration message
        database_url: Optional database URL
    """
    alembic_cfg = get_alembic_config(database_url)
    command.revision(alembic_cfg, autogenerate=True, message=message)
    logger.info(f"Created migration: {message}")


def upgrade_database(database_url: str | None = None, revision: str = "head") -> None:
    """Upgrade database to specified revision.

    Args:
        database_url: Optional database URL
        revision: Target revision (default: head)
    """
    alembic_cfg = get_alembic_config(database_url)
    command.upgrade(alembic_cfg, revision)
    logger.info(f"Upgraded database to revision: {revision}")


def downgrade_database(database_url: str | None = None, revision: str = "-1") -> None:
    """Downgrade database to specified revision.

    Args:
        database_url: Optional database URL
        revision: Target revision (default: -1)
    """
    alembic_cfg = get_alembic_config(database_url)
    command.downgrade(alembic_cfg, revision)
    logger.info(f"Downgraded database to revision: {revision}")


def show_current_revision(database_url: str | None = None) -> None:
    """Show current database revision.

    Args:
        database_url: Optional database URL
    """
    alembic_cfg = get_alembic_config(database_url)
    command.current(alembic_cfg)


def show_migration_history(database_url: str | None = None) -> None:
    """Show migration history.

    Args:
        database_url: Optional database URL
    """
    alembic_cfg = get_alembic_config(database_url)
    command.history(alembic_cfg)


async def reset_database(database_url: str) -> None:
    """Reset database (drop all tables and recreate).

    Args:
        database_url: Database URL
    """
    from .models import Base

    engine = create_async_engine(database_url)

    try:
        async with engine.begin() as conn:
            # Drop all tables
            await conn.run_sync(Base.metadata.drop_all)
            logger.info("Dropped all tables")

            # Create all tables
            await conn.run_sync(Base.metadata.create_all)
            logger.info("Created all tables")
    finally:
        await engine.dispose()


async def init_database_full(
    write_url: str,
    read_url: str | None = None,
    reset: bool = False,
) -> None:
    """Initialize database with full setup.

    Args:
        write_url: Write database URL
        read_url: Read database URL (optional)
        reset: Whether to reset database first
    """
    # Create databases if they don't exist
    await create_database_if_not_exists(write_url)
    if read_url and read_url != write_url:
        await create_database_if_not_exists(read_url)

    # Setup extensions
    await setup_extensions(write_url)
    if read_url and read_url != write_url:
        await setup_extensions(read_url)

    if reset:
        await reset_database(write_url)
        if read_url and read_url != write_url:
            await reset_database(read_url)

    # Run migrations
    upgrade_database(write_url)
    if read_url and read_url != write_url:
        upgrade_database(read_url)

    # Apply RLS policies
    await apply_rls_policies(write_url)
    if read_url and read_url != write_url:
        await apply_rls_policies(read_url)

    logger.info("Database initialization completed")


# CLI-like functions for use in scripts
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m core.migrations <command> [args...]")
        print("Commands:")
        print("  create <message>     - Create new migration")
        print("  upgrade [revision]   - Upgrade to revision (default: head)")
        print("  downgrade [revision] - Downgrade to revision (default: -1)")
        print("  current              - Show current revision")
        print("  history              - Show migration history")
        print("  reset                - Reset database (development only)")
        print("  init                 - Full database initialization")
        sys.exit(1)

    command = sys.argv[1]

    # Get database URL from environment
    database_url = os.getenv("DATABASE_WRITE_URL")
    if not database_url:
        print("DATABASE_WRITE_URL environment variable is required")
        sys.exit(1)

    if command == "create":
        if len(sys.argv) < 3:
            print("Migration message is required")
            sys.exit(1)
        create_migration(sys.argv[2], database_url)

    elif command == "upgrade":
        revision = sys.argv[2] if len(sys.argv) > 2 else "head"
        upgrade_database(database_url, revision)

    elif command == "downgrade":
        revision = sys.argv[2] if len(sys.argv) > 2 else "-1"
        downgrade_database(database_url, revision)

    elif command == "current":
        show_current_revision(database_url)

    elif command == "history":
        show_migration_history(database_url)

    elif command == "reset":
        asyncio.run(reset_database(database_url))

    elif command == "init":
        read_url = os.getenv("DATABASE_READ_URL")
        asyncio.run(init_database_full(database_url, read_url))

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
