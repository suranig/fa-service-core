"""Error handling and HTTP status code mapping."""

import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_ENTITY,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

logger = logging.getLogger(__name__)


class BaseError(Exception):
    """Base exception for application errors."""

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
        status_code: int = HTTP_500_INTERNAL_SERVER_ERROR,
    ) -> None:
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Convert error to dictionary."""
        error_dict = {
            "error": self.__class__.__name__,
            "message": self.message,
            "status_code": self.status_code,
        }

        if self.details:
            error_dict["details"] = self.details

        return error_dict


class ValidationError(BaseError):
    """Validation error (400 Bad Request)."""

    def __init__(
        self,
        message: str = "Validation failed",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details, HTTP_400_BAD_REQUEST)


class AuthenticationError(BaseError):
    """Authentication error (401 Unauthorized)."""

    def __init__(
        self,
        message: str = "Authentication required",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details, 401)


class AuthorizationError(BaseError):
    """Authorization error (403 Forbidden)."""

    def __init__(
        self,
        message: str = "Access forbidden",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details, HTTP_403_FORBIDDEN)


class NotFoundError(BaseError):
    """Resource not found error (404 Not Found)."""

    def __init__(
        self,
        message: str = "Resource not found",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details, HTTP_404_NOT_FOUND)


class ConflictError(BaseError):
    """Conflict error (409 Conflict)."""

    def __init__(
        self,
        message: str = "Resource conflict",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details, HTTP_409_CONFLICT)


class BusinessRuleError(BaseError):
    """Business rule violation error (422 Unprocessable Entity)."""

    def __init__(
        self,
        message: str = "Business rule violation",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details, HTTP_422_UNPROCESSABLE_ENTITY)


class InternalError(BaseError):
    """Internal server error (500 Internal Server Error)."""

    def __init__(
        self,
        message: str = "Internal server error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details, HTTP_500_INTERNAL_SERVER_ERROR)


# Domain-specific errors should be defined in individual microservices, not in core library


# Database-related errors


class DatabaseError(InternalError):
    """Database operation errors."""

    pass


class ConnectionError(DatabaseError):
    """Database connection errors."""

    def __init__(
        self,
        details: dict[str, Any] | None = None,
    ) -> None:
        message = "Database connection failed"
        super().__init__(message, details)


class TransactionError(DatabaseError):
    """Database transaction errors."""

    def __init__(
        self,
        details: dict[str, Any] | None = None,
    ) -> None:
        message = "Database transaction failed"
        super().__init__(message, details)


class RowLevelSecurityError(AuthorizationError):
    """Row Level Security violation error."""

    def __init__(
        self,
        details: dict[str, Any] | None = None,
    ) -> None:
        message = "Row Level Security policy violation"
        super().__init__(message, details)


# Error handlers


async def base_error_handler(request: Request, exc: BaseError) -> JSONResponse:
    """Handle BaseError exceptions.

    Args:
        request: FastAPI request
        exc: BaseError exception

    Returns:
        JSON response with error details
    """
    from .observability import get_request_id, get_site_id_from_request

    # Get request context
    request_id = get_request_id(request)
    site_id = get_site_id_from_request(request)

    # Build response data
    response_data = exc.to_dict()

    if request_id:
        response_data["request_id"] = request_id

    # Log the error
    log_extra = {
        "error_type": exc.__class__.__name__,
        "status_code": exc.status_code,
        "message": exc.message,
    }

    if request_id:
        log_extra["request_id"] = request_id
    if site_id:
        log_extra["site_id"] = site_id
    if exc.details:
        log_extra["error_details"] = exc.details

    # Log as error for 5xx, warning for 4xx
    if exc.status_code >= 500:
        logger.error("Internal error occurred", extra=log_extra, exc_info=True)
    else:
        logger.warning("Client error occurred", extra=log_extra)

    return JSONResponse(status_code=exc.status_code, content=response_data)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle Pydantic validation errors.

    Args:
        request: FastAPI request
        exc: Validation exception

    Returns:
        JSON response with validation error details
    """
    from pydantic import ValidationError as PydanticValidationError

    if isinstance(exc, PydanticValidationError):
        validation_error = ValidationError(
            message="Request validation failed", details={"validation_errors": exc.errors()}
        )
        return await base_error_handler(request, validation_error)

    # Fallback to default handler
    return await http_exception_handler(request, HTTPException(status_code=422, detail=str(exc)))


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle generic exceptions.

    Args:
        request: FastAPI request
        exc: Exception

    Returns:
        JSON response with error details
    """
    from .observability import get_request_id, get_site_id_from_request

    # Get request context
    request_id = get_request_id(request)
    site_id = get_site_id_from_request(request)

    # Create internal error
    internal_error = InternalError(
        message="An unexpected error occurred", details={"exception_type": exc.__class__.__name__}
    )

    # Log the exception
    log_extra = {
        "exception_type": exc.__class__.__name__,
        "exception_message": str(exc),
    }

    if request_id:
        log_extra["request_id"] = request_id
    if site_id:
        log_extra["site_id"] = site_id

    logger.error("Unhandled exception occurred", extra=log_extra, exc_info=True)

    return await base_error_handler(request, internal_error)


# Error mapping utilities


def map_database_error(exc: Exception) -> BaseError:
    """Map database exceptions to application errors.

    Args:
        exc: Database exception

    Returns:
        Mapped application error
    """
    error_message = str(exc).lower()

    # PostgreSQL specific error mapping
    if "duplicate key" in error_message or "unique constraint" in error_message:
        return ConflictError(
            message="Resource already exists", details={"database_error": str(exc)}
        )

    if "foreign key" in error_message:
        return ValidationError(
            message="Referenced resource does not exist", details={"database_error": str(exc)}
        )

    if "check constraint" in error_message:
        return ValidationError(
            message="Data validation failed", details={"database_error": str(exc)}
        )

    if "row security" in error_message or "policy" in error_message:
        return RowLevelSecurityError(details={"database_error": str(exc)})

    if "connection" in error_message:
        return ConnectionError(details={"database_error": str(exc)})

    if "timeout" in error_message:
        return InternalError(
            message="Database operation timed out", details={"database_error": str(exc)}
        )

    # Default to generic database error
    return DatabaseError(message="Database operation failed", details={"database_error": str(exc)})


def setup_error_handlers(app) -> None:
    """Setup error handlers for FastAPI app.

    Args:
        app: FastAPI application instance
    """
    from pydantic import ValidationError as PydanticValidationError

    # Register error handlers
    app.add_exception_handler(BaseError, base_error_handler)
    app.add_exception_handler(PydanticValidationError, validation_error_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    logger.info("Error handlers registered")


# Context managers for error handling


class ErrorContext:
    """Context manager for consistent error handling."""

    def __init__(
        self,
        operation: str,
        error_mapping: dict[type, type] | None = None,
    ) -> None:
        self.operation = operation
        self.error_mapping = error_mapping or {}

    def __enter__(self) -> "ErrorContext":
        return self

    def __exit__(self, exc_type: type, exc_val: Exception, exc_tb) -> bool:
        if exc_type is None:
            return False

        # Map specific exceptions
        if exc_type in self.error_mapping:
            mapped_error_class = self.error_mapping[exc_type]
            raise mapped_error_class(
                message=f"{self.operation} failed: {str(exc_val)}",
                details={"original_error": str(exc_val)},
            ) from exc_val

        # Try database error mapping
        if "psycopg" in str(exc_type) or "sqlalchemy" in str(exc_type):
            raise map_database_error(exc_val) from exc_val

        # Let other exceptions propagate
        return False


# Helper functions for common error patterns


def raise_not_found(resource: str, identifier: str) -> None:
    """Raise a generic not found error.

    Args:
        resource: Resource type
        identifier: Resource identifier
    """
    raise NotFoundError(
        message=f"{resource.title()} not found",
        details={"resource": resource, "identifier": identifier},
    )


def raise_conflict(resource: str, field: str, value: str) -> None:
    """Raise a generic conflict error.

    Args:
        resource: Resource type
        field: Conflicting field
        value: Conflicting value
    """
    raise ConflictError(
        message=f"{resource.title()} {field} already exists",
        details={"resource": resource, "field": field, "value": value},
    )


def raise_business_rule_violation(rule: str, details: dict[str, Any] | None = None) -> None:
    """Raise a generic business rule violation error.

    Args:
        rule: Business rule description
        details: Additional details
    """
    raise BusinessRuleError(message=f"Business rule violation: {rule}", details=details)
