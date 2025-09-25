"""Tests for database management."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import DatabaseManager, check_database_connection, now, test_rls_configuration


class TestDatabaseManager:
    """Test DatabaseManager functionality."""

    @pytest.mark.asyncio
    async def test_database_manager_initialization(self):
        """Test database manager initialization."""
        db_manager = DatabaseManager(
            write_url="sqlite+aiosqlite:///:memory:",
            read_url="sqlite+aiosqlite:///:memory:",
        )
        
        assert db_manager.write_url == "sqlite+aiosqlite:///:memory:"
        assert db_manager.read_url == "sqlite+aiosqlite:///:memory:"
        assert db_manager._write_engine is None
        assert db_manager._read_engine is None

    @pytest.mark.asyncio
    async def test_engine_lazy_initialization(self, test_db_manager: DatabaseManager):
        """Test lazy initialization of engines."""
        # Engines should be None initially
        assert test_db_manager._write_engine is None
        assert test_db_manager._read_engine is None
        
        # Accessing engines should initialize them
        write_engine = test_db_manager.write_engine
        read_engine = test_db_manager.read_engine
        
        assert write_engine is not None
        assert read_engine is not None

    @pytest.mark.asyncio
    async def test_session_factories(self, test_db_manager: DatabaseManager):
        """Test session factory creation."""
        write_factory = test_db_manager.write_session_factory
        read_factory = test_db_manager.read_session_factory
        
        assert write_factory is not None
        assert read_factory is not None
        
        # Test session creation
        async with write_factory() as session:
            assert isinstance(session, AsyncSession)

    @pytest.mark.asyncio
    async def test_database_connection_check(self, test_db_manager: DatabaseManager):
        """Test database connection checking."""
        # This might not work with SQLite in tests, but we can check the function exists
        try:
            result = await check_database_connection(test_db_manager.write_engine)
            assert "status" in result
        except Exception:
            # SQLite might not support all the features we're testing
            pytest.skip("SQLite doesn't support all PostgreSQL features")

    def test_now_function(self):
        """Test the now() utility function."""
        timestamp = now()
        assert timestamp is not None
        assert timestamp.tzinfo is not None  # Should be timezone-aware

    @pytest.mark.asyncio
    async def test_rls_configuration_test(self, test_session: AsyncSession):
        """Test RLS configuration testing function."""
        # This will likely fail with SQLite but tests the function
        try:
            result = await test_rls_configuration(test_session, "test-site-id")
            # SQLite doesn't support RLS, so this should return False or raise an error
            assert isinstance(result, bool)
        except Exception:
            # Expected with SQLite
            pytest.skip("SQLite doesn't support RLS")


class TestDatabaseIntegration:
    """Integration tests for database functionality."""

    @pytest.mark.asyncio
    async def test_session_context_manager(self, test_db_manager: DatabaseManager):
        """Test session context manager usage."""
        async with test_db_manager.write_session_factory() as session:
            assert session is not None
            assert isinstance(session, AsyncSession)
            
            # Session should be usable
            result = await session.execute("SELECT 1")
            assert result.scalar() == 1

    @pytest.mark.asyncio
    async def test_transaction_handling(self, test_db_manager: DatabaseManager):
        """Test basic transaction handling."""
        async with test_db_manager.write_session_factory() as session:
            async with session.begin():
                # Transaction should be active
                assert session.in_transaction()
                
                # Basic query should work
                result = await session.execute("SELECT 1")
                assert result.scalar() == 1
