"""Version management for resources."""

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import now

logger = logging.getLogger(__name__)


class VersionError(Exception):
    """Base exception for version operations."""

    pass


class ResourceVersion:
    """Represents a version of a resource."""

    def __init__(
        self,
        page_id: UUID,
        site_id: UUID,
        version: int,
        data: dict[str, Any],
        created_at: datetime | None = None,
        created_by: UUID | None = None,
    ) -> None:
        self.page_id = page_id
        self.site_id = site_id
        self.version = version
        self.data = data
        self.created_at = created_at or now()
        self.created_by = created_by

    def to_dict(self) -> dict[str, Any]:
        """Convert version to dictionary."""
        return {
            "page_id": str(self.page_id),
            "site_id": str(self.site_id),
            "version": self.version,
            "data": self.data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": str(self.created_by) if self.created_by else None,
        }

    def __repr__(self) -> str:
        return f"ResourceVersion(page_id={self.page_id}, version={self.version})"


async def record_version(
    session: AsyncSession,
    table: str,
    id_field: str,
    resource_id: UUID,
    site_id: UUID,
    version: int,
    data: dict[str, Any],
    created_by: UUID | None = None,
) -> ResourceVersion:
    """Record a new version of a resource.

    Args:
        session: Database session
        table: Table name (e.g., "page_versions")
        id_field: Name of the ID field (e.g., "page_id")
        resource_id: UUID of the resource
        site_id: Site UUID
        version: Version number
        data: Resource data to store
        created_by: User who created this version

    Returns:
        Created ResourceVersion

    Raises:
        VersionError: If version cannot be recorded
    """
    try:
        resource_version = ResourceVersion(
            page_id=resource_id,
            site_id=site_id,
            version=version,
            data=data,
            created_by=created_by,
        )

        # Dynamic query based on table and id_field
        query = text(
            f"""
            INSERT INTO {table} ({id_field}, site_id, version, data, created_at, created_by)
            VALUES (:resource_id, :site_id, :version, :data, :created_at, :created_by)
        """
        )

        await session.execute(
            query,
            {
                "resource_id": resource_id,
                "site_id": site_id,
                "version": version,
                "data": json.dumps(data),
                "created_at": resource_version.created_at,
                "created_by": created_by,
            },
        )

        logger.info(
            "Version recorded",
            extra={
                "table": table,
                "resource_id": str(resource_id),
                "version": version,
                "site_id": str(site_id),
                "created_by": str(created_by) if created_by else None,
            },
        )

        return resource_version

    except Exception as e:
        logger.error(
            "Failed to record version",
            extra={
                "table": table,
                "resource_id": str(resource_id),
                "version": version,
                "error": str(e),
            },
        )
        raise VersionError(f"Cannot record version: {e}") from e


async def get_version(
    session: AsyncSession,
    table: str,
    id_field: str,
    resource_id: UUID,
    version: int,
) -> ResourceVersion | None:
    """Get a specific version of a resource.

    Args:
        session: Database session
        table: Table name
        id_field: Name of the ID field
        resource_id: UUID of the resource
        version: Version number to retrieve

    Returns:
        ResourceVersion if found, None otherwise
    """
    try:
        query = text(
            f"""
            SELECT {id_field}, site_id, version, data, created_at, created_by
            FROM {table}
            WHERE {id_field} = :resource_id AND version = :version
        """
        )

        result = await session.execute(
            query,
            {
                "resource_id": resource_id,
                "version": version,
            },
        )

        row = result.fetchone()
        if row:
            return ResourceVersion(
                page_id=UUID(row[0]),
                site_id=UUID(row[1]),
                version=row[2],
                data=json.loads(row[3]) if row[3] else {},
                created_at=row[4],
                created_by=UUID(row[5]) if row[5] else None,
            )

        return None

    except Exception as e:
        logger.error(
            "Failed to get version",
            extra={
                "table": table,
                "resource_id": str(resource_id),
                "version": version,
                "error": str(e),
            },
        )
        raise VersionError(f"Cannot get version: {e}") from e


async def list_versions(
    session: AsyncSession,
    table: str,
    id_field: str,
    resource_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[ResourceVersion]:
    """List versions of a resource.

    Args:
        session: Database session
        table: Table name
        id_field: Name of the ID field
        resource_id: UUID of the resource
        limit: Maximum number of versions to return
        offset: Number of versions to skip

    Returns:
        List of ResourceVersion objects ordered by version (newest first)
    """
    try:
        query = text(
            f"""
            SELECT {id_field}, site_id, version, data, created_at, created_by
            FROM {table}
            WHERE {id_field} = :resource_id
            ORDER BY version DESC
            LIMIT :limit OFFSET :offset
        """
        )

        result = await session.execute(
            query,
            {
                "resource_id": resource_id,
                "limit": limit,
                "offset": offset,
            },
        )

        versions = []
        for row in result.fetchall():
            version = ResourceVersion(
                page_id=UUID(row[0]),
                site_id=UUID(row[1]),
                version=row[2],
                data=json.loads(row[3]) if row[3] else {},
                created_at=row[4],
                created_by=UUID(row[5]) if row[5] else None,
            )
            versions.append(version)

        logger.debug(
            "Listed versions",
            extra={
                "table": table,
                "resource_id": str(resource_id),
                "count": len(versions),
                "limit": limit,
                "offset": offset,
            },
        )

        return versions

    except Exception as e:
        logger.error(
            "Failed to list versions",
            extra={"table": table, "resource_id": str(resource_id), "error": str(e)},
        )
        raise VersionError(f"Cannot list versions: {e}") from e


async def get_latest_version_number(
    session: AsyncSession,
    table: str,
    id_field: str,
    resource_id: UUID,
) -> int:
    """Get the latest version number for a resource.

    Args:
        session: Database session
        table: Table name
        id_field: Name of the ID field
        resource_id: UUID of the resource

    Returns:
        Latest version number, or 0 if no versions exist
    """
    try:
        query = text(
            f"""
            SELECT COALESCE(MAX(version), 0)
            FROM {table}
            WHERE {id_field} = :resource_id
        """
        )

        result = await session.execute(query, {"resource_id": resource_id})
        version_number = result.scalar() or 0

        return version_number

    except Exception as e:
        logger.error(
            "Failed to get latest version number",
            extra={"table": table, "resource_id": str(resource_id), "error": str(e)},
        )
        raise VersionError(f"Cannot get latest version number: {e}") from e


async def delete_old_versions(
    session: AsyncSession,
    table: str,
    id_field: str,
    resource_id: UUID,
    keep_latest: int = 10,
) -> int:
    """Delete old versions, keeping only the latest N versions.

    Args:
        session: Database session
        table: Table name
        id_field: Name of the ID field
        resource_id: UUID of the resource
        keep_latest: Number of latest versions to keep

    Returns:
        Number of versions deleted
    """
    try:
        # Get versions to delete (all except the latest N)
        query = text(
            f"""
            DELETE FROM {table}
            WHERE {id_field} = :resource_id
                AND version NOT IN (
                    SELECT version
                    FROM (
                        SELECT version
                        FROM {table}
                        WHERE {id_field} = :resource_id
                        ORDER BY version DESC
                        LIMIT :keep_latest
                    ) latest_versions
                )
        """
        )

        result = await session.execute(
            query,
            {
                "resource_id": resource_id,
                "keep_latest": keep_latest,
            },
        )

        deleted_count = result.rowcount

        logger.info(
            "Deleted old versions",
            extra={
                "table": table,
                "resource_id": str(resource_id),
                "deleted_count": deleted_count,
                "kept_latest": keep_latest,
            },
        )

        return deleted_count

    except Exception as e:
        logger.error(
            "Failed to delete old versions",
            extra={"table": table, "resource_id": str(resource_id), "error": str(e)},
        )
        raise VersionError(f"Cannot delete old versions: {e}") from e


class VersionManager:
    """High-level version management operations."""

    def __init__(self, table: str = "page_versions", id_field: str = "page_id") -> None:
        """Initialize version manager.

        Args:
            table: Default table name for versions
            id_field: Default ID field name
        """
        self.table = table
        self.id_field = id_field

    async def create_version(
        self,
        session: AsyncSession,
        resource_id: UUID,
        site_id: UUID,
        data: dict[str, Any],
        created_by: UUID | None = None,
    ) -> ResourceVersion:
        """Create a new version with auto-incremented version number.

        Args:
            session: Database session
            resource_id: UUID of the resource
            site_id: Site UUID
            data: Resource data
            created_by: User who created this version

        Returns:
            Created ResourceVersion
        """
        # Get next version number
        latest_version = await get_latest_version_number(
            session, self.table, self.id_field, resource_id
        )
        next_version = latest_version + 1

        # Record the version
        return await record_version(
            session=session,
            table=self.table,
            id_field=self.id_field,
            resource_id=resource_id,
            site_id=site_id,
            version=next_version,
            data=data,
            created_by=created_by,
        )

    async def get_version(
        self,
        session: AsyncSession,
        resource_id: UUID,
        version: int,
    ) -> ResourceVersion | None:
        """Get a specific version."""
        return await get_version(session, self.table, self.id_field, resource_id, version)

    async def get_latest_version(
        self,
        session: AsyncSession,
        resource_id: UUID,
    ) -> ResourceVersion | None:
        """Get the latest version of a resource."""
        latest_version_num = await get_latest_version_number(
            session, self.table, self.id_field, resource_id
        )

        if latest_version_num > 0:
            return await self.get_version(session, resource_id, latest_version_num)

        return None

    async def list_versions(
        self,
        session: AsyncSession,
        resource_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ResourceVersion]:
        """List versions of a resource."""
        return await list_versions(session, self.table, self.id_field, resource_id, limit, offset)

    async def cleanup_old_versions(
        self,
        session: AsyncSession,
        resource_id: UUID,
        keep_latest: int = 10,
    ) -> int:
        """Clean up old versions."""
        return await delete_old_versions(
            session, self.table, self.id_field, resource_id, keep_latest
        )


# Pre-configured managers removed - create them in specific microservices:
# page_version_manager = VersionManager("page_versions", "page_id")
# site_version_manager = VersionManager("site_versions", "site_id")
