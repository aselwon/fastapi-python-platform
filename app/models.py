import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


def new_id() -> str:
    return str(uuid.uuid4())


class Role(enum.StrEnum):
    admin = "admin"
    agent = "agent"
    viewer = "viewer"


class TicketStatus(enum.StrEnum):
    open = "open"
    in_progress = "in_progress"
    closed = "closed"


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Tenant(Timestamps, Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))


class User(Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_users_tenant_id_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Ticket(Timestamps, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tickets_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "assignee_id"],
            ["users.tenant_id", "users.id"],
            name="fk_tickets_assignee_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name="fk_tickets_creator_tenant",
        ),
        Index("ix_tickets_tenant_active_created", "tenant_id", "deleted_at", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=TicketStatus.open.value)
    assignee_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(String(36))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Comment(Timestamps, Base):
    __tablename__ = "comments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "ticket_id"],
            ["tickets.tenant_id", "tickets.id"],
            name="fk_comments_ticket_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name="fk_comments_creator_tenant",
        ),
        Index("ix_comments_tenant_ticket", "tenant_id", "ticket_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    ticket_id: Mapped[str] = mapped_column(String(36))
    body: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(36))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
