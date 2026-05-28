"""company job intelligence import

Revision ID: 0021_company_job_intelligence
Revises: 0020_mintel_company_profile
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_company_job_intelligence"
down_revision = "0020_mintel_company_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pursuit_intelligence_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("requested_research_window", sa.String(length=80), nullable=False, server_default="last_12_months"),
        sa.Column("actual_evidence_window", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("requested_location", sa.String(length=80), nullable=False, server_default="USA"),
        sa.Column("research_date", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("count_type", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("is_full_window_coverage", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("coverage_gap_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("total_eligible_usa_job_signal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_below_8_year_usa_jobs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_below_8_year_usa_jobs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("role_counts_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("company_tech_stack_summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("company_level_use_cases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("role_wise_tech_stack_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("role_wise_use_cases_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("mintel_training_recommendation_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("top_marketing_role", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("second_best_role", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("company_rating", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("data_quality_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("imported_by", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["pursuit_id"], ["company_pursuits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pursuit_intelligence_snapshots_pursuit_id", "pursuit_intelligence_snapshots", ["pursuit_id"])
    op.create_index("ix_pursuit_intel_snapshots_pursuit_created", "pursuit_intelligence_snapshots", ["pursuit_id", "created_at"])
    op.create_index("ix_pursuit_intel_snapshots_rating", "pursuit_intelligence_snapshots", ["company_rating"])

    op.create_table(
        "pursuit_job_posting_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=True),
        sa.Column("included", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("exclusion_group", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("exclusion_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("job_title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("company", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("job_id", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("location", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("usa_location_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("work_mode", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("published_date", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("source_type", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("official_job_url", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("supporting_urls_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("primary_marketing_role", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("primary_role_slug", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("secondary_marketing_roles_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("confidence_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("match_strength", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("experience_requirement_mentioned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exact_experience_text_from_jd", sa.Text(), nullable=False, server_default=""),
        sa.Column("minimum_years_required", sa.Integer(), nullable=True),
        sa.Column("maximum_years_required", sa.Integer(), nullable=True),
        sa.Column("experience_evidence_type", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("estimated_experience_band", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("experience_filter_result", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("experience_filter_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("technology_signals_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("extracted_tech_stack_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("primary_use_cases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("role_specific_use_cases_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("resume_positioning_use_cases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("interview_preparation_use_cases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("why_counted", sa.Text(), nullable=False, server_default=""),
        sa.Column("duplicate_check", sa.Text(), nullable=False, server_default=""),
        sa.Column("duplicate_source_urls_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("raw_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["pursuit_id"], ["company_pursuits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["pursuit_intelligence_snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pursuit_job_posting_evidence_pursuit_id", "pursuit_job_posting_evidence", ["pursuit_id"])
    op.create_index("ix_pursuit_job_posting_evidence_snapshot_id", "pursuit_job_posting_evidence", ["snapshot_id"])
    op.create_index("ix_pursuit_job_evidence_pursuit_included", "pursuit_job_posting_evidence", ["pursuit_id", "included"])
    op.create_index("ix_pursuit_job_evidence_title", "pursuit_job_posting_evidence", ["job_title"])
    op.create_index("ix_pursuit_job_evidence_role", "pursuit_job_posting_evidence", ["primary_role_slug"])
    op.create_index("ix_pursuit_job_evidence_location", "pursuit_job_posting_evidence", ["location"])
    op.create_index("ix_pursuit_job_evidence_url", "pursuit_job_posting_evidence", ["official_job_url"])


def downgrade() -> None:
    op.drop_index("ix_pursuit_job_evidence_url", table_name="pursuit_job_posting_evidence")
    op.drop_index("ix_pursuit_job_evidence_location", table_name="pursuit_job_posting_evidence")
    op.drop_index("ix_pursuit_job_evidence_role", table_name="pursuit_job_posting_evidence")
    op.drop_index("ix_pursuit_job_evidence_title", table_name="pursuit_job_posting_evidence")
    op.drop_index("ix_pursuit_job_evidence_pursuit_included", table_name="pursuit_job_posting_evidence")
    op.drop_index("ix_pursuit_job_posting_evidence_snapshot_id", table_name="pursuit_job_posting_evidence")
    op.drop_index("ix_pursuit_job_posting_evidence_pursuit_id", table_name="pursuit_job_posting_evidence")
    op.drop_table("pursuit_job_posting_evidence")
    op.drop_index("ix_pursuit_intel_snapshots_rating", table_name="pursuit_intelligence_snapshots")
    op.drop_index("ix_pursuit_intel_snapshots_pursuit_created", table_name="pursuit_intelligence_snapshots")
    op.drop_index("ix_pursuit_intelligence_snapshots_pursuit_id", table_name="pursuit_intelligence_snapshots")
    op.drop_table("pursuit_intelligence_snapshots")
