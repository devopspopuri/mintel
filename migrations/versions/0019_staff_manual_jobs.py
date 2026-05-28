"""staff manual job posting fields

Revision ID: 0019_staff_manual_jobs
Revises: 0018_pursuit_managed_notes
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_staff_manual_jobs"
down_revision = "0018_pursuit_managed_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_opportunities", sa.Column("requirement_key", sa.String(length=120), nullable=True))
    op.add_column("job_opportunities", sa.Column("marketing_role_ids", sa.String(length=500), nullable=False, server_default=""))
    op.add_column("job_opportunities", sa.Column("additional_cloud_specializations", sa.String(length=120), nullable=False, server_default=""))
    op.add_column("job_opportunities", sa.Column("certifications_required", sa.Text(), nullable=False, server_default=""))
    op.add_column("job_opportunities", sa.Column("job_type", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("job_opportunities", sa.Column("experience_level", sa.String(length=20), nullable=False, server_default=""))
    op.add_column("job_opportunities", sa.Column("source_type", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("job_opportunities", sa.Column("posted_on", sa.Date(), nullable=True))
    op.add_column("job_opportunities", sa.Column("ats_platform", sa.String(length=120), nullable=False, server_default=""))
    op.add_column("job_opportunities", sa.Column("description", sa.Text(), nullable=False, server_default=""))
    op.add_column("job_opportunities", sa.Column("decision_payload", sa.Text(), nullable=False, server_default=""))
    op.add_column("job_opportunities", sa.Column("approval_status", sa.String(length=40), nullable=False, server_default="pending"))
    op.add_column("job_opportunities", sa.Column("created_by", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("job_opportunities", sa.Column("job_alerts_created", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("uq_jobs_company_requirement_key", "job_opportunities", ["company_id", "requirement_key"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_jobs_company_requirement_key", table_name="job_opportunities")
    op.drop_column("job_opportunities", "job_alerts_created")
    op.drop_column("job_opportunities", "created_by")
    op.drop_column("job_opportunities", "approval_status")
    op.drop_column("job_opportunities", "decision_payload")
    op.drop_column("job_opportunities", "description")
    op.drop_column("job_opportunities", "ats_platform")
    op.drop_column("job_opportunities", "posted_on")
    op.drop_column("job_opportunities", "source_type")
    op.drop_column("job_opportunities", "experience_level")
    op.drop_column("job_opportunities", "job_type")
    op.drop_column("job_opportunities", "certifications_required")
    op.drop_column("job_opportunities", "additional_cloud_specializations")
    op.drop_column("job_opportunities", "marketing_role_ids")
    op.drop_column("job_opportunities", "requirement_key")
