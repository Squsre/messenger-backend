"""create users and token_blocklist tables

Revision ID: 0002_create_users_and_token_blocklist
Revises: 0001_create_health_checks
Create Date: 2026-10-18

Adds the authentication tables:

* ``users``           — accounts, with a unique lower-cased email and an
  Argon2id password hash.
* ``token_blocklist`` — revoked access-token ids, used to implement logout.

Uniqueness for ``users.email`` and ``token_blocklist.jti`` is enforced with a
*unique index* (``unique=True, index=True`` on the model), which serves both the
uniqueness guarantee and the lookup path with a single index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_create_users_and_token_blocklist"
down_revision: str | None = "0001_create_health_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "token_blocklist",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("token_type", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_token_blocklist_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_token_blocklist")),
    )
    op.create_index(op.f("ix_token_blocklist_jti"), "token_blocklist", ["jti"], unique=True)
    op.create_index(
        op.f("ix_token_blocklist_user_id"), "token_blocklist", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_token_blocklist_expires_at"), "token_blocklist", ["expires_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_token_blocklist_expires_at"), table_name="token_blocklist")
    op.drop_index(op.f("ix_token_blocklist_user_id"), table_name="token_blocklist")
    op.drop_index(op.f("ix_token_blocklist_jti"), table_name="token_blocklist")
    op.drop_table("token_blocklist")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
