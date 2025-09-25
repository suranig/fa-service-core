"""Schema-driven REST API helpers for FastAPI."""

import builtins
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class UIConfig(BaseModel):
    """UI configuration for schema endpoints."""

    list: dict[str, Any] | None = None
    form: dict[str, Any] | None = None
    detail: dict[str, Any] | None = None
    filters: builtins.list[dict[str, Any]] | None = None


class ActionConfig(BaseModel):
    """Action configuration for custom resource actions."""

    name: str
    label: str
    icon: str | None = None
    confirmation: str | None = None
    disabled_when: str | None = None
    payload_schema: dict[str, Any] | None = None


class RelationConfig(BaseModel):
    """Relation configuration for related resources."""

    name: str
    type: str  # "one-to-many", "many-to-one", "many-to-many"
    resource: str
    foreign_key: str | None = None
    display_field: str | None = None


class ResourceSchema(BaseModel):
    """Complete resource schema for frontend generation."""

    name: str
    schema: dict[str, Any]  # JSON Schema
    ui: UIConfig | None = None
    actions: list[ActionConfig] | None = None
    relations: list[RelationConfig] | None = None
    meta: dict[str, Any] | None = None


class SchemaRegistry:
    """Registry for resource schemas."""

    def __init__(self) -> None:
        self._schemas: dict[str, ResourceSchema] = {}
        self._routers: dict[str, APIRouter] = {}

    def register_schema(
        self,
        resource_name: str,
        schema: ResourceSchema,
        router: APIRouter | None = None,
    ) -> None:
        """Register a resource schema.

        Args:
            resource_name: Name of the resource
            schema: Resource schema configuration
            router: Optional router to attach schema endpoint to
        """
        self._schemas[resource_name] = schema

        if router:
            self._routers[resource_name] = router
            # Add schema endpoint to router
            self._add_schema_endpoint(router, resource_name, schema)

        logger.info(
            "Registered resource schema",
            extra={"resource": resource_name, "has_router": router is not None},
        )

    def get_schema(self, resource_name: str) -> ResourceSchema | None:
        """Get schema for a resource."""
        return self._schemas.get(resource_name)

    def list_schemas(self) -> dict[str, ResourceSchema]:
        """List all registered schemas."""
        return self._schemas.copy()

    def _add_schema_endpoint(
        self,
        router: APIRouter,
        resource_name: str,
        schema: ResourceSchema,
    ) -> None:
        """Add schema endpoint to router."""

        @router.get(f"/{resource_name}/schema", response_model=None)
        async def get_schema() -> dict[str, Any]:
            """Get resource schema for frontend generation."""
            return schema.model_dump(exclude_none=True)


# Global schema registry
schema_registry = SchemaRegistry()


def resource_schema(
    schema_dict: dict[str, Any],
    ui_config: dict[str, Any] | None = None,
    actions: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """Decorator to register resource schema.

    Args:
        schema_dict: JSON Schema for the resource
        ui_config: UI configuration for frontend
        actions: List of custom actions
        relations: List of relations to other resources
        meta: Additional metadata

    Returns:
        Decorator function
    """

    def decorator(func: F) -> F:
        # Extract resource name from function name or explicit parameter
        resource_name = func.__name__.replace("create_", "").replace("_router", "")

        # Convert dict configs to Pydantic models
        ui = UIConfig(**ui_config) if ui_config else None
        action_list = [ActionConfig(**action) for action in (actions or [])]
        relation_list = [RelationConfig(**rel) for rel in (relations or [])]

        # Create resource schema
        resource_schema_obj = ResourceSchema(
            name=resource_name,
            schema=schema_dict,
            ui=ui,
            actions=action_list,
            relations=relation_list,
            meta=meta,
        )

        # Register schema
        schema_registry.register_schema(resource_name, resource_schema_obj)

        return func

    return decorator


def attach_schema_to_router(
    router: APIRouter,
    resource_name: str,
) -> None:
    """Attach schema endpoint to an existing router.

    Args:
        router: FastAPI router
        resource_name: Name of the resource
    """
    schema = schema_registry.get_schema(resource_name)
    if schema:
        schema_registry._routers[resource_name] = router
        schema_registry._add_schema_endpoint(router, resource_name, schema)
    else:
        logger.warning(f"Schema not found for resource: {resource_name}")


# Helper functions for common schema patterns


def create_list_ui_config(
    columns: list[str],
    searchable_columns: list[str] | None = None,
    sortable_columns: list[str] | None = None,
    default_sort: dict[str, str] | None = None,
    page_size: int = 20,
) -> dict[str, Any]:
    """Create UI config for list view.

    Args:
        columns: List of columns to display
        searchable_columns: Columns that can be searched
        sortable_columns: Columns that can be sorted
        default_sort: Default sort configuration
        page_size: Default page size

    Returns:
        UI configuration dict
    """
    return {
        "list": {
            "columns": columns,
            "searchable_columns": searchable_columns or [],
            "sortable_columns": sortable_columns or columns,
            "default_sort": default_sort or {"field": columns[0], "order": "asc"},
            "page_size": page_size,
        }
    }


def create_form_ui_config(
    layout: list[list[str]],
    widgets: dict[str, dict[str, Any]] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create UI config for form view.

    Args:
        layout: Layout of form fields (rows of fields)
        widgets: Custom widget configurations
        validation: Client-side validation rules

    Returns:
        UI configuration dict
    """
    return {
        "form": {
            "layout": layout,
            "widgets": widgets or {},
            "validation": validation or {},
        }
    }


def create_action_config(
    name: str,
    label: str,
    icon: str | None = None,
    confirmation: str | None = None,
    payload_schema: dict[str, Any] | None = None,
    disabled_when: str | None = None,
) -> dict[str, Any]:
    """Create action configuration.

    Args:
        name: Action name (used in URLs)
        label: Display label
        icon: Icon name
        confirmation: Confirmation message
        payload_schema: JSON schema for action payload
        disabled_when: Condition when action is disabled

    Returns:
        Action configuration dict
    """
    config = {
        "name": name,
        "label": label,
    }

    if icon:
        config["icon"] = icon
    if confirmation:
        config["confirmation"] = confirmation
    if payload_schema:
        config["payload_schema"] = payload_schema
    if disabled_when:
        config["disabled_when"] = disabled_when

    return config


def create_relation_config(
    name: str,
    relation_type: str,
    resource: str,
    foreign_key: str | None = None,
    display_field: str | None = None,
) -> dict[str, Any]:
    """Create relation configuration.

    Args:
        name: Relation name
        relation_type: Type of relation ("one-to-many", "many-to-one", "many-to-many")
        resource: Related resource name
        foreign_key: Foreign key field
        display_field: Field to display for related records

    Returns:
        Relation configuration dict
    """
    config = {
        "name": name,
        "type": relation_type,
        "resource": resource,
    }

    if foreign_key:
        config["foreign_key"] = foreign_key
    if display_field:
        config["display_field"] = display_field

    return config


# Common schema patterns

COMMON_SCHEMAS = {
    "uuid_field": {
        "type": "string",
        "format": "uuid",
        "title": "ID",
        "readOnly": True,
    },
    "timestamp_field": {
        "type": "string",
        "format": "date-time",
        "title": "Timestamp",
        "readOnly": True,
    },
    "slug_field": {
        "type": "string",
        "pattern": "^[a-z0-9-]+$",
        "title": "Slug",
        "description": "URL-friendly identifier",
    },
}


def get_common_schema(name: str) -> dict[str, Any]:
    """Get a common schema pattern.

    Args:
        name: Schema pattern name

    Returns:
        Schema pattern dict
    """
    return COMMON_SCHEMAS.get(name, {}).copy()


# Concrete schema implementations should be in specific microservices
# Use get_common_schema() and helper functions to build schemas in your microservices
