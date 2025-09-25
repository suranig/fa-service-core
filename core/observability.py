"""Observability utilities with Prometheus metrics and request tracking."""

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response

# Metrics functionality removed - focusing on request tracking and logging only
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

logger = logging.getLogger(__name__)


# Metrics functionality removed - using only logging for observability


class RequestTrackingMiddleware(BaseHTTPMiddleware):
    """Middleware for tracking requests with logging and request IDs."""

    def __init__(
        self,
        app: Any,
        request_id_header: str = "X-Request-ID",
        site_id_header: str = "X-Site-ID",
    ) -> None:
        super().__init__(app)
        self.request_id_header = request_id_header
        self.site_id_header = site_id_header

    async def dispatch(self, request: Request, call_next: Callable) -> StarletteResponse:
        """Process request with tracking and logging."""
        # Generate or extract request ID
        request_id = request.headers.get(self.request_id_header)
        if not request_id:
            request_id = str(uuid.uuid4())

        # Extract site ID for logging
        site_id = request.headers.get(self.site_id_header, "unknown")

        # Add request ID to request state
        request.state.request_id = request_id
        request.state.site_id = site_id

        # Extract endpoint for logging (remove dynamic parts)
        endpoint = self._extract_endpoint(request.url.path)

        # Start timing
        start_time = time.time()

        try:
            # Process request
            response = await call_next(request)

            # Calculate duration
            duration = time.time() - start_time

            # Add request ID to response headers
            response.headers[self.request_id_header] = request_id

            # Log request
            logger.info(
                "Request processed",
                extra={
                    "request_id": request_id,
                    "site_id": site_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration": duration,
                    "endpoint": endpoint,
                },
            )

            return response

        except Exception as e:
            # Calculate duration
            duration = time.time() - start_time

            # Log error
            logger.error(
                "Request failed",
                extra={
                    "request_id": request_id,
                    "site_id": site_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration": duration,
                    "endpoint": endpoint,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )

            raise

    def _extract_endpoint(self, path: str) -> str:
        """Extract endpoint pattern from path for logging.

        This removes dynamic parts like UUIDs to group related requests.
        """
        # Replace UUIDs with placeholder
        import re

        # UUID pattern
        uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        path = re.sub(uuid_pattern, "{id}", path, flags=re.IGNORECASE)

        # Numeric IDs
        path = re.sub(r"/\d+/", "/{id}/", path)
        path = re.sub(r"/\d+$", "/{id}", path)

        # Remove query parameters
        path = path.split("?")[0]

        return path


# Database and business metrics classes removed - using only logging


def get_request_id(request: Request) -> str | None:
    """Get request ID from request state.

    Args:
        request: FastAPI request object

    Returns:
        Request ID if available
    """
    return getattr(request.state, "request_id", None)


def get_site_id_from_request(request: Request) -> str | None:
    """Get site ID from request state.

    Args:
        request: FastAPI request object

    Returns:
        Site ID if available
    """
    return getattr(request.state, "site_id", None)


def setup_logging_with_request_id(request: Request) -> dict[str, Any]:
    """Setup logging context with request ID.

    Args:
        request: FastAPI request object

    Returns:
        Logging context dict
    """
    context = {}

    request_id = get_request_id(request)
    if request_id:
        context["request_id"] = request_id

    site_id = get_site_id_from_request(request)
    if site_id:
        context["site_id"] = site_id

    return context


async def metrics_endpoint() -> Response:
    """Simple metrics endpoint (metrics functionality removed).

    Returns:
        Basic application info instead of Prometheus metrics
    """
    return Response(
        content="Metrics functionality removed - check logs for observability data",
        media_type="text/plain; charset=utf-8",
    )


def init_application_info(
    app_name: str,
    app_version: str,
    build_info: dict[str, str] | None = None,
) -> None:
    """Initialize application info logging.

    Args:
        app_name: Application name
        app_version: Application version
        build_info: Additional build information
    """
    info_data = {
        "name": app_name,
        "version": app_version,
    }

    if build_info:
        info_data.update(build_info)

    logger.info("Application info initialized", extra=info_data)


# Metrics collector removed - using only logging for observability


# Context manager for timing operations
class TimedOperation:
    """Context manager for timing operations with logging."""

    def __init__(
        self,
        operation_type: str,
        pool_type: str = "unknown",
        site_id: str = "unknown",
        log_result: bool = True,
    ) -> None:
        self.operation_type = operation_type
        self.pool_type = pool_type
        self.site_id = site_id
        self.log_result = log_result
        self.start_time: float | None = None
        self.duration: float | None = None

    def __enter__(self) -> "TimedOperation":
        """Start timing."""
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """End timing and log result."""
        if self.start_time is not None:
            self.duration = time.time() - self.start_time

            if self.log_result:
                logger.debug(
                    "Operation completed",
                    extra={
                        "operation_type": self.operation_type,
                        "pool_type": self.pool_type,
                        "site_id": self.site_id,
                        "duration": self.duration,
                        "success": exc_type is None,
                    },
                )

    def get_duration(self) -> float | None:
        """Get operation duration.

        Returns:
            Duration in seconds if available
        """
        return self.duration


# Health check utilities
class HealthChecker:
    """Health check utilities."""

    @staticmethod
    async def check_database(db_manager) -> dict[str, Any]:
        """Check database health.

        Args:
            db_manager: Database manager instance

        Returns:
            Health check result
        """
        try:
            from .db import check_database_connection

            # Check write database
            write_info = await check_database_connection(db_manager.write_engine)

            # Check read database
            read_info = await check_database_connection(db_manager.read_engine)

            return {
                "status": "healthy",
                "write_db": write_info,
                "read_db": read_info,
            }

        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
            }

   

    @staticmethod
    def check_overall_health(
        database_health: dict[str, Any],
    ) -> dict[str, Any]:
        """Check overall application health.

        Args:
            database_health: Database health status

        Returns:
            Overall health status
        """
        checks = {
            "database": database_health,
        }

        # Determine overall status
        overall_status = "healthy"
        for check_name, check_result in checks.items():
            if check_result.get("status") != "healthy":
                overall_status = "unhealthy"
                break

        return {
            "status": overall_status,
            "checks": checks,
        }
