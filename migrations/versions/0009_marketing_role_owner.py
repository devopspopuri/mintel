"""marketing role owner

Revision ID: 0009_marketing_role_owner
Revises: 0008_consultant_profiles
Create Date: 2026-05-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0009_marketing_role_owner"
down_revision = "0008_consultant_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("marketing_roles", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_marketing_roles_owner_id_users", "marketing_roles", "users", ["owner_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_marketing_roles_owner_id", "marketing_roles", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_marketing_roles_owner_id", table_name="marketing_roles")
    op.drop_constraint("fk_marketing_roles_owner_id_users", "marketing_roles", type_="foreignkey")
    op.drop_column("marketing_roles", "owner_id")
