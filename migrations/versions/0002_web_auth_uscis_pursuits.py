"""web auth uscis pursuits

Revision ID: 0002_web_auth_uscis_pursuits
Revises: 0001_initial
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_web_auth_uscis_pursuits"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "regions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("staff_owner_name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("staff_owner_email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_regions_code", "regions", ["code"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("role", sa.String(length=40), nullable=False, server_default="viewer"),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_role", "users", ["role"])

    op.create_table(
        "uscis_employer_yearly_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("employer_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_employer_name", sa.String(length=255), nullable=False),
        sa.Column("tax_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("naics_code", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("naics_label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("petitioner_city", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("petitioner_state", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("petitioner_zip_code", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("new_employment_approval", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_employment_denial", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("continuation_approval", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("continuation_denial", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("change_same_employer_approval", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("change_same_employer_denial", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_concurrent_approval", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_concurrent_denial", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("change_employer_approval", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("change_employer_denial", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amended_approval", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amended_denial", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_approvals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_denials", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_decisions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_file", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fiscal_year", "normalized_employer_name", "tax_id", "naics_code", "petitioner_city", "petitioner_state", name="uq_uscis_employer_yearly_identity"),
    )
    op.create_index("ix_uscis_employer_yearly_stats_company_id", "uscis_employer_yearly_stats", ["company_id"])
    op.create_index("ix_uscis_employer_yearly_stats_employer_name", "uscis_employer_yearly_stats", ["employer_name"])
    op.create_index("ix_uscis_employer_yearly_stats_fiscal_year", "uscis_employer_yearly_stats", ["fiscal_year"])
    op.create_index("ix_uscis_employer_yearly_stats_naics_code", "uscis_employer_yearly_stats", ["naics_code"])
    op.create_index("ix_uscis_employer_yearly_stats_normalized_employer_name", "uscis_employer_yearly_stats", ["normalized_employer_name"])
    op.create_index("ix_uscis_employer_yearly_stats_petitioner_city", "uscis_employer_yearly_stats", ["petitioner_city"])
    op.create_index("ix_uscis_employer_yearly_stats_petitioner_state", "uscis_employer_yearly_stats", ["petitioner_state"])
    op.create_index("ix_uscis_employer_yearly_stats_total_approvals", "uscis_employer_yearly_stats", ["total_approvals"])
    op.create_index("ix_uscis_employer_yearly_stats_total_decisions", "uscis_employer_yearly_stats", ["total_decisions"])
    op.create_index("ix_uscis_employer_yearly_stats_total_denials", "uscis_employer_yearly_stats", ["total_denials"])
    op.create_index("ix_uscis_normalized_year", "uscis_employer_yearly_stats", ["normalized_employer_name", "fiscal_year"])
    op.create_index("ix_uscis_year_naics", "uscis_employer_yearly_stats", ["fiscal_year", "naics_code"])
    op.create_index("ix_uscis_year_state", "uscis_employer_yearly_stats", ["fiscal_year", "petitioner_state"])

    op.create_table(
        "company_pursuits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="promoted"),
        sa.Column("assigned_staff_name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("assigned_staff_email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pursuit_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id"),
    )
    op.create_index("ix_company_pursuits_assigned_staff_email", "company_pursuits", ["assigned_staff_email"])
    op.create_index("ix_company_pursuits_company_id", "company_pursuits", ["company_id"])
    op.create_index("ix_company_pursuits_region_id", "company_pursuits", ["region_id"])
    op.create_index("ix_company_pursuits_status", "company_pursuits", ["status"])


def downgrade() -> None:
    op.drop_index("ix_company_pursuits_status", table_name="company_pursuits")
    op.drop_index("ix_company_pursuits_region_id", table_name="company_pursuits")
    op.drop_index("ix_company_pursuits_company_id", table_name="company_pursuits")
    op.drop_index("ix_company_pursuits_assigned_staff_email", table_name="company_pursuits")
    op.drop_table("company_pursuits")
    op.drop_index("ix_uscis_year_state", table_name="uscis_employer_yearly_stats")
    op.drop_index("ix_uscis_year_naics", table_name="uscis_employer_yearly_stats")
    op.drop_index("ix_uscis_normalized_year", table_name="uscis_employer_yearly_stats")
    op.drop_index("ix_uscis_employer_yearly_stats_total_denials", table_name="uscis_employer_yearly_stats")
    op.drop_index("ix_uscis_employer_yearly_stats_total_decisions", table_name="uscis_employer_yearly_stats")
    op.drop_index("ix_uscis_employer_yearly_stats_total_approvals", table_name="uscis_employer_yearly_stats")
    op.drop_index("ix_uscis_employer_yearly_stats_petitioner_state", table_name="uscis_employer_yearly_stats")
    op.drop_index("ix_uscis_employer_yearly_stats_petitioner_city", table_name="uscis_employer_yearly_stats")
    op.drop_index("ix_uscis_employer_yearly_stats_normalized_employer_name", table_name="uscis_employer_yearly_stats")
    op.drop_index("ix_uscis_employer_yearly_stats_naics_code", table_name="uscis_employer_yearly_stats")
    op.drop_index("ix_uscis_employer_yearly_stats_fiscal_year", table_name="uscis_employer_yearly_stats")
    op.drop_index("ix_uscis_employer_yearly_stats_employer_name", table_name="uscis_employer_yearly_stats")
    op.drop_index("ix_uscis_employer_yearly_stats_company_id", table_name="uscis_employer_yearly_stats")
    op.drop_table("uscis_employer_yearly_stats")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_index("ix_regions_code", table_name="regions")
    op.drop_table("regions")
