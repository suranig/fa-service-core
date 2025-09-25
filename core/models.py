"""SQLAlchemy models for the core library."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Base class for all models."""

    pass



class AuditLog(Base):
    """Audit log for tracking changes."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    site_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sites.id", ondelete="SET NULL")
    )
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    resource: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    patch_json: Mapped[list | None] = mapped_column(JSONB)
    snapshot: Mapped[dict | None] = mapped_column(JSONB)
    meta: Mapped[dict | None] = mapped_column(JSONB)

    # Constraints
    __table_args__ = (
        Index("idx_audit_log_ts", "ts"),
        Index("idx_audit_log_site_id", "site_id"),
        Index("idx_audit_log_resource", "resource", "resource_id"),
        Index("idx_audit_log_user_id", "user_id"),
        Index("idx_audit_log_event_type", "event_type"),
    )


class OutboxEvent(Base):
    """Outbox events for reliable message publishing."""

    __tablename__ = "outbox_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    site_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    aggregate: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Constraints
    __table_args__ = (
        Index("idx_outbox_events_ts", "ts"),
        Index("idx_outbox_events_site_id", "site_id"),
        Index("idx_outbox_events_aggregate", "aggregate", "aggregate_id"),
        Index("idx_outbox_events_processed_at", "processed_at"),
        Index("idx_outbox_events_unprocessed", "ts", postgresql_where=text("processed_at IS NULL")),
    )



class IdempotencyKey(Base):
    """Idempotency keys for request deduplication."""

    __tablename__ = "idempotency_keys"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    status: Mapped[int] = mapped_column(Integer, nullable=False)
    headers: Mapped[dict | None] = mapped_column(JSONB)
    body: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Constraints
    __table_args__ = (
        Index("idx_idempotency_keys_created_at", "created_at"),
        Index("idx_idempotency_keys_status", "status"),
    )


# RLS Configuration for infrastructure tables only
RLS_POLICIES = """
-- Enable RLS on infrastructure tables only
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY;

-- Audit log policies (read: current site or global, write: current site only)
CREATE POLICY audit_log_select_policy ON audit_log
    FOR SELECT
    USING (site_id::text = current_setting('app.current_site', true) OR site_id IS NULL);

CREATE POLICY audit_log_modify_policy ON audit_log
    FOR INSERT
    WITH CHECK (site_id::text = current_setting('app.current_site', true));

-- Outbox events policies
CREATE POLICY outbox_events_policy ON outbox_events
    FOR ALL
    USING (site_id::text = current_setting('app.current_site', true))
    WITH CHECK (site_id::text = current_setting('app.current_site', true));

-- Bypass policies for superusers
CREATE POLICY audit_log_bypass_policy ON audit_log
    FOR ALL TO postgres USING (true) WITH CHECK (true);
    
CREATE POLICY outbox_events_bypass_policy ON outbox_events
    FOR ALL TO postgres USING (true) WITH CHECK (true);

-- Business domain table RLS policies should be defined in respective microservices
"""
