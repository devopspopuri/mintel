"""training programs

Revision ID: 0010_training_programs
Revises: 0009_marketing_role_owner
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0010_training_programs"
down_revision = "0009_marketing_role_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_programs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("marketing_role_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False, server_default=""),
        sa.Column("duration_weeks", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("target_audience", sa.Text(), nullable=False, server_default=""),
        sa.Column("outcome", sa.Text(), nullable=False, server_default=""),
        sa.Column("vocabulary_plan", sa.Text(), nullable=False, server_default=""),
        sa.Column("concepts_plan", sa.Text(), nullable=False, server_default=""),
        sa.Column("usecases_plan", sa.Text(), nullable=False, server_default=""),
        sa.Column("interview_plan", sa.Text(), nullable=False, server_default=""),
        sa.Column("resume_plan", sa.Text(), nullable=False, server_default=""),
        sa.Column("labs_plan", sa.Text(), nullable=False, server_default=""),
        sa.Column("readiness_checklist", sa.Text(), nullable=False, server_default=""),
        sa.Column("missing_areas", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["marketing_role_id"], ["marketing_roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("marketing_role_id", name="uq_training_programs_marketing_role"),
    )
    op.create_index("ix_training_programs_active", "training_programs", ["active"])
    op.create_index("ix_training_programs_marketing_role_id", "training_programs", ["marketing_role_id"])
    op.create_table(
        "training_job_descriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("pattern_type", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=220), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("responsibilities", sa.Text(), nullable=False, server_default=""),
        sa.Column("required_skills", sa.Text(), nullable=False, server_default=""),
        sa.Column("nice_to_have", sa.Text(), nullable=False, server_default=""),
        sa.Column("domain", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("difficulty", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("work_auth_signal", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["program_id"], ["training_programs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_training_job_descriptions_pattern", "training_job_descriptions", ["pattern_type"])
    op.create_index("ix_training_job_descriptions_program", "training_job_descriptions", ["program_id"])
    op.create_index("ix_training_job_descriptions_program_id", "training_job_descriptions", ["program_id"])


def downgrade() -> None:
    op.drop_index("ix_training_job_descriptions_program_id", table_name="training_job_descriptions")
    op.drop_index("ix_training_job_descriptions_program", table_name="training_job_descriptions")
    op.drop_index("ix_training_job_descriptions_pattern", table_name="training_job_descriptions")
    op.drop_table("training_job_descriptions")
    op.drop_index("ix_training_programs_marketing_role_id", table_name="training_programs")
    op.drop_index("ix_training_programs_active", table_name="training_programs")
    op.drop_table("training_programs")
