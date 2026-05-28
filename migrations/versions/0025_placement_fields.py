"""placement fields

Revision ID: 0025_placement_fields
Revises: 0024_basics_completion
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_placement_fields"
down_revision = "0024_basics_completion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("consultant_profiles", sa.Column("placement_company", sa.String(length=180), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("placement_role", sa.String(length=180), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("placement_start_date", sa.Date(), nullable=True))
    op.add_column("consultant_profiles", sa.Column("placement_notes", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("consultant_profiles", "placement_notes")
    op.drop_column("consultant_profiles", "placement_start_date")
    op.drop_column("consultant_profiles", "placement_role")
    op.drop_column("consultant_profiles", "placement_company")
