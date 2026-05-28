"""mintel company profile fields

Revision ID: 0020_mintel_company_profile
Revises: 0019_staff_manual_jobs
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_mintel_company_profile"
down_revision = "0019_staff_manual_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("linkedin_url", sa.String(length=500), nullable=False, server_default=""))
    op.add_column("companies", sa.Column("careers_url", sa.String(length=500), nullable=False, server_default=""))
    op.add_column("companies", sa.Column("ats_api_url", sa.String(length=500), nullable=False, server_default=""))
    op.add_column("companies", sa.Column("ats_type", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("companies", sa.Column("ats_platform", sa.String(length=120), nullable=False, server_default=""))
    op.add_column("companies", sa.Column("location", sa.String(length=200), nullable=False, server_default=""))
    op.add_column("companies", sa.Column("managed_by_id", sa.Integer(), nullable=True))
    op.add_column("companies", sa.Column("application_time_minutes", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("companies", sa.Column("requires_account_creation", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("companies", sa.Column("requires_email_verification", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("companies", sa.Column("accepts_cover_letter", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("companies", sa.Column("onsite_interview_required", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("companies", sa.Column("opt_status", sa.String(length=30), nullable=False, server_default="unknown"))
    op.add_column("companies", sa.Column("stem_opt_status", sa.String(length=30), nullable=False, server_default="unknown"))
    op.add_column("companies", sa.Column("sponsorship_status", sa.String(length=30), nullable=False, server_default="unknown"))
    op.add_column("companies", sa.Column("opt_risk", sa.String(length=30), nullable=False, server_default="low"))
    op.add_column("companies", sa.Column("opt_recent_hires", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("companies", sa.Column("h1b_filings_recent", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("companies", sa.Column("opt_last_verified", sa.Date(), nullable=True))
    op.add_column("companies", sa.Column("opt_notes", sa.Text(), nullable=False, server_default=""))
    op.add_column("companies", sa.Column("tech_stack", sa.Text(), nullable=False, server_default=""))
    op.add_column("companies", sa.Column("background_process", sa.Text(), nullable=False, server_default=""))
    op.add_column("companies", sa.Column("submission_guidance", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_companies_managed_by_id", "companies", ["managed_by_id"])
    op.create_foreign_key("fk_companies_managed_by_id_users", "companies", "users", ["managed_by_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_companies_managed_by_id_users", "companies", type_="foreignkey")
    op.drop_index("ix_companies_managed_by_id", table_name="companies")
    op.drop_column("companies", "submission_guidance")
    op.drop_column("companies", "background_process")
    op.drop_column("companies", "tech_stack")
    op.drop_column("companies", "opt_notes")
    op.drop_column("companies", "opt_last_verified")
    op.drop_column("companies", "h1b_filings_recent")
    op.drop_column("companies", "opt_recent_hires")
    op.drop_column("companies", "opt_risk")
    op.drop_column("companies", "sponsorship_status")
    op.drop_column("companies", "stem_opt_status")
    op.drop_column("companies", "opt_status")
    op.drop_column("companies", "onsite_interview_required")
    op.drop_column("companies", "accepts_cover_letter")
    op.drop_column("companies", "requires_email_verification")
    op.drop_column("companies", "requires_account_creation")
    op.drop_column("companies", "application_time_minutes")
    op.drop_column("companies", "managed_by_id")
    op.drop_column("companies", "location")
    op.drop_column("companies", "ats_platform")
    op.drop_column("companies", "ats_type")
    op.drop_column("companies", "ats_api_url")
    op.drop_column("companies", "careers_url")
    op.drop_column("companies", "linkedin_url")
