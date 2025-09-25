"""Repository pattern with filtering and pagination for SQLAlchemy."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import and_, desc, func, or_, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import Select

logger = logging.getLogger(__name__)

# Type variables
T = TypeVar("T", bound=DeclarativeBase)


class SortDirection(Enum):
    """Sort direction enum."""

    ASC = "asc"
    DESC = "desc"


class FilterOperator(Enum):
    """Filter operators."""

    EQ = "eq"  # Equal
    NE = "ne"  # Not equal
    GT = "gt"  # Greater than
    GTE = "gte"  # Greater than or equal
    LT = "lt"  # Less than
    LTE = "lte"  # Less than or equal
    LIKE = "like"  # SQL LIKE
    ILIKE = "ilike"  # Case insensitive LIKE
    IN = "in"  # IN list
    NOT_IN = "not_in"  # NOT IN list
    IS_NULL = "is_null"  # IS NULL
    IS_NOT_NULL = "is_not_null"  # IS NOT NULL
    BETWEEN = "between"  # BETWEEN two values
    CONTAINS = "contains"  # JSON contains (PostgreSQL)
    JSONB_PATH = "jsonb_path"  # JSONPath query (PostgreSQL)


@dataclass
class Filter:
    """Single filter condition."""

    field: str
    operator: FilterOperator
    value: Any = None
    values: list[Any] | None = None  # For IN, NOT_IN, BETWEEN


@dataclass
class Sort:
    """Sort specification."""

    field: str
    direction: SortDirection = SortDirection.ASC


@dataclass
class Pagination:
    """Pagination specification."""

    page: int = 1
    page_size: int = 50
    max_page_size: int = 1000

    def __post_init__(self) -> None:
        """Validate pagination parameters."""
        if self.page < 1:
            self.page = 1
        if self.page_size < 1:
            self.page_size = 50
        if self.page_size > self.max_page_size:
            self.page_size = self.max_page_size

    @property
    def offset(self) -> int:
        """Calculate offset for SQL query."""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """Get limit for SQL query."""
        return self.page_size


@dataclass
class QueryOptions:
    """Complete query options."""

    filters: list[Filter] | None = None
    sorts: list[Sort] | None = None
    pagination: Pagination | None = None
    search: str | None = None  # Full-text search term
    include_soft_deleted: bool = False


@dataclass
class ListResult(Generic[T]):
    """Result of list query with pagination info."""

    items: list[T]
    total_count: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_prev: bool

    @classmethod
    def create(
        cls,
        items: list[T],
        total_count: int,
        pagination: Pagination,
    ) -> ListResult[T]:
        """Create ListResult from items and pagination info."""
        total_pages = (total_count + pagination.page_size - 1) // pagination.page_size
        return cls(
            items=items,
            total_count=total_count,
            page=pagination.page,
            page_size=pagination.page_size,
            total_pages=total_pages,
            has_next=pagination.page < total_pages,
            has_prev=pagination.page > 1,
        )


class BaseRepository(ABC, Generic[T]):
    """Abstract base repository with common patterns."""

    def __init__(self, model_class: type[T]) -> None:
        """Initialize repository with model class."""
        self.model_class = model_class

    @abstractmethod
    async def get_by_id(
        self,
        session: AsyncSession,
        site_id: UUID,
        entity_id: UUID,
    ) -> T | None:
        """Get entity by ID."""
        pass

    @abstractmethod
    async def list(
        self,
        session: AsyncSession,
        site_id: UUID,
        options: QueryOptions | None = None,
    ) -> ListResult[T]:
        """List entities with filtering, sorting, and pagination."""
        pass

    @abstractmethod
    async def create(
        self,
        session: AsyncSession,
        site_id: UUID,
        data: dict[str, Any],
    ) -> T:
        """Create new entity."""
        pass

    @abstractmethod
    async def update(
        self,
        session: AsyncSession,
        site_id: UUID,
        entity_id: UUID,
        data: dict[str, Any],
    ) -> T | None:
        """Update existing entity."""
        pass

    @abstractmethod
    async def delete(
        self,
        session: AsyncSession,
        site_id: UUID,
        entity_id: UUID,
        soft_delete: bool = True,
    ) -> bool:
        """Delete entity (soft or hard delete)."""
        pass

    def _build_base_query(self, session: AsyncSession, site_id: UUID) -> Select:
        """Build base query with site filtering."""
        return session.query(self.model_class).filter(self.model_class.site_id == site_id)

    def _apply_filters(self, query: Select, filters: list[Filter]) -> Select:
        """Apply filters to query."""
        conditions = []

        for filter_spec in filters:
            field_attr = getattr(self.model_class, filter_spec.field, None)
            if field_attr is None:
                logger.warning(f"Unknown field: {filter_spec.field}")
                continue

            condition = self._build_filter_condition(field_attr, filter_spec)
            if condition is not None:
                conditions.append(condition)

        if conditions:
            query = query.filter(and_(*conditions))

        return query

    def _build_filter_condition(self, field_attr: Any, filter_spec: Filter) -> Any:
        """Build individual filter condition."""
        op = filter_spec.operator
        value = filter_spec.value
        values = filter_spec.values

        match op:
            case FilterOperator.EQ:
                return field_attr == value
            case FilterOperator.NE:
                return field_attr != value
            case FilterOperator.GT:
                return field_attr > value
            case FilterOperator.GTE:
                return field_attr >= value
            case FilterOperator.LT:
                return field_attr < value
            case FilterOperator.LTE:
                return field_attr <= value
            case FilterOperator.LIKE:
                return field_attr.like(value)
            case FilterOperator.ILIKE:
                return field_attr.ilike(value)
            case FilterOperator.IN:
                return field_attr.in_(values or [])
            case FilterOperator.NOT_IN:
                return ~field_attr.in_(values or [])
            case FilterOperator.IS_NULL:
                return field_attr.is_(None)
            case FilterOperator.IS_NOT_NULL:
                return field_attr.is_not(None)
            case FilterOperator.BETWEEN:
                if values and len(values) >= 2:
                    return field_attr.between(values[0], values[1])
                return None
            case FilterOperator.CONTAINS:
                # PostgreSQL JSONB contains
                return field_attr.contains(value)
            case FilterOperator.JSONB_PATH:
                # PostgreSQL JSONPath query
                return text(f"{filter_spec.field} @? :jsonpath").bindparam(jsonpath=value)
            case _:
                logger.warning(f"Unknown filter operator: {op}")
                return None

    def _apply_sorts(self, query: Select, sorts: list[Sort]) -> Select:
        """Apply sorting to query."""
        for sort_spec in sorts:
            field_attr = getattr(self.model_class, sort_spec.field, None)
            if field_attr is None:
                logger.warning(f"Unknown sort field: {sort_spec.field}")
                continue

            if sort_spec.direction == SortDirection.DESC:
                query = query.order_by(desc(field_attr))
            else:
                query = query.order_by(field_attr)

        return query

    def _apply_search(self, query: Select, search_term: str) -> Select:
        """Apply full-text search. Override in subclasses for specific search logic."""
        # Default implementation - search in common text fields
        search_fields = self._get_search_fields()
        if not search_fields:
            return query

        search_conditions = []
        search_pattern = f"%{search_term}%"

        for field_name in search_fields:
            field_attr = getattr(self.model_class, field_name, None)
            if field_attr is not None:
                search_conditions.append(field_attr.ilike(search_pattern))

        if search_conditions:
            query = query.filter(or_(*search_conditions))

        return query

    def _get_search_fields(self) -> list[str]:
        """Get list of fields to search in. Override in subclasses."""
        # Common searchable field names
        common_fields = ["title", "name", "slug", "description", "content"]
        existing_fields = []

        for field_name in common_fields:
            if hasattr(self.model_class, field_name):
                existing_fields.append(field_name)

        return existing_fields

    def _apply_soft_delete_filter(self, query: Select, include_soft_deleted: bool) -> Select:
        """Apply soft delete filtering if model supports it."""
        if not include_soft_deleted and hasattr(self.model_class, "deleted_at"):
            query = query.filter(self.model_class.deleted_at.is_(None))
        return query

    async def _get_total_count(
        self,
        session: AsyncSession,
        base_query: Select,
    ) -> int:
        """Get total count for pagination."""
        count_query = base_query.with_only_columns(func.count())
        result = await session.execute(count_query)
        return result.scalar() or 0


class FilterBuilder:
    """Helper class for building filters."""

    @staticmethod
    def eq(field: str, value: Any) -> Filter:
        """Equal filter."""
        return Filter(field=field, operator=FilterOperator.EQ, value=value)

    @staticmethod
    def ne(field: str, value: Any) -> Filter:
        """Not equal filter."""
        return Filter(field=field, operator=FilterOperator.NE, value=value)

    @staticmethod
    def gt(field: str, value: Any) -> Filter:
        """Greater than filter."""
        return Filter(field=field, operator=FilterOperator.GT, value=value)

    @staticmethod
    def gte(field: str, value: Any) -> Filter:
        """Greater than or equal filter."""
        return Filter(field=field, operator=FilterOperator.GTE, value=value)

    @staticmethod
    def lt(field: str, value: Any) -> Filter:
        """Less than filter."""
        return Filter(field=field, operator=FilterOperator.LT, value=value)

    @staticmethod
    def lte(field: str, value: Any) -> Filter:
        """Less than or equal filter."""
        return Filter(field=field, operator=FilterOperator.LTE, value=value)

    @staticmethod
    def like(field: str, pattern: str) -> Filter:
        """LIKE filter."""
        return Filter(field=field, operator=FilterOperator.LIKE, value=pattern)

    @staticmethod
    def ilike(field: str, pattern: str) -> Filter:
        """Case insensitive LIKE filter."""
        return Filter(field=field, operator=FilterOperator.ILIKE, value=pattern)

    @staticmethod
    def in_(field: str, values: list[Any]) -> Filter:
        """IN filter."""
        return Filter(field=field, operator=FilterOperator.IN, values=values)

    @staticmethod
    def not_in(field: str, values: list[Any]) -> Filter:
        """NOT IN filter."""
        return Filter(field=field, operator=FilterOperator.NOT_IN, values=values)

    @staticmethod
    def is_null(field: str) -> Filter:
        """IS NULL filter."""
        return Filter(field=field, operator=FilterOperator.IS_NULL)

    @staticmethod
    def is_not_null(field: str) -> Filter:
        """IS NOT NULL filter."""
        return Filter(field=field, operator=FilterOperator.IS_NOT_NULL)

    @staticmethod
    def between(field: str, min_value: Any, max_value: Any) -> Filter:
        """BETWEEN filter."""
        return Filter(
            field=field,
            operator=FilterOperator.BETWEEN,
            values=[min_value, max_value],
        )

    @staticmethod
    def contains(field: str, value: dict[str, Any]) -> Filter:
        """JSONB contains filter (PostgreSQL)."""
        return Filter(field=field, operator=FilterOperator.CONTAINS, value=value)

    @staticmethod
    def jsonb_path(field: str, path_expression: str) -> Filter:
        """JSONPath filter (PostgreSQL)."""
        return Filter(field=field, operator=FilterOperator.JSONB_PATH, value=path_expression)


# Convenience functions for creating common objects
def paginate(page: int = 1, page_size: int = 50, max_page_size: int = 1000) -> Pagination:
    """Create pagination object."""
    return Pagination(page=page, page_size=page_size, max_page_size=max_page_size)


def sort_by(field: str, direction: SortDirection = SortDirection.ASC) -> Sort:
    """Create sort object."""
    return Sort(field=field, direction=direction)


def sort_desc(field: str) -> Sort:
    """Create descending sort."""
    return Sort(field=field, direction=SortDirection.DESC)


def sort_asc(field: str) -> Sort:
    """Create ascending sort."""
    return Sort(field=field, direction=SortDirection.ASC)


def query_options(
    filters: list[Filter] | None = None,
    sorts: list[Sort] | None = None,
    pagination: Pagination | None = None,
    search: str | None = None,
    include_soft_deleted: bool = False,
) -> QueryOptions:
    """Create query options object."""
    return QueryOptions(
        filters=filters,
        sorts=sorts,
        pagination=pagination,
        search=search,
        include_soft_deleted=include_soft_deleted,
    )
