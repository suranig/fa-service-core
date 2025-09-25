"""FastAPI query parameters parsing for filtering, sorting, and pagination."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import unquote_plus

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .repository import (
    Filter,
    FilterOperator,
    Pagination,
    QueryOptions,
    Sort,
    SortDirection,
)

logger = logging.getLogger(__name__)


class PaginationParams(BaseModel):
    """Pagination query parameters."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    page: int = Field(default=1, ge=1, description="Page number (1-based)")
    page_size: int = Field(default=50, ge=1, le=1000, description="Number of items per page")
    max_page_size: int = Field(default=1000, ge=1, description="Maximum page size")

    @model_validator(mode="after")
    def validate_page_size(self) -> "PaginationParams":
        """Ensure page_size doesn't exceed max_page_size."""
        if self.page_size > self.max_page_size:
            self.page_size = self.max_page_size
        return self

    def to_pagination(self) -> Pagination:
        """Convert to Pagination object."""
        return Pagination(
            page=self.page, page_size=self.page_size, max_page_size=self.max_page_size
        )


class SortParams(BaseModel):
    """Sort query parameters."""

    sort: list[str] | None = Field(
        default=None,
        description="Sort fields in format: field:direction (e.g., 'created_at:desc,title:asc')",
    )

    @field_validator("sort", mode="before")
    @classmethod
    def parse_sort_string(cls, v: Any) -> list[str] | None:
        """Parse sort string into list."""
        if v is None:
            return None
        if isinstance(v, str):
            # Handle comma-separated sort fields
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    def to_sorts(self) -> list[Sort]:
        """Convert to Sort objects."""
        if not self.sort:
            return []

        sorts = []
        for sort_spec in self.sort:
            try:
                if ":" in sort_spec:
                    field, direction_str = sort_spec.split(":", 1)
                    direction = (
                        SortDirection.DESC
                        if direction_str.lower() in ("desc", "descending", "d")
                        else SortDirection.ASC
                    )
                else:
                    field = sort_spec
                    direction = SortDirection.ASC

                sorts.append(Sort(field=field.strip(), direction=direction))
            except Exception as e:
                logger.warning(f"Invalid sort specification: {sort_spec}, error: {e}")

        return sorts


class FilterParams(BaseModel):
    """Filter query parameters."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    filter: list[str] | None = Field(
        default=None,
        description="Filters in format: field:operator:value (e.g., 'status:eq:published,created_at:gte:2024-01-01')",
        alias="filter[]",  # Support array query params: ?filter[]=status:eq:published&filter[]=created_at:gte:2024-01-01
    )

    @field_validator("filter", mode="before")
    @classmethod
    def parse_filter_string(cls, v: Any) -> list[str] | None:
        """Parse filter string into list."""
        if v is None:
            return None
        if isinstance(v, str):
            # Handle comma-separated filters
            return [f.strip() for f in v.split(",") if f.strip()]
        return v

    def to_filters(self) -> list[Filter]:
        """Convert to Filter objects."""
        if not self.filter:
            return []

        filters = []
        for filter_spec in self.filter:
            try:
                filter_obj = self._parse_filter_spec(filter_spec)
                if filter_obj:
                    filters.append(filter_obj)
            except Exception as e:
                logger.warning(f"Invalid filter specification: {filter_spec}, error: {e}")

        return filters

    def _parse_filter_spec(self, filter_spec: str) -> Filter | None:
        """Parse individual filter specification."""
        parts = filter_spec.split(":", 2)
        if len(parts) < 2:
            return None

        field = parts[0].strip()
        operator_str = parts[1].strip().lower()
        value_str = parts[2].strip() if len(parts) > 2 else ""

        # Map string operators to enum
        operator_map = {
            "eq": FilterOperator.EQ,
            "equal": FilterOperator.EQ,
            "ne": FilterOperator.NE,
            "not_equal": FilterOperator.NE,
            "gt": FilterOperator.GT,
            "greater": FilterOperator.GT,
            "gte": FilterOperator.GTE,
            "greater_equal": FilterOperator.GTE,
            "lt": FilterOperator.LT,
            "less": FilterOperator.LT,
            "lte": FilterOperator.LTE,
            "less_equal": FilterOperator.LTE,
            "like": FilterOperator.LIKE,
            "ilike": FilterOperator.ILIKE,
            "in": FilterOperator.IN,
            "not_in": FilterOperator.NOT_IN,
            "is_null": FilterOperator.IS_NULL,
            "null": FilterOperator.IS_NULL,
            "is_not_null": FilterOperator.IS_NOT_NULL,
            "not_null": FilterOperator.IS_NOT_NULL,
            "between": FilterOperator.BETWEEN,
            "contains": FilterOperator.CONTAINS,
            "jsonb_path": FilterOperator.JSONB_PATH,
        }

        operator = operator_map.get(operator_str)
        if not operator:
            logger.warning(f"Unknown filter operator: {operator_str}")
            return None

        # Parse value based on operator
        return self._parse_filter_value(field, operator, value_str)

    def _parse_filter_value(
        self, field: str, operator: FilterOperator, value_str: str
    ) -> Filter | None:
        """Parse filter value based on operator type."""
        # URL decode the value
        value_str = unquote_plus(value_str)

        match operator:
            case FilterOperator.IS_NULL | FilterOperator.IS_NOT_NULL:
                # No value needed
                return Filter(field=field, operator=operator)

            case FilterOperator.IN | FilterOperator.NOT_IN:
                # Parse comma-separated values
                if not value_str:
                    return None
                values = [v.strip() for v in value_str.split(",") if v.strip()]
                return Filter(field=field, operator=operator, values=values)

            case FilterOperator.BETWEEN:
                # Parse two values separated by comma
                if not value_str:
                    return None
                parts = value_str.split(",", 1)
                if len(parts) != 2:
                    return None
                values = [parts[0].strip(), parts[1].strip()]
                return Filter(field=field, operator=operator, values=values)

            case FilterOperator.CONTAINS:
                # Try to parse as JSON for JSONB contains
                try:
                    import json

                    value = json.loads(value_str)
                except json.JSONDecodeError:
                    # Fallback to string value
                    value = value_str
                return Filter(field=field, operator=operator, value=value)

            case _:
                # Single value operators
                value = self._convert_value_type(value_str)
                return Filter(field=field, operator=operator, value=value)

    def _convert_value_type(self, value_str: str) -> Any:
        """Convert string value to appropriate type."""
        if not value_str:
            return value_str

        value_lower = value_str.lower()

        # Boolean values
        if value_lower in ("true", "yes", "1", "on"):
            return True
        elif value_lower in ("false", "no", "0", "off"):
            return False

        # None/null values
        if value_lower in ("null", "none", "nil"):
            return None

        # Try to parse as number
        try:
            if "." in value_str:
                return float(value_str)
            else:
                return int(value_str)
        except ValueError:
            pass

        # Return as string
        return value_str


class SearchParams(BaseModel):
    """Search query parameters."""

    search: str | None = Field(default=None, description="Full-text search term", min_length=1)
    include_deleted: bool = Field(default=False, description="Include soft-deleted items")


class QueryParams(PaginationParams, SortParams, FilterParams, SearchParams):
    """Combined query parameters for listing endpoints."""

    def to_query_options(self) -> QueryOptions:
        """Convert to QueryOptions object."""
        return QueryOptions(
            filters=self.to_filters(),
            sorts=self.to_sorts(),
            pagination=self.to_pagination(),
            search=self.search,
            include_soft_deleted=self.include_deleted,
        )


# FastAPI dependency factory functions
def create_pagination_dependency(
    default_page_size: int = 50, max_page_size: int = 1000
) -> type[PaginationParams]:
    """Create pagination dependency with custom defaults."""

    class CustomPaginationParams(PaginationParams):
        page_size: int = Field(
            default=default_page_size, ge=1, le=max_page_size, description="Page size"
        )
        max_page_size: int = Field(default=max_page_size, ge=1)

    return CustomPaginationParams


def create_query_dependency(
    default_page_size: int = 50,
    max_page_size: int = 1000,
    default_sorts: list[str] | None = None,
) -> type[QueryParams]:
    """Create query params dependency with custom defaults."""

    class CustomQueryParams(QueryParams):
        page_size: int = Field(
            default=default_page_size, ge=1, le=max_page_size, description="Page size"
        )
        max_page_size: int = Field(default=max_page_size, ge=1)
        sort: list[str] | None = Field(
            default=default_sorts or ["created_at:desc"], description="Sort fields"
        )

    return CustomQueryParams


# Common query dependencies for convenience
PaginationDep = PaginationParams
SortDep = SortParams
FilterDep = FilterParams
SearchDep = SearchParams
QueryDep = QueryParams


# Example usage in FastAPI endpoints:
"""
from fastapi import FastAPI, Depends
from core.query_params import QueryParams

app = FastAPI()

@app.get("/items")
async def list_items(
    query: QueryParams = Depends(),
    site_id: UUID = Depends(site_id_dep),
):
    options = query.to_query_options()
    async with read_uow(site_id) as session:
        result = await item_repository.list(session, site_id, options)
    return result

# Or with custom defaults:
CustomQuery = create_query_dependency(
    default_page_size=25,
    max_page_size=500,
    default_sorts=["name:asc", "created_at:desc"]
)

@app.get("/custom-items")
async def list_custom_items(
    query: CustomQuery = Depends(),
    site_id: UUID = Depends(site_id_dep),
):
    options = query.to_query_options()
    # ... rest of the endpoint
"""
