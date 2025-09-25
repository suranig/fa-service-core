"""Outbox pattern implementation for reliable event publishing."""

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import now

logger = logging.getLogger(__name__)


class OutboxError(Exception):
    """Base exception for outbox operations."""

    pass


class OutboxEvent:
    """Represents an outbox event."""

    def __init__(
        self,
        id: int | None = None,
        ts: datetime | None = None,
        site_id: UUID | None = None,
        aggregate: str = "",
        aggregate_id: UUID | None = None,
        event_type: str = "",
        payload: dict[str, Any] | None = None,
        processed_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.ts = ts or now()
        self.site_id = site_id
        self.aggregate = aggregate
        self.aggregate_id = aggregate_id
        self.event_type = event_type
        self.payload = payload or {}
        self.processed_at = processed_at

    @property
    def is_processed(self) -> bool:
        """Check if event has been processed."""
        return self.processed_at is not None

    def to_dict(self) -> dict[str, Any]:
        """Convert outbox event to dictionary."""
        return {
            "id": self.id,
            "ts": self.ts.isoformat() if self.ts else None,
            "site_id": str(self.site_id) if self.site_id else None,
            "aggregate": self.aggregate,
            "aggregate_id": str(self.aggregate_id) if self.aggregate_id else None,
            "event_type": self.event_type,
            "payload": self.payload,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"OutboxEvent(id={self.id}, aggregate={self.aggregate}, "
            f"event_type={self.event_type}, processed={self.is_processed})"
        )


class OutboxManager:
    """Manages outbox events for reliable event publishing."""

    async def enqueue(
        self,
        session: AsyncSession,
        site_id: UUID,
        aggregate: str,
        aggregate_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> OutboxEvent:
        """Enqueue a new outbox event.

        Args:
            session: Database session
            site_id: Site UUID
            aggregate: Aggregate type (e.g., "pages", "sites")
            aggregate_id: UUID of the aggregate instance
            event_type: Event type (e.g., "pages.published", "sites.created")
            payload: Event payload data

        Returns:
            Created OutboxEvent

        Raises:
            OutboxError: If event cannot be enqueued
        """
        try:
            outbox_event = OutboxEvent(
                site_id=site_id,
                aggregate=aggregate,
                aggregate_id=aggregate_id,
                event_type=event_type,
                payload=payload,
            )

            query = text(
                """
                INSERT INTO outbox_events (
                    ts, site_id, aggregate, aggregate_id, type, payload
                )
                VALUES (
                    :ts, :site_id, :aggregate, :aggregate_id, :event_type, :payload
                )
                RETURNING id
            """
            )

            result = await session.execute(
                query,
                {
                    "ts": outbox_event.ts,
                    "site_id": outbox_event.site_id,
                    "aggregate": outbox_event.aggregate,
                    "aggregate_id": outbox_event.aggregate_id,
                    "event_type": outbox_event.event_type,
                    "payload": json.dumps(outbox_event.payload),
                },
            )

            event_id = result.scalar()
            outbox_event.id = event_id

            logger.info(
                "Outbox event enqueued",
                extra={
                    "event_id": event_id,
                    "aggregate": aggregate,
                    "aggregate_id": str(aggregate_id),
                    "event_type": event_type,
                    "site_id": str(site_id),
                },
            )

            return outbox_event

        except Exception as e:
            logger.error(
                "Failed to enqueue outbox event",
                extra={
                    "aggregate": aggregate,
                    "aggregate_id": str(aggregate_id),
                    "event_type": event_type,
                    "error": str(e),
                },
            )
            raise OutboxError(f"Cannot enqueue outbox event: {e}") from e

    async def fetch_batch(
        self,
        session: AsyncSession,
        batch_size: int = 100,
        site_id: UUID | None = None,
    ) -> list[OutboxEvent]:
        """Fetch a batch of unprocessed events for processing.

        Args:
            session: Database session
            batch_size: Maximum number of events to fetch
            site_id: Optional site filter

        Returns:
            List of unprocessed OutboxEvent objects
        """
        try:
            # Base query for unprocessed events
            where_clause = "processed_at IS NULL"
            params = {"batch_size": batch_size}

            if site_id:
                where_clause += " AND site_id = :site_id"
                params["site_id"] = site_id

            query = text(
                f"""
                SELECT 
                    id, ts, site_id, aggregate, aggregate_id, type, payload, processed_at
                FROM outbox_events
                WHERE {where_clause}
                ORDER BY ts ASC, id ASC
                LIMIT :batch_size
            """
            )

            result = await session.execute(query, params)

            events = []
            for row in result.fetchall():
                event = OutboxEvent(
                    id=row.id,
                    ts=row.ts,
                    site_id=UUID(row.site_id) if row.site_id else None,
                    aggregate=row.aggregate,
                    aggregate_id=UUID(row.aggregate_id) if row.aggregate_id else None,
                    event_type=row.type,
                    payload=json.loads(row.payload) if row.payload else {},
                    processed_at=row.processed_at,
                )
                events.append(event)

            logger.debug(
                "Fetched outbox events batch",
                extra={
                    "count": len(events),
                    "batch_size": batch_size,
                    "site_id": str(site_id) if site_id else None,
                },
            )

            return events

        except Exception as e:
            logger.error(
                "Failed to fetch outbox events", extra={"batch_size": batch_size, "error": str(e)}
            )
            raise OutboxError(f"Cannot fetch outbox events: {e}") from e

    async def mark_processed(
        self,
        session: AsyncSession,
        event_ids: list[int],
    ) -> int:
        """Mark events as processed.

        Args:
            session: Database session
            event_ids: List of event IDs to mark as processed

        Returns:
            Number of events marked as processed

        Raises:
            OutboxError: If events cannot be marked as processed
        """
        if not event_ids:
            return 0

        try:
            # Convert event_ids to comma-separated string for SQL IN clause
            ids_str = ",".join(str(id) for id in event_ids)

            query = text(
                f"""
                UPDATE outbox_events
                SET processed_at = NOW()
                WHERE id IN ({ids_str}) AND processed_at IS NULL
            """
            )

            result = await session.execute(query)
            processed_count = result.rowcount

            logger.info(
                "Marked outbox events as processed",
                extra={
                    "event_ids": event_ids,
                    "processed_count": processed_count,
                },
            )

            return processed_count

        except Exception as e:
            logger.error(
                "Failed to mark outbox events as processed",
                extra={"event_ids": event_ids, "error": str(e)},
            )
            raise OutboxError(f"Cannot mark events as processed: {e}") from e

    async def mark_single_processed(
        self,
        session: AsyncSession,
        event_id: int,
    ) -> bool:
        """Mark a single event as processed.

        Args:
            session: Database session
            event_id: Event ID to mark as processed

        Returns:
            True if event was marked as processed
        """
        result = await self.mark_processed(session, [event_id])
        return result > 0

    async def get_event(
        self,
        session: AsyncSession,
        event_id: int,
    ) -> OutboxEvent | None:
        """Get a specific outbox event by ID.

        Args:
            session: Database session
            event_id: Event ID

        Returns:
            OutboxEvent if found, None otherwise
        """
        try:
            query = text(
                """
                SELECT 
                    id, ts, site_id, aggregate, aggregate_id, type, payload, processed_at
                FROM outbox_events
                WHERE id = :event_id
            """
            )

            result = await session.execute(query, {"event_id": event_id})
            row = result.fetchone()

            if row:
                return OutboxEvent(
                    id=row.id,
                    ts=row.ts,
                    site_id=UUID(row.site_id) if row.site_id else None,
                    aggregate=row.aggregate,
                    aggregate_id=UUID(row.aggregate_id) if row.aggregate_id else None,
                    event_type=row.type,
                    payload=json.loads(row.payload) if row.payload else {},
                    processed_at=row.processed_at,
                )

            return None

        except Exception as e:
            logger.error(
                "Failed to get outbox event", extra={"event_id": event_id, "error": str(e)}
            )
            raise OutboxError(f"Cannot get outbox event: {e}") from e

    async def cleanup_processed_events(
        self,
        session: AsyncSession,
        older_than_hours: int = 24,
    ) -> int:
        """Clean up old processed events.

        Args:
            session: Database session
            older_than_hours: Remove events processed more than this many hours ago

        Returns:
            Number of events removed
        """
        try:
            query = text(
                """
                DELETE FROM outbox_events
                WHERE processed_at IS NOT NULL 
                    AND processed_at < NOW() - INTERVAL ':hours hours'
            """
            )

            result = await session.execute(query, {"hours": older_than_hours})
            deleted_count = result.rowcount

            logger.info(
                "Cleaned up processed outbox events",
                extra={
                    "deleted_count": deleted_count,
                    "older_than_hours": older_than_hours,
                },
            )

            return deleted_count

        except Exception as e:
            logger.error("Failed to cleanup processed outbox events", extra={"error": str(e)})
            raise OutboxError(f"Cannot cleanup processed events: {e}") from e

    async def count_pending_events(
        self,
        session: AsyncSession,
        site_id: UUID | None = None,
    ) -> int:
        """Count pending (unprocessed) events.

        Args:
            session: Database session
            site_id: Optional site filter

        Returns:
            Number of pending events
        """
        try:
            where_clause = "processed_at IS NULL"
            params = {}

            if site_id:
                where_clause += " AND site_id = :site_id"
                params["site_id"] = site_id

            query = text(
                f"""
                SELECT COUNT(*)
                FROM outbox_events
                WHERE {where_clause}
            """
            )

            result = await session.execute(query, params)
            count = result.scalar()

            return count or 0

        except Exception as e:
            logger.error("Failed to count pending outbox events", extra={"error": str(e)})
            return 0

    async def get_events_by_aggregate(
        self,
        session: AsyncSession,
        aggregate: str,
        aggregate_id: UUID,
        processed: bool | None = None,
        limit: int = 50,
    ) -> list[OutboxEvent]:
        """Get events for a specific aggregate.

        Args:
            session: Database session
            aggregate: Aggregate type
            aggregate_id: Aggregate ID
            processed: Filter by processed status (None for all)
            limit: Maximum number of events to return

        Returns:
            List of OutboxEvent objects
        """
        try:
            where_clauses = ["aggregate = :aggregate", "aggregate_id = :aggregate_id"]
            params = {
                "aggregate": aggregate,
                "aggregate_id": aggregate_id,
                "limit": limit,
            }

            if processed is not None:
                if processed:
                    where_clauses.append("processed_at IS NOT NULL")
                else:
                    where_clauses.append("processed_at IS NULL")

            where_clause = " AND ".join(where_clauses)

            query = text(
                f"""
                SELECT 
                    id, ts, site_id, aggregate, aggregate_id, type, payload, processed_at
                FROM outbox_events
                WHERE {where_clause}
                ORDER BY ts DESC, id DESC
                LIMIT :limit
            """
            )

            result = await session.execute(query, params)

            events = []
            for row in result.fetchall():
                event = OutboxEvent(
                    id=row.id,
                    ts=row.ts,
                    site_id=UUID(row.site_id) if row.site_id else None,
                    aggregate=row.aggregate,
                    aggregate_id=UUID(row.aggregate_id) if row.aggregate_id else None,
                    event_type=row.type,
                    payload=json.loads(row.payload) if row.payload else {},
                    processed_at=row.processed_at,
                )
                events.append(event)

            return events

        except Exception as e:
            logger.error(
                "Failed to get events by aggregate",
                extra={"aggregate": aggregate, "aggregate_id": str(aggregate_id), "error": str(e)},
            )
            raise OutboxError(f"Cannot get events by aggregate: {e}") from e


# Global outbox manager instance
outbox_manager: OutboxManager | None = None


def init_outbox_manager() -> OutboxManager:
    """Initialize global outbox manager."""
    global outbox_manager
    outbox_manager = OutboxManager()
    return outbox_manager


def get_outbox_manager() -> OutboxManager:
    """Get global outbox manager instance.

    Raises:
        RuntimeError: If outbox manager is not initialized

    Returns:
        OutboxManager instance
    """
    if outbox_manager is None:
        raise RuntimeError("Outbox manager not initialized. Call init_outbox_manager() first.")
    return outbox_manager


# Helper functions for common event patterns


async def enqueue_domain_event(
    session: AsyncSession,
    site_id: UUID,
    aggregate: str,
    aggregate_id: UUID,
    event_name: str,
    data: dict[str, Any],
    version: int | None = None,
) -> OutboxEvent:
    """Enqueue a domain event with standard payload structure.

    Args:
        session: Database session
        site_id: Site UUID
        aggregate: Aggregate type
        aggregate_id: Aggregate ID
        event_name: Event name (will be prefixed with aggregate)
        data: Event data
        version: Optional version number

    Returns:
        Created OutboxEvent
    """
    manager = get_outbox_manager()

    event_type = f"{aggregate}.{event_name}"
    payload = {
        "aggregate": aggregate,
        "aggregate_id": str(aggregate_id),
        "site_id": str(site_id),
        "event_name": event_name,
        "data": data,
        "timestamp": now().isoformat(),
    }

    if version is not None:
        payload["version"] = version

    return await manager.enqueue(
        session=session,
        site_id=site_id,
        aggregate=aggregate,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload,
    )


# Event types should be defined in specific microservices, not in core library
# Example:
# class PageEventTypes:
#     CREATED = "pages.created"
#     UPDATED = "pages.updated"
#     PUBLISHED = "pages.published"
