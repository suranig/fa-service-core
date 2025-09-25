"""Idempotency middleware for FastAPI applications."""

import hashlib
import json
import logging
from enum import IntEnum
from typing import Any

from fastapi import Request, Response
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from .db import get_db_manager

logger = logging.getLogger(__name__)


class IdempotencyStatus(IntEnum):
    """Status codes for idempotency keys."""

    PROCESSING = 1
    COMPLETED = 2
    FAILED = 3


class IdempotencyError(Exception):
    """Base exception for idempotency operations."""

    pass


class IdempotencyRecord:
    """Represents an idempotency record."""

    def __init__(
        self,
        key: str,
        status: IdempotencyStatus,
        headers: dict[str, str],
        body: bytes,
        created_at: str | None = None,
    ) -> None:
        self.key = key
        self.status = status
        self.headers = headers
        self.body = body
        self.created_at = created_at

    @classmethod
    def from_response(
        cls,
        key: str,
        response: Response,
        status: IdempotencyStatus = IdempotencyStatus.COMPLETED,
    ) -> "IdempotencyRecord":
        """Create idempotency record from FastAPI response."""
        # Convert headers to dict, filtering out problematic headers
        headers = {
            name: value
            for name, value in response.headers.items()
            if name.lower() not in {"content-length", "transfer-encoding"}
        }

        return cls(
            key=key,
            status=status,
            headers=headers,
            body=response.body if hasattr(response, "body") else b"",
        )

    def to_response(self) -> Response:
        """Convert idempotency record back to FastAPI response."""
        response = Response(
            content=self.body,
            status_code=200,  # Default, should be overridden by headers if stored
            headers=self.headers,
        )
        return response


class IdempotencyManager:
    """Manages idempotency keys and records."""

    def __init__(self) -> None:
        pass

    async def get_record(self, key: str) -> IdempotencyRecord | None:
        """Retrieve idempotency record by key.

        Args:
            key: Idempotency key

        Returns:
            IdempotencyRecord if found, None otherwise
        """
        db_manager = get_db_manager()

        async with db_manager.read_session_factory() as session:
            query = text(
                """
                SELECT id, status, headers, body, created_at
                FROM idempotency_keys
                WHERE id = :key
            """
            )

            result = await session.execute(query, {"key": key})
            row = result.fetchone()

            if row:
                return IdempotencyRecord(
                    key=row.id,
                    status=IdempotencyStatus(row.status),
                    headers=row.headers or {},
                    body=row.body or b"",
                    created_at=row.created_at.isoformat() if row.created_at else None,
                )
            return None

    async def store_record(self, record: IdempotencyRecord) -> bool:
        """Store idempotency record.

        Args:
            record: IdempotencyRecord to store

        Returns:
            True if stored successfully, False if key already exists
        """
        db_manager = get_db_manager()

        async with db_manager.write_session_factory() as session:
            try:
                async with session.begin():
                    query = text(
                        """
                        INSERT INTO idempotency_keys (id, status, headers, body, created_at)
                        VALUES (:key, :status, :headers, :body, NOW())
                    """
                    )

                    await session.execute(
                        query,
                        {
                            "key": record.key,
                            "status": record.status.value,
                            "headers": json.dumps(record.headers),
                            "body": record.body,
                        },
                    )

                logger.debug("Stored idempotency record", extra={"key": record.key})
                return True

            except Exception as e:
                # Check if it's a duplicate key error
                if "duplicate key" in str(e).lower() or "unique constraint" in str(e).lower():
                    logger.debug("Idempotency key already exists", extra={"key": record.key})
                    return False
                else:
                    logger.error(
                        "Failed to store idempotency record",
                        extra={"key": record.key, "error": str(e)},
                    )
                    raise IdempotencyError(f"Failed to store idempotency record: {e}") from e

    async def update_record_status(
        self,
        key: str,
        status: IdempotencyStatus,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> bool:
        """Update idempotency record status and response data.

        Args:
            key: Idempotency key
            status: New status
            headers: Response headers (optional)
            body: Response body (optional)

        Returns:
            True if updated successfully
        """
        db_manager = get_db_manager()

        async with db_manager.write_session_factory() as session:
            try:
                async with session.begin():
                    # Build dynamic update query
                    update_parts = ["status = :status"]
                    params = {"key": key, "status": status.value}

                    if headers is not None:
                        update_parts.append("headers = :headers")
                        params["headers"] = json.dumps(headers)

                    if body is not None:
                        update_parts.append("body = :body")
                        params["body"] = body

                    query = text(
                        f"""
                        UPDATE idempotency_keys
                        SET {', '.join(update_parts)}
                        WHERE id = :key
                    """
                    )

                    result = await session.execute(query, params)

                    if result.rowcount == 0:
                        logger.warning("Idempotency key not found for update", extra={"key": key})
                        return False

                logger.debug(
                    "Updated idempotency record", extra={"key": key, "status": status.name}
                )
                return True

            except Exception as e:
                logger.error(
                    "Failed to update idempotency record", extra={"key": key, "error": str(e)}
                )
                raise IdempotencyError(f"Failed to update idempotency record: {e}") from e

    async def cleanup_old_records(self, older_than_hours: int = 24) -> int:
        """Clean up old idempotency records.

        Args:
            older_than_hours: Remove records older than this many hours

        Returns:
            Number of records removed
        """
        db_manager = get_db_manager()

        async with db_manager.write_session_factory() as session:
            try:
                async with session.begin():
                    query = text(
                        """
                        DELETE FROM idempotency_keys
                        WHERE created_at < NOW() - INTERVAL ':hours hours'
                    """
                    )

                    result = await session.execute(query, {"hours": older_than_hours})
                    deleted_count = result.rowcount

                logger.info(
                    "Cleaned up old idempotency records",
                    extra={"deleted_count": deleted_count, "older_than_hours": older_than_hours},
                )
                return deleted_count

            except Exception as e:
                logger.error("Failed to cleanup idempotency records", extra={"error": str(e)})
                raise IdempotencyError(f"Failed to cleanup records: {e}") from e


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """ASGI middleware for handling idempotency."""

    def __init__(
        self,
        app: Any,
        header_name: str = "Idempotency-Key",
        methods: set[str] | None = None,
        skip_paths: set[str] | None = None,
    ) -> None:
        """Initialize idempotency middleware.

        Args:
            app: ASGI application
            header_name: Name of the idempotency header
            methods: HTTP methods to apply idempotency to (default: POST, PUT, PATCH)
            skip_paths: Paths to skip idempotency checking
        """
        super().__init__(app)
        self.header_name = header_name
        self.methods = methods or {"POST", "PUT", "PATCH"}
        self.skip_paths = skip_paths or {"/healthz", "/readyz", "/metrics"}
        self.manager = IdempotencyManager()

    async def dispatch(self, request: Request, call_next: Any) -> StarletteResponse:
        """Process request with idempotency checking."""
        # Skip if method not supported or path is excluded
        if request.method not in self.methods or request.url.path in self.skip_paths:
            return await call_next(request)

        # Get idempotency key from header
        idempotency_key = request.headers.get(self.header_name)
        if not idempotency_key:
            # No idempotency key provided, process normally
            return await call_next(request)

        # Validate and normalize the key
        key = self._normalize_key(idempotency_key, request)

        # Check if we have a cached response
        existing_record = await self.manager.get_record(key)
        if existing_record:
            if existing_record.status == IdempotencyStatus.COMPLETED:
                logger.info(
                    "Returning cached idempotent response",
                    extra={"key": key, "path": request.url.path},
                )
                return existing_record.to_response()
            elif existing_record.status == IdempotencyStatus.PROCESSING:
                # Another request is processing, return conflict
                logger.warning(
                    "Idempotency key is being processed",
                    extra={"key": key, "path": request.url.path},
                )
                return Response(
                    content={"error": "Request is being processed"},
                    status_code=409,
                    headers={"Content-Type": "application/json"},
                )
            elif existing_record.status == IdempotencyStatus.FAILED:
                # Previous request failed, allow retry
                logger.info(
                    "Retrying failed idempotent request",
                    extra={"key": key, "path": request.url.path},
                )

        # Create processing record
        processing_record = IdempotencyRecord(
            key=key,
            status=IdempotencyStatus.PROCESSING,
            headers={},
            body=b"",
        )

        stored = await self.manager.store_record(processing_record)
        if not stored and existing_record is None:
            # Race condition: another request started processing
            logger.warning(
                "Race condition in idempotency processing",
                extra={"key": key, "path": request.url.path},
            )
            return Response(
                content={"error": "Request is being processed"},
                status_code=409,
                headers={"Content-Type": "application/json"},
            )

        try:
            # Process the request
            response = await call_next(request)

            # Store the successful response
            if 200 <= response.status_code < 300:
                # Read response body for storage
                response_body = b""
                if hasattr(response, "body"):
                    response_body = response.body

                response_headers = dict(response.headers)

                await self.manager.update_record_status(
                    key=key,
                    status=IdempotencyStatus.COMPLETED,
                    headers=response_headers,
                    body=response_body,
                )

                logger.info(
                    "Stored successful idempotent response",
                    extra={"key": key, "status_code": response.status_code},
                )
            else:
                # Mark as failed for non-success responses
                await self.manager.update_record_status(
                    key=key,
                    status=IdempotencyStatus.FAILED,
                )

                logger.warning(
                    "Marked idempotent request as failed",
                    extra={"key": key, "status_code": response.status_code},
                )

            return response

        except Exception as e:
            # Mark as failed on exception
            await self.manager.update_record_status(
                key=key,
                status=IdempotencyStatus.FAILED,
            )

            logger.error(
                "Idempotent request failed with exception", extra={"key": key, "error": str(e)}
            )
            raise

    def _normalize_key(self, key: str, request: Request) -> str:
        """Normalize idempotency key.

        Args:
            key: Raw idempotency key
            request: Request object

        Returns:
            Normalized key including request fingerprint
        """
        # Create a fingerprint of the request
        fingerprint_data = {
            "method": request.method,
            "path": request.url.path,
            "query": str(request.query_params),
            "key": key,
        }

        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_data, sort_keys=True).encode()
        ).hexdigest()[:16]

        return f"{key}:{fingerprint}"


# Global idempotency manager instance
idempotency_manager: IdempotencyManager | None = None


def init_idempotency_manager() -> IdempotencyManager:
    """Initialize global idempotency manager."""
    global idempotency_manager
    idempotency_manager = IdempotencyManager()
    return idempotency_manager


def get_idempotency_manager() -> IdempotencyManager:
    """Get global idempotency manager instance."""
    if idempotency_manager is None:
        raise RuntimeError("Idempotency manager not initialized.")
    return idempotency_manager
