"""Initial users, LinkedIn accounts, and scheduled posts.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    post_status = postgresql.ENUM(
        "SCHEDULED",
        "PROCESSING",
        "PUBLISHED",
        "FAILED",
        name="post_status",
        create_type=False,
    )
    post_status.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "linkedin_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("linkedin_member_id", sa.String(length=255), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_linkedin_accounts_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("id", "user_id", name="uq_linkedin_accounts_id_user"),
        sa.UniqueConstraint(
            "user_id",
            "linkedin_member_id",
            name="uq_linkedin_accounts_user_member",
        ),
    )
    op.create_index("ix_linkedin_accounts_user_id", "linkedin_accounts", ["user_id"])
    op.create_table(
        "scheduled_posts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("linkedin_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            post_status,
            nullable=False,
            server_default=sa.text("'SCHEDULED'"),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("linkedin_post_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 3000",
            name="ck_scheduled_posts_content_length",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_scheduled_posts_attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name="ck_scheduled_posts_max_attempts_positive",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_scheduled_posts_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["linkedin_account_id"],
            ["linkedin_accounts.id"],
            name="fk_scheduled_posts_linkedin_account_id_linkedin_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["linkedin_account_id", "user_id"],
            ["linkedin_accounts.id", "linkedin_accounts.user_id"],
            name="fk_scheduled_posts_account_same_user",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_scheduled_posts_status_scheduled_at",
        "scheduled_posts",
        ["status", "scheduled_at"],
    )
    op.create_index(
        "ix_scheduled_posts_linkedin_account_id",
        "scheduled_posts",
        ["linkedin_account_id"],
    )
    op.create_index(
        "ix_scheduled_posts_idempotency_key",
        "scheduled_posts",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index("ix_scheduled_posts_user_id", "scheduled_posts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_posts_user_id", table_name="scheduled_posts")
    op.drop_index("ix_scheduled_posts_idempotency_key", table_name="scheduled_posts")
    op.drop_index("ix_scheduled_posts_linkedin_account_id", table_name="scheduled_posts")
    op.drop_index("ix_scheduled_posts_status_scheduled_at", table_name="scheduled_posts")
    op.drop_table("scheduled_posts")
    op.drop_index("ix_linkedin_accounts_user_id", table_name="linkedin_accounts")
    op.drop_table("linkedin_accounts")
    op.drop_table("users")
    postgresql.ENUM(name="post_status").drop(op.get_bind(), checkfirst=False)
