"""consultant profiles

Revision ID: 0008_consultant_profiles
Revises: 0007_staff_account_fields
Create Date: 2026-05-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0008_consultant_profiles"
down_revision = "0007_staff_account_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consultant_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("current_location", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("work_authorization", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("years_experience", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("marketing_role_id", sa.Integer(), nullable=True),
        sa.Column("primary_skills", sa.Text(), nullable=False, server_default=""),
        sa.Column("resume_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("availability", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["marketing_role_id"], ["marketing_roles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_consultant_profiles_email"),
    )
    op.create_index("ix_consultant_profiles_active", "consultant_profiles", ["active"])
    op.create_index("ix_consultant_profiles_email", "consultant_profiles", ["email"])
    op.create_index("ix_consultant_profiles_marketing_role_id", "consultant_profiles", ["marketing_role_id"])


def downgrade() -> None:
    op.drop_index("ix_consultant_profiles_marketing_role_id", table_name="consultant_profiles")
    op.drop_index("ix_consultant_profiles_email", table_name="consultant_profiles")
    op.drop_index("ix_consultant_profiles_active", table_name="consultant_profiles")
    op.drop_table("consultant_profiles")
