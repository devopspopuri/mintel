"""pursuit managed notes

Revision ID: 0018_pursuit_managed_notes
Revises: 0017_region_groups
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_pursuit_managed_notes"
down_revision = "0017_region_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pursuit_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=False),
        sa.Column("author", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=60), nullable=False, server_default="owner_note"),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["pursuit_id"], ["company_pursuits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pursuit_notes_active", "pursuit_notes", ["active"])
    op.create_index("ix_pursuit_notes_pursuit_id", "pursuit_notes", ["pursuit_id"])
    op.create_index("ix_pursuit_notes_pursuit_created", "pursuit_notes", ["pursuit_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_pursuit_notes_pursuit_created", table_name="pursuit_notes")
    op.drop_index("ix_pursuit_notes_pursuit_id", table_name="pursuit_notes")
    op.drop_index("ix_pursuit_notes_active", table_name="pursuit_notes")
    op.drop_table("pursuit_notes")
