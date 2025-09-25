"""Basic usage example of FA Service Core library."""

import asyncio
import logging
import os
from uuid import uuid4

from core.audit import AuditManager
from core.db import init_database
from core.idempotency import init_idempotency_manager
from core.models import Site, Page, PageStatus
from core.observability import init_application_info
from core.outbox import init_outbox_manager
# Projector imports removed - should be used in projection microservice
from core.site_resolver import init_site_resolver
from core.uow import init_uow_manager, write_uow, read_uow
from core.versions import VersionManager

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def setup_core_components():
    """Initialize all core components."""
    logger.info("Setting up core components...")
    
    # Database
    init_database(
        write_url=os.getenv("DATABASE_WRITE_URL", "postgresql+asyncpg://fa_user:fa_password@localhost:5432/fa_cms"),
        read_url=os.getenv("DATABASE_READ_URL", "postgresql+asyncpg://fa_user:fa_password@localhost:5433/fa_cms"),
    )
    
    # Site resolver
    init_site_resolver(cache_ttl=60)
    
    # Unit of Work
    init_uow_manager()
    
    # Idempotency
    init_idempotency_manager()
    
    # Outbox
    init_outbox_manager()
    
    try:
        init_application_info(
            app_name="FA Service Core Example",
            app_version="0.1.0",
            build_info={"environment": "development"}
        )
        logger.info("Observability initialized")
    except Exception as e:
        logger.info(f"Observability not available: {e}")
    
    logger.info("Core components initialized")


async def create_example_site() -> Site:
    """Create an example site."""
    site_id = uuid4()
    
    async with write_uow(site_id) as session:
        # Create site
        site = Site(
            id=site_id,
            uid="example-site",
            name="Example Site",
            host="example.com",
        )
        
        session.add(site)
        await session.flush()
        
        logger.info(f"Created site: {site.uid} ({site.id})")
        return site


async def create_example_page(site: Site) -> Page:
    """Create an example page."""
    async with write_uow(site.id) as session:
        # Create page
        page = Page(
            site_id=site.id,
            slug="welcome",
            title="Welcome to Example Site",
            status=PageStatus.DRAFT,
            layout="default",
            sections=[
                {
                    "type": "example_section",
                    "data": {
                        "title": "Welcome!",
                        "content": "This is an example page content"
                    }
                }
            ],
            overrides={"theme": "light"},
        )
        
        session.add(page)
        await session.flush()
        
        # Record version
        await PageVersionManager.create_version(
            session=session,
            resource_id=page.id,
            site_id=site.id,
            data={
                "title": page.title,
                "slug": page.slug,
                "status": page.status.value,
                "layout": page.layout,
                "sections": page.sections,
                "overrides": page.overrides,
            },
        )
        
        # Record audit
        await AuditManager.record_create(
            session=session,
            site_id=site.id,
            user_id=None,
            resource="pages",
            resource_id=page.id,
            data={
                "title": page.title,
                "slug": page.slug,
                "status": page.status.value,
            },
        )
        
        logger.info(f"Created page: {page.slug} ({page.id})")
        return page


async def update_example_page(site: Site, page: Page):
    """Update the example page."""
    async with write_uow(site.id) as session:
        # Get the page (it will be filtered by RLS automatically)
        from sqlalchemy import select
        
        result = await session.execute(
            select(Page).where(Page.id == page.id)
        )
        page_to_update = result.scalar_one()
        
        # Store before state for audit
        before_state = {
            "title": page_to_update.title,
            "status": page_to_update.status.value,
            "version": page_to_update.version,
        }
        
        # Update page
        page_to_update.title = "Updated Welcome Page"
        page_to_update.status = PageStatus.PUBLISHED
        page_to_update.version += 1
        
        await session.flush()
        
        # After state for audit
        after_state = {
            "title": page_to_update.title,
            "status": page_to_update.status.value,
            "version": page_to_update.version,
        }
        
        # Record version
        await PageVersionManager.create_version(
            session=session,
            resource_id=page_to_update.id,
            site_id=site.id,
            data={
                "title": page_to_update.title,
                "slug": page_to_update.slug,
                "status": page_to_update.status.value,
                "layout": page_to_update.layout,
                "sections": page_to_update.sections,
                "overrides": page_to_update.overrides,
            },
        )
        
        # Record audit
        await AuditManager.record_update(
            session=session,
            site_id=site.id,
            user_id=None,
            resource="pages",
            resource_id=page_to_update.id,
            version=page_to_update.version,
            before=before_state,
            after=after_state,
        )
        
        logger.info(f"Updated page: {page_to_update.slug} (version {page_to_update.version})")


async def read_example_data(site: Site, page: Page):
    """Read data using read UoW."""
    async with read_uow(site.id) as session:
        from sqlalchemy import select
        
        # Read site
        result = await session.execute(
            select(Site).where(Site.id == site.id)
        )
        site_data = result.scalar_one()
        logger.info(f"Read site: {site_data.name}")
        
        # Read page
        result = await session.execute(
            select(Page).where(Page.id == page.id)
        )
        page_data = result.scalar_one()
        logger.info(f"Read page: {page_data.title} (status: {page_data.status.value})")
        
        # Read page versions
        versions = await PageVersionManager.list_versions(
            session=session,
            resource_id=page.id,
            limit=10,
        )
        logger.info(f"Page has {len(versions)} versions")


async def demonstrate_outbox_pattern(site: Site, page: Page):
    """Demonstrate outbox pattern usage."""
    from core.outbox import get_outbox_manager, enqueue_domain_event
    
    async with write_uow(site.id) as session:
        # Enqueue a domain event
        event = await enqueue_domain_event(
            session=session,
            site_id=site.id,
            aggregate="pages",
            aggregate_id=page.id,
            event_name="published",
            data={
                "title": "Updated Welcome Page",
                "slug": "welcome",
                "publish_at": "2023-01-01T00:00:00Z",
            },
            version=2,
        )
        
        logger.info(f"Enqueued outbox event: {event.event_type} (ID: {event.id})")
        
        # Fetch unprocessed events
        outbox_manager = get_outbox_manager()
        events = await outbox_manager.fetch_batch(session, batch_size=10)
        logger.info(f"Found {len(events)} unprocessed events")
        
        # Mark events as processed (in real app, this would be done by projector)
        if events:
            event_ids = [e.id for e in events]
            processed = await outbox_manager.mark_processed(session, event_ids)
            logger.info(f"Marked {processed} events as processed")


async def main():
    """Main example function."""
    logger.info("Starting FA Service Core example...")
    
    try:
        # Setup
        await setup_core_components()
        
        # Create example data
        site = await create_example_site()
        page = await create_example_page(site)
        
        # Update data
        await update_example_page(site, page)
        
        # Read data
        await read_example_data(site, page)
        
        # Demonstrate outbox
        await demonstrate_outbox_pattern(site, page)
        
        logger.info("Example completed successfully!")
        
    except Exception as e:
        logger.error(f"Example failed: {e}", exc_info=True)
        raise
    
    finally:
        # Cleanup
        from core.db import get_db_manager
        try:
            db_manager = get_db_manager()
            await db_manager.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
