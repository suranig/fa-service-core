"""Custom actions decorator and framework for FastAPI resources."""

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .audit import AuditManager
from .idempotency import get_idempotency_manager
from .outbox import get_outbox_manager
from .site_resolver import site_id_dep
from .uow import write_uow

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class ActionContext(BaseModel):
    """Context for action execution."""

    site_id: UUID
    resource_id: UUID
    action_name: str
    user_id: UUID | None = None
    idempotency_key: str | None = None
    meta: dict[str, Any] | None = None


class ActionResult(BaseModel):
    """Result of action execution."""

    success: bool
    message: str
    data: dict[str, Any] | None = None
    version: int | None = None


class ActionError(Exception):
    """Base exception for action operations."""

    pass


class ActionRegistry:
    """Registry for custom actions."""

    def __init__(self) -> None:
        self._actions: dict[str, dict[str, Callable]] = {}

    def register_action(
        self,
        resource: str,
        action_name: str,
        handler: Callable,
    ) -> None:
        """Register an action handler.

        Args:
            resource: Resource name (e.g., "pages")
            action_name: Action name (e.g., "publish")
            handler: Action handler function
        """
        if resource not in self._actions:
            self._actions[resource] = {}

        self._actions[resource][action_name] = handler

        logger.info("Registered action", extra={"resource": resource, "action": action_name})

    def get_action(self, resource: str, action_name: str) -> Callable | None:
        """Get action handler.

        Args:
            resource: Resource name
            action_name: Action name

        Returns:
            Action handler function if found
        """
        return self._actions.get(resource, {}).get(action_name)

    def list_actions(self, resource: str) -> list[str]:
        """List available actions for a resource.

        Args:
            resource: Resource name

        Returns:
            List of action names
        """
        return list(self._actions.get(resource, {}).keys())


# Global action registry
action_registry = ActionRegistry()


def action(
    name: str,
    resource: str,
    audit_event_type: str | None = None,
    emit_outbox_event: bool = True,
    require_idempotency: bool = True,
) -> Callable[[F], F]:
    """Decorator for custom resource actions.

    This decorator:
    1. Handles idempotency checking
    2. Sets up database transaction with site context
    3. Executes the action function
    4. Records audit trail
    5. Emits outbox events
    6. Returns standardized response

    Args:
        name: Action name
        resource: Resource type (e.g., "pages")
        audit_event_type: Custom audit event type (defaults to action name)
        emit_outbox_event: Whether to emit outbox event on success
        require_idempotency: Whether to require Idempotency-Key header

    Returns:
        Decorated action function
    """

    def decorator(func: F) -> F:
        # Register the action
        action_registry.register_action(resource, name, func)

        @wraps(func)
        async def wrapper(
            resource_id: UUID,
            request: Request,
            response: Response,
            site: Site = Depends(site_dep),
            payload: dict[str, Any] | None = None,
        ) -> ActionResult:
            # Check for idempotency key if required
            idempotency_key = request.headers.get("Idempotency-Key")
            if require_idempotency and not idempotency_key:
                raise HTTPException(
                    status_code=400, detail="Idempotency-Key header is required for this action"
                )

            # Check idempotency if key provided
            if idempotency_key:
                idempotency_manager = get_idempotency_manager()
                existing_record = await idempotency_manager.get_record(idempotency_key)

                if existing_record:
                    if existing_record.status.value == 2:  # COMPLETED
                        logger.info(
                            "Returning cached action result",
                            extra={
                                "action": name,
                                "resource": resource,
                                "resource_id": str(resource_id),
                                "idempotency_key": idempotency_key,
                            },
                        )
                        # Return cached result (should be implemented properly)
                        return ActionResult(
                            success=True,
                            message=f"Action {name} completed (cached)",
                            data={"cached": True},
                        )
                    elif existing_record.status.value == 1:  # PROCESSING
                        raise HTTPException(
                            status_code=409, detail="Action is currently being processed"
                        )

            # Create action context
            context = ActionContext(
                site=site,
                resource_id=resource_id,
                action_name=name,
                user_id=None,  # TODO: Extract from auth
                idempotency_key=idempotency_key,
                meta={"request_path": str(request.url.path)},
            )

            # Execute action within transaction
            async with write_uow(site.id) as session:
                try:
                    # Execute the action function
                    result = await func(
                        session=session,
                        context=context,
                        payload=payload or {},
                    )

                    # Ensure result is ActionResult
                    if not isinstance(result, ActionResult):
                        result = ActionResult(
                            success=True,
                            message=f"Action {name} completed",
                            data=result if isinstance(result, dict) else {"result": result},
                        )

                    # Record audit if successful
                    if result.success:
                        event_type = audit_event_type or name
                        await AuditManager.record_custom_event(
                            session=session,
                            site_id=site.id,
                            user_id=context.user_id,
                            resource=resource,
                            resource_id=resource_id,
                            event_type=event_type,
                            version=result.version or 1,
                            data={
                                "action": name,
                                "payload": payload,
                                "result": result.data,
                            },
                            meta=context.meta,
                        )

                        # Emit outbox event if configured
                        if emit_outbox_event:
                            outbox_manager = get_outbox_manager()
                            await outbox_manager.enqueue(
                                session=session,
                                site_id=site.id,
                                aggregate=resource,
                                aggregate_id=resource_id,
                                event_type=f"{resource}.{name}",
                                payload={
                                    "action": name,
                                    "resource_id": str(resource_id),
                                    "site_id": str(site.id),
                                    "payload": payload,
                                    "result": result.data,
                                    "version": result.version,
                                },
                            )

                        logger.info(
                            "Action executed successfully",
                            extra={
                                "action": name,
                                "resource": resource,
                                "resource_id": str(resource_id),
                                "site_id": str(site.id),
                                "version": result.version,
                            },
                        )

                    return result

                except Exception as e:
                    logger.error(
                        "Action execution failed",
                        extra={
                            "action": name,
                            "resource": resource,
                            "resource_id": str(resource_id),
                            "error": str(e),
                        },
                    )

                    # Return error result
                    return ActionResult(
                        success=False,
                        message=f"Action {name} failed: {str(e)}",
                        data={"error": str(e)},
                    )

        return wrapper

    return decorator


# Concrete action implementations should be in specific microservices, not in core library


# Helper functions for creating action endpoints


def create_action_endpoint(
    resource: str,
    action_name: str,
    handler: Callable,
    payload_model: type | None = None,
) -> Callable:
    """Create a FastAPI endpoint for a custom action.

    Args:
        resource: Resource name
        action_name: Action name
        handler: Action handler function
        payload_model: Pydantic model for request payload

    Returns:
        FastAPI endpoint function
    """
    # Decorate the handler with action decorator
    decorated_handler = action(
        name=action_name,
        resource=resource,
    )(handler)

    async def endpoint(
        resource_id: UUID,
        request: Request,
        response: Response,
        site_id: UUID = Depends(site_id_dep),
        payload: payload_model | None = None,
    ) -> ActionResult:
        payload_dict = payload.model_dump() if payload else {}
        return await decorated_handler(
            resource_id=resource_id,
            request=request,
            response=response,
            site_id=site_id,
            payload=payload_dict,
        )

    return endpoint


# Pre-defined decorators removed - each microservice should define its own actions
