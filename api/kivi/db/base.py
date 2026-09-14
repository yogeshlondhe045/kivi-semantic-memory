"""Declarative base and shared column helpers."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


def created_at() -> Mapped[datetime]:
    return mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def pg_enum(enum_cls, name: str) -> sa.Enum:
    """A Postgres ENUM whose stored labels are the enum *values*.

    Storing values rather than Python member names keeps the database readable
    on its own: a reviewer running psql sees 'dictation_content_not_assertion',
    not 'DICTATION_CONTENT_NOT_ASSERTION'.
    """
    return sa.Enum(
        enum_cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
    )
