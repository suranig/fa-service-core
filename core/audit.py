"""Audit logging with JSON Patch RFC6902 support."""

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import jsonpatch
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import now

logger = logging.getLogger(__name__)


class AuditError(Exception):
    """Base exception for audit operations."""

    pass


class AuditRecord:
    """Represents an audit log record."""

    def __init__(
        self,
        id: int | None = None,
        ts: datetime | None = None,
        site_id: UUID | None = None,
        user_id: UUID | None = None,
        resource: str = "",
        resource_id: UUID | None = None,
        event_type: str = "",
        version: int = 1,
        patch_json: list[dict[str, Any]] | None = None,
        snapshot: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.id = id
        self.ts = ts or now()
        self.site_id = site_id
        self.user_id = user_id
        self.resource = resource
        self.resource_id = resource_id
        self.event_type = event_type
        self.version = version
        self.patch_json = patch_json or []
        self.snapshot = snapshot or {}
        self.meta = meta or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert audit record to dictionary."""
        return {
            "id": self.id,
            "ts": self.ts.isoformat() if self.ts else None,
            "site_id": str(self.site_id) if self.site_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "resource": self.resource,
            "resource_id": str(self.resource_id) if self.resource_id else None,
            "event_type": self.event_type,
            "version": self.version,
            "patch_json": self.patch_json,
            "snapshot": self.snapshot,
            "meta": self.meta,
        }

    def __repr__(self) -> str:
        return (
            f"AuditRecord(resource={self.resource}, resource_id={self.resource_id}, "
            f"event_type={self.event_type}, version={self.version})"
        )


def json_patch(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate JSON Patch (RFC6902) between two objects.

    Args:
        before: Object state before changes
        after: Object state after changes

    Returns:
        List of JSON Patch operations
    """
    try:
        # Generate patch using jsonpatch library
        patch = jsonpatch.make_patch(before, after)
        return patch.patch
    except Exception as e:
        logger.error(
            "Failed to generate JSON patch",
            extra={
                "error": str(e),
                "before_keys": list(before.keys()),
                "after_keys": list(after.keys()),
            },
        )
        # Return empty patch on error
        return []


def apply_json_patch(obj: dict[str, Any], patch_ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply JSON Patch operations to an object.

    Args:
        obj: Object to apply patch to
        patch_ops: List of patch operations

    Returns:
        Object with patch applied

    Raises:
        AuditError: If patch cannot be applied
    """
    try:
        patch = jsonpatch.JsonPatch(patch_ops)
        result = patch.apply(obj)
        return result
    except Exception as e:
        logger.error("Failed to apply JSON patch", extra={"error": str(e), "patch_ops": patch_ops})
        raise AuditError(f"Cannot apply JSON patch: {e}") from e


async def record_audit(
    session: AsyncSession,
    site_id: UUID | None,
    user_id: UUID | None,
    resource: str,
    resource_id: UUID,
    event_type: str,
    version: int,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> AuditRecord:
    """Record an audit log entry.

    Args:
        session: Database session
        site_id: Site UUID (can be None for global events)
        user_id: User UUID who performed the action
        resource: Resource type (e.g., "pages", "sites")
        resource_id: UUID of the resource that was modified
        event_type: Type of event (e.g., "created", "updated", "deleted")
        version: Resource version after the change
        before: Object state before changes
        after: Object state after changes
        meta: Additional metadata

    Returns:
        Created AuditRecord

    Raises:
        AuditError: If audit record cannot be created
    """
    try:
        # Generate JSON patch if both before and after are provided
        patch_json = []
        if before is not None and after is not None:
            patch_json = json_patch(before, after)

        # Use 'after' as snapshot, or 'before' if only that is provided
        snapshot = after or before or {}

        # Create audit record
        audit_record = AuditRecord(
            site_id=site_id,
            user_id=user_id,
            resource=resource,
            resource_id=resource_id,
            event_type=event_type,
            version=version,
            patch_json=patch_json,
            snapshot=snapshot,
            meta=meta or {},
        )

        # Store in database
        query = text(
            """
            INSERT INTO audit_log (
                ts, site_id, user_id, resource, resource_id, 
                event_type, version, patch_json, snapshot, meta
            )
            VALUES (
                :ts, :site_id, :user_id, :resource, :resource_id,
                :event_type, :version, :patch_json, :snapshot, :meta
            )
            RETURNING id
        """
        )

        result = await session.execute(
            query,
            {
                "ts": audit_record.ts,
                "site_id": audit_record.site_id,
                "user_id": audit_record.user_id,
                "resource": audit_record.resource,
                "resource_id": audit_record.resource_id,
                "event_type": audit_record.event_type,
                "version": audit_record.version,
                "patch_json": json.dumps(audit_record.patch_json),
                "snapshot": json.dumps(audit_record.snapshot),
                "meta": json.dumps(audit_record.meta),
            },
        )

        audit_id = result.scalar()
        audit_record.id = audit_id

        logger.info(
            "Audit record created",
            extra={
                "audit_id": audit_id,
                "resource": resource,
                "resource_id": str(resource_id),
                "event_type": event_type,
                "version": version,
                "site_id": str(site_id) if site_id else None,
                "patch_ops_count": len(patch_json),
            },
        )

        return audit_record

    except Exception as e:
        logger.error(
            "Failed to record audit",
            extra={
                "resource": resource,
                "resource_id": str(resource_id),
                "event_type": event_type,
                "error": str(e),
            },
        )
        raise AuditError(f"Cannot record audit: {e}") from e


async def list_history(
    session: AsyncSession,
    resource: str,
    resource_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[AuditRecord]:
    """List audit history for a resource.

    Args:
        session: Database session
        resource: Resource type
        resource_id: Resource UUID
        limit: Maximum number of records to return
        offset: Number of records to skip

    Returns:
        List of AuditRecord objects ordered by timestamp (newest first)
    """
    try:
        query = text(
            """
            SELECT 
                id, ts, site_id, user_id, resource, resource_id,
                event_type, version, patch_json, snapshot, meta
            FROM audit_log
            WHERE resource = :resource AND resource_id = :resource_id
            ORDER BY ts DESC, id DESC
            LIMIT :limit OFFSET :offset
        """
        )

        result = await session.execute(
            query,
            {
                "resource": resource,
                "resource_id": resource_id,
                "limit": limit,
                "offset": offset,
            },
        )

        records = []
        for row in result.fetchall():
            audit_record = AuditRecord(
                id=row.id,
                ts=row.ts,
                site_id=UUID(row.site_id) if row.site_id else None,
                user_id=UUID(row.user_id) if row.user_id else None,
                resource=row.resource,
                resource_id=UUID(row.resource_id) if row.resource_id else None,
                event_type=row.event_type,
                version=row.version,
                patch_json=json.loads(row.patch_json) if row.patch_json else [],
                snapshot=json.loads(row.snapshot) if row.snapshot else {},
                meta=json.loads(row.meta) if row.meta else {},
            )
            records.append(audit_record)

        logger.debug(
            "Retrieved audit history",
            extra={
                "resource": resource,
                "resource_id": str(resource_id),
                "count": len(records),
                "limit": limit,
                "offset": offset,
            },
        )

        return records

    except Exception as e:
        logger.error(
            "Failed to list audit history",
            extra={"resource": resource, "resource_id": str(resource_id), "error": str(e)},
        )
        raise AuditError(f"Cannot list audit history: {e}") from e


async def get_version_snapshot(
    session: AsyncSession,
    resource: str,
    resource_id: UUID,
    version: int,
) -> dict[str, Any] | None:
    """Get snapshot of a resource at a specific version.

    Args:
        session: Database session
        resource: Resource type
        resource_id: Resource UUID
        version: Version number

    Returns:
        Resource snapshot at the specified version, or None if not found
    """
    try:
        query = text(
            """
            SELECT snapshot
            FROM audit_log
            WHERE resource = :resource 
                AND resource_id = :resource_id 
                AND version = :version
            ORDER BY ts DESC, id DESC
            LIMIT 1
        """
        )

        result = await session.execute(
            query,
            {
                "resource": resource,
                "resource_id": resource_id,
                "version": version,
            },
        )

        row = result.fetchone()
        if row and row.snapshot:
            return json.loads(row.snapshot)

        return None

    except Exception as e:
        logger.error(
            "Failed to get version snapshot",
            extra={
                "resource": resource,
                "resource_id": str(resource_id),
                "version": version,
                "error": str(e),
            },
        )
        raise AuditError(f"Cannot get version snapshot: {e}") from e


async def reconstruct_object_at_version(
    session: AsyncSession,
    resource: str,
    resource_id: UUID,
    target_version: int,
) -> dict[str, Any] | None:
    """Reconstruct an object state at a specific version by applying patches.

    This method starts from the earliest version and applies all patches
    sequentially up to the target version.

    Args:
        session: Database session
        resource: Resource type
        resource_id: Resource UUID
        target_version: Target version to reconstruct

    Returns:
        Reconstructed object state, or None if cannot be reconstructed
    """
    try:
        # Get all audit records up to target version, ordered by version
        query = text(
            """
            SELECT 
                version, event_type, patch_json, snapshot
            FROM audit_log
            WHERE resource = :resource 
                AND resource_id = :resource_id 
                AND version <= :target_version
            ORDER BY version ASC, ts ASC, id ASC
        """
        )

        result = await session.execute(
            query,
            {
                "resource": resource,
                "resource_id": resource_id,
                "target_version": target_version,
            },
        )

        records = result.fetchall()
        if not records:
            return None

        # Start with the first snapshot (creation event)
        current_state = None
        for record in records:
            if record.event_type == "created" and record.snapshot:
                current_state = json.loads(record.snapshot)
                break

        if current_state is None:
            logger.warning(
                "No creation snapshot found for reconstruction",
                extra={"resource": resource, "resource_id": str(resource_id)},
            )
            return None

        # Apply patches sequentially
        for record in records:
            if record.version == 1:
                continue  # Skip creation record

            if record.patch_json:
                patch_ops = json.loads(record.patch_json)
                if patch_ops:
                    current_state = apply_json_patch(current_state, patch_ops)

        logger.debug(
            "Reconstructed object at version",
            extra={
                "resource": resource,
                "resource_id": str(resource_id),
                "target_version": target_version,
                "patches_applied": len(records) - 1,
            },
        )

        return current_state

    except Exception as e:
        logger.error(
            "Failed to reconstruct object at version",
            extra={
                "resource": resource,
                "resource_id": str(resource_id),
                "target_version": target_version,
                "error": str(e),
            },
        )
        raise AuditError(f"Cannot reconstruct object: {e}") from e


class AuditManager:
    """High-level audit operations manager."""

    @staticmethod
    async def record_create(
        session: AsyncSession,
        site_id: UUID | None,
        user_id: UUID | None,
        resource: str,
        resource_id: UUID,
        data: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Record creation of a resource."""
        return await record_audit(
            session=session,
            site_id=site_id,
            user_id=user_id,
            resource=resource,
            resource_id=resource_id,
            event_type="created",
            version=1,
            before=None,
            after=data,
            meta=meta,
        )

    @staticmethod
    async def record_update(
        session: AsyncSession,
        site_id: UUID | None,
        user_id: UUID | None,
        resource: str,
        resource_id: UUID,
        version: int,
        before: dict[str, Any],
        after: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Record update of a resource."""
        return await record_audit(
            session=session,
            site_id=site_id,
            user_id=user_id,
            resource=resource,
            resource_id=resource_id,
            event_type="updated",
            version=version,
            before=before,
            after=after,
            meta=meta,
        )

    @staticmethod
    async def record_delete(
        session: AsyncSession,
        site_id: UUID | None,
        user_id: UUID | None,
        resource: str,
        resource_id: UUID,
        version: int,
        data: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Record deletion of a resource."""
        return await record_audit(
            session=session,
            site_id=site_id,
            user_id=user_id,
            resource=resource,
            resource_id=resource_id,
            event_type="deleted",
            version=version,
            before=data,
            after=None,
            meta=meta,
        )

    @staticmethod
    async def record_custom_event(
        session: AsyncSession,
        site_id: UUID | None,
        user_id: UUID | None,
        resource: str,
        resource_id: UUID,
        event_type: str,
        version: int,
        data: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Record custom event for a resource."""
        return await record_audit(
            session=session,
            site_id=site_id,
            user_id=user_id,
            resource=resource,
            resource_id=resource_id,
            event_type=event_type,
            version=version,
            before=None,
            after=data,
            meta=meta,
        )
