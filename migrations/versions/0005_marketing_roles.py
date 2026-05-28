"""marketing roles

Revision ID: 0005_marketing_roles
Revises: 0004_pursuit_intel
Create Date: 2026-05-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_marketing_roles"
down_revision = "0004_pursuit_intel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketing_roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("covers", sa.Text(), nullable=False, server_default=""),
        sa.Column("common_tools", sa.Text(), nullable=False, server_default=""),
        sa.Column("aliases", sa.Text(), nullable=False, server_default=""),
        sa.Column("keywords", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_marketing_roles_code"),
    )
    op.create_index("ix_marketing_roles_active", "marketing_roles", ["active"])
    op.add_column("pursuit_requirements", sa.Column("marketing_role_id", sa.Integer(), nullable=True))
    op.create_index("ix_pursuit_requirements_marketing_role_id", "pursuit_requirements", ["marketing_role_id"])
    op.create_foreign_key("fk_pursuit_requirements_marketing_role_id", "pursuit_requirements", "marketing_roles", ["marketing_role_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_pursuit_requirements_marketing_role_id", "pursuit_requirements", type_="foreignkey")
    op.drop_index("ix_pursuit_requirements_marketing_role_id", table_name="pursuit_requirements")
    op.drop_column("pursuit_requirements", "marketing_role_id")
    op.drop_index("ix_marketing_roles_active", table_name="marketing_roles")
    op.drop_table("marketing_roles")
