"""Site resolution and caching logic."""

import asyncio
import logging
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db_manager

logger = logging.getLogger(__name__)


class Site:
    """Site model for resolved site information."""

    def __init__(
        self,
        id: UUID,
        uid: str,
        name: str,
        host: str,
        created_at: datetime,
    ) -> None:
        self.id = id
        self.uid = uid
        self.name = name
        self.host = host
        self.created_at = created_at

    def __str__(self) -> str:
        return f"Site(uid={self.uid}, host={self.host})"

    def __repr__(self) -> str:
        return f"Site(id={self.id}, uid={self.uid}, name={self.name}, host={self.host})"

    def to_dict(self) -> dict[str, str | UUID | datetime]:
        """Convert site to dictionary."""
        return {
            "id": self.id,
            "uid": self.uid,
            "name": self.name,
            "host": self.host,
            "created_at": self.created_at,
        }


class SiteCache:
    """Simple in-memory cache for site resolution."""

    def __init__(self, ttl_seconds: int = 60) -> None:
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[Site, datetime]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Site | None:
        """Get site from cache if not expired."""
        async with self._lock:
            if key in self._cache:
                site, cached_at = self._cache[key]
                if datetime.now() - cached_at < timedelta(seconds=self.ttl_seconds):
                    return site
                else:
                    # Remove expired entry
                    del self._cache[key]
            return None

    async def set(self, key: str, site: Site) -> None:
        """Set site in cache."""
        async with self._lock:
            self._cache[key] = (site, datetime.now())

    async def clear(self) -> None:
        """Clear all cached entries."""
        async with self._lock:
            self._cache.clear()

    async def remove(self, key: str) -> None:
        """Remove specific key from cache."""
        async with self._lock:
            self._cache.pop(key, None)


class SiteResolver:
    """Resolves sites by host with caching."""

    def __init__(self, cache_ttl: int = 60) -> None:
        self.cache = SiteCache(cache_ttl)

    async def by_host(self, host: str) -> Site | None:
        """Resolve site by host with caching.

        Args:
            host: The hostname to resolve

        Returns:
            Site object if found, None otherwise
        """
        # Check cache first
        cached_site = await self.cache.get(host)
        if cached_site:
            logger.debug(
                "Site resolved from cache", extra={"host": host, "site_uid": cached_site.uid}
            )
            return cached_site

        # Query database
        db_manager = get_db_manager()
        async with db_manager.read_session_factory() as session:
            site = await self._query_site_by_host(session, host)

            if site:
                # Cache the result
                await self.cache.set(host, site)
                logger.info(
                    "Site resolved from database",
                    extra={"host": host, "site_uid": site.uid, "site_id": str(site.id)},
                )
            else:
                logger.warning("Site not found", extra={"host": host})

            return site

    async def by_id(self, site_id: UUID) -> Site | None:
        """Resolve site by ID.

        Args:
            site_id: The site UUID

        Returns:
            Site object if found, None otherwise
        """
        # For ID lookups, we could cache by ID too, but for now query directly
        db_manager = get_db_manager()
        async with db_manager.read_session_factory() as session:
            return await self._query_site_by_id(session, site_id)

    async def invalidate_cache(self, host: str | None = None) -> None:
        """Invalidate cache entries.

        Args:
            host: Specific host to invalidate, or None to clear all
        """
        if host:
            await self.cache.remove(host)
            logger.info("Cache invalidated for host", extra={"host": host})
        else:
            await self.cache.clear()
            logger.info("Cache cleared")

    async def _query_site_by_host(self, session: AsyncSession, host: str) -> Site | None:
        """Query site by host from database."""
        query = text(
            """
            SELECT id, uid, name, host, created_at 
            FROM sites 
            WHERE host = :host
        """
        )

        result = await session.execute(query, {"host": host})
        row = result.fetchone()

        if row:
            return Site(
                id=row.id,
                uid=row.uid,
                name=row.name,
                host=row.host,
                created_at=row.created_at,
            )
        return None

    async def _query_site_by_id(self, session: AsyncSession, site_id: UUID) -> Site | None:
        """Query site by ID from database."""
        query = text(
            """
            SELECT id, uid, name, host, created_at 
            FROM sites 
            WHERE id = :site_id
        """
        )

        result = await session.execute(query, {"site_id": site_id})
        row = result.fetchone()

        if row:
            return Site(
                id=row.id,
                uid=row.uid,
                name=row.name,
                host=row.host,
                created_at=row.created_at,
            )
        return None


# Global resolver instance
site_resolver: SiteResolver | None = None


def init_site_resolver(cache_ttl: int = 60) -> SiteResolver:
    """Initialize global site resolver.

    Args:
        cache_ttl: Cache TTL in seconds

    Returns:
        Initialized SiteResolver instance
    """
    global site_resolver
    site_resolver = SiteResolver(cache_ttl)
    return site_resolver


def get_site_resolver() -> SiteResolver:
    """Get global site resolver instance.

    Raises:
        RuntimeError: If site resolver is not initialized

    Returns:
        SiteResolver instance
    """
    if site_resolver is None:
        raise RuntimeError("Site resolver not initialized. Call init_site_resolver() first.")
    return site_resolver


async def resolve_site_id_from_request(request: Request) -> UUID:
    """Resolve site ID from FastAPI request.

    Checks for site_id in:
    1. X-Site-ID header
    2. Host header (hostname resolution) - if resolver is available

    Args:
        request: FastAPI request object

    Returns:
        Site UUID

    Raises:
        HTTPException: If site cannot be resolved
    """
    # Try X-Site-ID header first - most direct
    site_id_header = request.headers.get("X-Site-ID")
    if site_id_header:
        try:
            site_id = UUID(site_id_header)
            logger.debug("Site ID from X-Site-ID header", extra={"site_id": str(site_id)})
            return site_id
        except ValueError:
            logger.warning("Invalid X-Site-ID header format", extra={"header": site_id_header})

    # Try host resolution (if resolver is available)
    try:
        resolver = get_site_resolver()
        host = request.headers.get("Host")
        if host:
            # Remove port if present
            hostname = host.split(":")[0]
            site = await resolver.by_host(hostname)
            if site:
                logger.debug(
                    "Site ID from host resolution",
                    extra={"host": hostname, "site_id": str(site.id)},
                )
                return site.id
    except RuntimeError:
        # Site resolver not initialized - skip host resolution
        pass

    # Site not found
    raise HTTPException(
        status_code=404,
        detail="Site not found. Provide valid X-Site-ID header or valid hostname.",
    )


# FastAPI dependency for site ID (most common use case)
async def site_id_dep(request: Request) -> UUID:
    """FastAPI dependency to resolve site ID from request.

    Args:
        request: FastAPI request object

    Returns:
        Site UUID
    """
    return await resolve_site_id_from_request(request)


# FastAPI dependency for full site object (for sites microservice)
async def site_dep(request: Request) -> Site:
    """FastAPI dependency to resolve full site object from request.

    This is mainly for sites microservice that needs full site data.
    Other microservices should use site_id_dep instead.

    Args:
        request: FastAPI request object

    Returns:
        Resolved Site object
    """
    resolver = get_site_resolver()
    site_id = await resolve_site_id_from_request(request)
    site = await resolver.by_id(site_id)

    if not site:
        raise HTTPException(status_code=404, detail=f"Site with ID {site_id} not found")

    return site
