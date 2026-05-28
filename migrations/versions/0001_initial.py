"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=280), nullable=False),
        sa.Column("website", sa.String(length=500), nullable=False),
        sa.Column("industry", sa.String(length=160), nullable=False),
        sa.Column("headquarters_city", sa.String(length=120), nullable=False),
        sa.Column("headquarters_state", sa.String(length=80), nullable=False),
        sa.Column("sponsorship_tier", sa.String(length=20), nullable=False),
        sa.Column("h1b_approval_count", sa.Integer(), nullable=False),
        sa.Column("h1b_denial_count", sa.Integer(), nullable=False),
        sa.Column("opt_friendly", sa.Boolean(), nullable=False),
        sa.Column("opt_friendliness_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_companies_name", "companies", ["name"])
    op.create_index("ix_companies_opt_friendly", "companies", ["opt_friendly"])
    op.create_index("ix_companies_slug", "companies", ["slug"])
    op.create_index("ix_companies_sponsorship_tier", "companies", ["sponsorship_tier"])

    op.create_table(
        "company_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("raw_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("raw_name", name="uq_company_aliases_raw_name"),
    )
    op.create_index("ix_company_aliases_company_id", "company_aliases", ["company_id"])
    op.create_index("ix_company_aliases_normalized_name", "company_aliases", ["normalized_name"])

    op.create_table(
        "h1b_disclosures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("case_number", sa.String(length=80), nullable=False),
        sa.Column("employer_name_raw", sa.String(length=255), nullable=False),
        sa.Column("job_title", sa.String(length=255), nullable=False),
        sa.Column("soc_code", sa.String(length=30), nullable=False),
        sa.Column("soc_title", sa.String(length=255), nullable=False),
        sa.Column("case_status", sa.String(length=30), nullable=False),
        sa.Column("worksite_city", sa.String(length=120), nullable=False),
        sa.Column("worksite_state", sa.String(length=80), nullable=False),
        sa.Column("wage_rate_from", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("wage_unit", sa.String(length=40), nullable=False),
        sa.Column("source_file", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_h1b_disclosures_company_id", "h1b_disclosures", ["company_id"])
    op.create_index("ix_h1b_disclosures_fiscal_year", "h1b_disclosures", ["fiscal_year"])
    op.create_index("ix_h1b_fiscal_year_status", "h1b_disclosures", ["fiscal_year", "case_status"])
    op.create_index("ix_h1b_job_title", "h1b_disclosures", ["job_title"])
    op.create_index("ix_h1b_worksite_state", "h1b_disclosures", ["worksite_state"])
    op.create_index("uq_h1b_case_number_fiscal_year", "h1b_disclosures", ["case_number", "fiscal_year"], unique=True, postgresql_where=sa.text("case_number <> ''"))

    op.create_table(
        "job_opportunities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("location", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("sponsorship_notes", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_active", "job_opportunities", ["active"])
    op.create_index("ix_jobs_title", "job_opportunities", ["title"])
    op.create_index("ix_job_opportunities_company_id", "job_opportunities", ["company_id"])

    op.create_table(
        "interview_experiences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("interview_date", sa.Date(), nullable=True),
        sa.Column("rounds", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["job_opportunities.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_interview_experiences_company_id", "interview_experiences", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_interview_experiences_company_id", table_name="interview_experiences")
    op.drop_table("interview_experiences")
    op.drop_index("ix_job_opportunities_company_id", table_name="job_opportunities")
    op.drop_index("ix_jobs_title", table_name="job_opportunities")
    op.drop_index("ix_jobs_active", table_name="job_opportunities")
    op.drop_table("job_opportunities")
    op.drop_index("ix_h1b_worksite_state", table_name="h1b_disclosures")
    op.drop_index("uq_h1b_case_number_fiscal_year", table_name="h1b_disclosures")
    op.drop_index("ix_h1b_job_title", table_name="h1b_disclosures")
    op.drop_index("ix_h1b_fiscal_year_status", table_name="h1b_disclosures")
    op.drop_index("ix_h1b_disclosures_fiscal_year", table_name="h1b_disclosures")
    op.drop_index("ix_h1b_disclosures_company_id", table_name="h1b_disclosures")
    op.drop_table("h1b_disclosures")
    op.drop_index("ix_company_aliases_normalized_name", table_name="company_aliases")
    op.drop_index("ix_company_aliases_company_id", table_name="company_aliases")
    op.drop_table("company_aliases")
    op.drop_index("ix_companies_sponsorship_tier", table_name="companies")
    op.drop_index("ix_companies_slug", table_name="companies")
    op.drop_index("ix_companies_opt_friendly", table_name="companies")
    op.drop_index("ix_companies_name", table_name="companies")
    op.drop_table("companies")
