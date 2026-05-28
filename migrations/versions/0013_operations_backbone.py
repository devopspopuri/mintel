"""operations backbone

Revision ID: 0013_operations_backbone
Revises: 0012_training_program_domains
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa


revision = "0013_operations_backbone"
down_revision = "0012_training_program_domains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resume_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("consultant_id", sa.Integer(), sa.ForeignKey("consultant_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("base_resume_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("target_role_id", sa.Integer(), sa.ForeignKey("marketing_roles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_domain", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("target_job_id", sa.Integer(), sa.ForeignKey("job_opportunities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("latest_project_update", sa.Text(), nullable=False, server_default=""),
        sa.Column("supporting_project_improvements", sa.Text(), nullable=False, server_default=""),
        sa.Column("ats_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tailoring_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("file_reference", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_resume_versions_consultant", "resume_versions", ["consultant_id"])
    op.create_index("ix_resume_versions_active", "resume_versions", ["active"])

    op.create_table(
        "consultant_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("consultant_id", sa.Integer(), sa.ForeignKey("consultant_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_opportunities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resume_version_id", sa.Integer(), sa.ForeignKey("resume_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("submitted_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="draft"),
        sa.Column("vendor_contact", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("bill_rate", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("submission_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_step", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_consultant_submissions_consultant", "consultant_submissions", ["consultant_id"])
    op.create_index("ix_consultant_submissions_job", "consultant_submissions", ["job_id"])
    op.create_index("ix_consultant_submissions_status", "consultant_submissions", ["status"])

    op.create_table(
        "mock_interviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("consultant_id", sa.Integer(), sa.ForeignKey("consultant_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("consultant_submissions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("training_program_id", sa.Integer(), sa.ForeignKey("training_programs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scheduled_on", sa.Date(), nullable=True),
        sa.Column("interviewer_name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("role_snapshot", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("domain_snapshot", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="planned"),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("strengths", sa.Text(), nullable=False, server_default=""),
        sa.Column("gaps", sa.Text(), nullable=False, server_default=""),
        sa.Column("action_items", sa.Text(), nullable=False, server_default=""),
        sa.Column("question_coverage", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_mock_interviews_consultant", "mock_interviews", ["consultant_id"])
    op.create_index("ix_mock_interviews_status", "mock_interviews", ["status"])


def downgrade() -> None:
    op.drop_index("ix_mock_interviews_status", table_name="mock_interviews")
    op.drop_index("ix_mock_interviews_consultant", table_name="mock_interviews")
    op.drop_table("mock_interviews")
    op.drop_index("ix_consultant_submissions_status", table_name="consultant_submissions")
    op.drop_index("ix_consultant_submissions_job", table_name="consultant_submissions")
    op.drop_index("ix_consultant_submissions_consultant", table_name="consultant_submissions")
    op.drop_table("consultant_submissions")
    op.drop_index("ix_resume_versions_active", table_name="resume_versions")
    op.drop_index("ix_resume_versions_consultant", table_name="resume_versions")
    op.drop_table("resume_versions")
