"""consultant basics completion

Revision ID: 0024_basics_completion
Revises: 0023_consultant_role_journeys
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_basics_completion"
down_revision = "0023_consultant_role_journeys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("consultant_profiles", sa.Column("basics_prep_complete", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("consultant_profiles", "basics_prep_complete")
