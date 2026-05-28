"""region groups

Revision ID: 0017_region_groups
Revises: 0016_mock_interview_operations
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_region_groups"
down_revision = "0016_mock_interview_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "region_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_region_groups_name"),
    )
    op.create_index("ix_region_groups_active", "region_groups", ["active"])
    op.create_index("ix_region_groups_name", "region_groups", ["name"])

    op.create_table(
        "region_group_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["region_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "user_id", name="uq_region_group_member"),
    )
    op.create_index("ix_region_group_members_group", "region_group_members", ["group_id"])
    op.create_index("ix_region_group_members_user", "region_group_members", ["user_id"])

    op.create_table(
        "region_group_regions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["region_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "region_id", name="uq_region_group_region"),
    )
    op.create_index("ix_region_group_regions_group", "region_group_regions", ["group_id"])
    op.create_index("ix_region_group_regions_region", "region_group_regions", ["region_id"])


def downgrade() -> None:
    op.drop_index("ix_region_group_regions_region", table_name="region_group_regions")
    op.drop_index("ix_region_group_regions_group", table_name="region_group_regions")
    op.drop_table("region_group_regions")
    op.drop_index("ix_region_group_members_user", table_name="region_group_members")
    op.drop_index("ix_region_group_members_group", table_name="region_group_members")
    op.drop_table("region_group_members")
    op.drop_index("ix_region_groups_name", table_name="region_groups")
    op.drop_index("ix_region_groups_active", table_name="region_groups")
    op.drop_table("region_groups")
