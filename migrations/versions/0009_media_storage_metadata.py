"""add media storage backend, checksum and object ETag

Revision ID: 0009_media_storage_metadata
Revises: 0008_add_groups
Create Date: 2026-10-27

Phase 14 moves media objects to MinIO while keeping the original local-disk
backend available for development and tests. These columns record which backend
owns an object, the SHA-256 of the accepted bytes and the backend ETag. Existing
rows are explicitly marked ``local`` so a mixed deployment remains inspectable.

The binary still lives only in object storage; no BLOB column is introduced.
The migration is additive and safe on a live table: the default backfills
existing rows, and the nullable metadata columns need no table rewrite.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_media_storage_metadata"
down_revision: str | None = "0008_add_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media",
        sa.Column(
            "storage_backend",
            sa.String(length=20),
            server_default="local",
            nullable=False,
        ),
    )
    op.add_column(
        "media",
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "media",
        sa.Column("etag", sa.String(length=128), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_media_storage_backend_allowed"),
        "media",
        "storage_backend IN ('local', 'minio')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_media_storage_backend_allowed"),
        "media",
        type_="check",
    )
    op.drop_column("media", "etag")
    op.drop_column("media", "checksum_sha256")
    op.drop_column("media", "storage_backend")
