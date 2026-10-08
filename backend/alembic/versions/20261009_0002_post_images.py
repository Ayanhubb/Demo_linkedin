"""Store an optional image with a scheduled post.

Revision ID: 0002_post_images
Revises: 0001_initial
Create Date: 2026-10-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_post_images"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scheduled_posts", sa.Column("image_bytes", sa.LargeBinary(), nullable=True))
    op.add_column(
        "scheduled_posts",
        sa.Column("image_content_type", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_scheduled_posts_image_pair",
        "scheduled_posts",
        "(image_bytes IS NULL AND image_content_type IS NULL) OR "
        "(image_bytes IS NOT NULL AND image_content_type IS NOT NULL "
        "AND octet_length(image_bytes) BETWEEN 1 AND 5242880)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_scheduled_posts_image_pair", "scheduled_posts", type_="check")
    op.drop_column("scheduled_posts", "image_content_type")
    op.drop_column("scheduled_posts", "image_bytes")
