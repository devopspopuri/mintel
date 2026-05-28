"""staff assignments

Revision ID: 0006_staff_assignments
Revises: 0005_marketing_roles
Create Date: 2026-05-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0006_staff_assignments"
down_revision = "0005_marketing_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staff_region_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "region_id", name="uq_staff_region_assignment"),
    )
    op.create_index("ix_staff_region_assignments_region", "staff_region_assignments", ["region_id"])
    op.create_index("ix_staff_region_assignments_user", "staff_region_assignments", ["user_id"])

    op.create_table(
        "staff_marketing_role_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("marketing_role_id", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["marketing_role_id"], ["marketing_roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "marketing_role_id", name="uq_staff_marketing_role_assignment"),
    )
    op.create_index("ix_staff_marketing_role_assignments_role", "staff_marketing_role_assignments", ["marketing_role_id"])
    op.create_index("ix_staff_marketing_role_assignments_user", "staff_marketing_role_assignments", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_staff_marketing_role_assignments_user", table_name="staff_marketing_role_assignments")
    op.drop_index("ix_staff_marketing_role_assignments_role", table_name="staff_marketing_role_assignments")
    op.drop_table("staff_marketing_role_assignments")
    op.drop_index("ix_staff_region_assignments_user", table_name="staff_region_assignments")
    op.drop_index("ix_staff_region_assignments_region", table_name="staff_region_assignments")
    op.drop_table("staff_region_assignments")
