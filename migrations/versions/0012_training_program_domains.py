"""training program industry domains

Revision ID: 0012_training_program_domains
Revises: 0011_import_jobs_company_merge
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0012_training_program_domains"
down_revision = "0011_import_jobs_company_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("training_programs") as batch:
        batch.drop_constraint("uq_training_programs_marketing_role", type_="unique")
        batch.add_column(sa.Column("industry_domain", sa.String(length=120), nullable=False, server_default="Healthcare / Health Insurance"))
        batch.add_column(sa.Column("short_description", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("enterprise_context", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("application_landscape_json", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("cloud_architecture_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("project_responsibilities_json", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("three_year_delivery_timeline_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("key_deliverables_json", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("tools_and_technologies_json", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("interview_story", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("resume_project_summary", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("production_support_scenarios_json", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("interview_questions_json", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"))
        batch.create_unique_constraint("uq_training_programs_role_domain", ["marketing_role_id", "industry_domain"])
    op.create_index("ix_training_programs_industry_domain", "training_programs", ["industry_domain"])


def downgrade() -> None:
    op.drop_index("ix_training_programs_industry_domain", table_name="training_programs")
    with op.batch_alter_table("training_programs") as batch:
        batch.drop_constraint("uq_training_programs_role_domain", type_="unique")
        batch.drop_column("display_order")
        batch.drop_column("interview_questions_json")
        batch.drop_column("production_support_scenarios_json")
        batch.drop_column("resume_project_summary")
        batch.drop_column("interview_story")
        batch.drop_column("tools_and_technologies_json")
        batch.drop_column("key_deliverables_json")
        batch.drop_column("three_year_delivery_timeline_json")
        batch.drop_column("project_responsibilities_json")
        batch.drop_column("cloud_architecture_json")
        batch.drop_column("application_landscape_json")
        batch.drop_column("enterprise_context")
        batch.drop_column("short_description")
        batch.drop_column("industry_domain")
        batch.create_unique_constraint("uq_training_programs_marketing_role", ["marketing_role_id"])
