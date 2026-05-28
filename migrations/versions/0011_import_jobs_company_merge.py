"""import jobs and company merge audit

Revision ID: 0011_import_jobs_company_merge
Revises: 0010_training_programs
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0011_import_jobs_company_merge"
down_revision = "0010_training_programs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "uscis_import_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_file", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("stored_path", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("requested_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("processed_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_uscis_import_jobs_created", "uscis_import_jobs", ["created_at"])
    op.create_index("ix_uscis_import_jobs_status", "uscis_import_jobs", ["status"])
    op.create_table(
        "company_merge_audits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_company_id", sa.Integer(), nullable=False),
        sa.Column("source_company_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("target_company_id", sa.Integer(), nullable=False),
        sa.Column("target_company_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_company_merge_audits_source", "company_merge_audits", ["source_company_id"])
    op.create_index("ix_company_merge_audits_target", "company_merge_audits", ["target_company_id"])


def downgrade() -> None:
    op.drop_index("ix_company_merge_audits_target", table_name="company_merge_audits")
    op.drop_index("ix_company_merge_audits_source", table_name="company_merge_audits")
    op.drop_table("company_merge_audits")
    op.drop_index("ix_uscis_import_jobs_status", table_name="uscis_import_jobs")
    op.drop_index("ix_uscis_import_jobs_created", table_name="uscis_import_jobs")
    op.drop_table("uscis_import_jobs")
