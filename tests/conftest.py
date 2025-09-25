"""Test configuration and fixtures."""

import asyncio
import os
from typing import AsyncGenerator, Generator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from core.db import DatabaseManager, init_database
from core.idempotency import init_idempotency_manager
from core.models import Base
from core.outbox import init_outbox_manager
from core.site_resolver import Site, init_site_resolver
from core.uow import init_uow_manager


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create test database engine."""
    # Use in-memory SQLite for tests
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
        connect_args={
            "check_same_thread": False,
        },
    )
    
    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    await engine.dispose()


@pytest_asyncio.fixture
async def test_db_manager(test_engine: AsyncEngine) -> AsyncGenerator[DatabaseManager, None]:
    """Create test database manager."""
    # Create a test database manager
    db_manager = DatabaseManager(
        write_url="sqlite+aiosqlite:///:memory:",
        read_url=None,  # Use same as write for tests
    )
    
    # Override engines with test engine
    db_manager._write_engine = test_engine
    db_manager._read_engine = test_engine
    
    yield db_manager
    
    await db_manager.close()


@pytest_asyncio.fixture
async def test_session(test_db_manager: DatabaseManager) -> AsyncGenerator[AsyncSession, None]:
    """Create test database session."""
    async with test_db_manager.write_session_factory() as session:
        yield session


@pytest.fixture
def test_site() -> Site:
    """Create a test site."""
    site_id = uuid4()
    return Site(
        id=site_id,
        uid="test-site",
        name="Test Site",
        host="test.example.com",
        created_at=None,  # Will be set by database
    )


@pytest_asyncio.fixture
async def initialized_core(test_db_manager: DatabaseManager) -> AsyncGenerator[None, None]:
    """Initialize core components with test database."""
    # Initialize global components
    init_database(
        write_url="sqlite+aiosqlite:///:memory:",
        read_url=None,
    )
    
    # Override with test manager
    import core.db
    core.db.db_manager = test_db_manager
    
    # Initialize other components
    init_site_resolver(cache_ttl=60)
    init_uow_manager()
    init_idempotency_manager()
    init_outbox_manager()
    
    yield
    
    # Cleanup
    core.db.db_manager = None


@pytest.fixture
def anyio_backends():
    """Configure anyio backends for pytest-asyncio."""
    return ["asyncio"]


# Test data fixtures

@pytest.fixture
def sample_page_data() -> dict:
    """Sample page data for testing."""
    return {
        "id": str(uuid4()),
        "slug": "test-page",
        "title": "Test Page",
        "status": "draft",
        "layout": "default",
        "sections": [
            {
                "type": "Hero",
                "data": {
                    "title": "Welcome to Test Page",
                    "subtitle": "This is a test page",
                }
            },
            {
                "type": "Text",
                "data": {
                    "content": "This is some test content."
                }
            }
        ],
        "overrides": {},
        "version": 1,
    }


@pytest.fixture
def sample_audit_data() -> dict:
    """Sample audit data for testing."""
    return {
        "before": {
            "title": "Old Title",
            "status": "draft",
        },
        "after": {
            "title": "New Title", 
            "status": "published",
        },
    }


# Environment setup

@pytest.fixture(autouse=True)
def setup_test_env():
    """Setup test environment variables."""
    os.environ.setdefault("DATABASE_WRITE_URL", "sqlite+aiosqlite:///:memory:")
    os.environ.setdefault("DATABASE_READ_URL", "sqlite+aiosqlite:///:memory:")
    os.environ.setdefault("LOG_LEVEL", "DEBUG")
    os.environ.setdefault("SITE_CACHE_TTL", "60")


# Async test helpers

class AsyncMock:
    """Simple async mock for testing."""
    
    def __init__(self, return_value=None):
        self.return_value = return_value
        self.call_count = 0
        self.call_args_list = []
    
    async def __call__(self, *args, **kwargs):
        self.call_count += 1
        self.call_args_list.append((args, kwargs))
        if callable(self.return_value):
            return self.return_value(*args, **kwargs)
        return self.return_value
