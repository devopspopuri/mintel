"""consultant role journeys

Revision ID: 0023_consultant_role_journeys
Revises: 0022_targeting_campaigns
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_consultant_role_journeys"
down_revision = "0022_targeting_campaigns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consultant_role_journeys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("consultant_id", sa.Integer(), nullable=False),
        sa.Column("marketing_role_id", sa.Integer(), nullable=True),
        sa.Column("training_program_id", sa.Integer(), nullable=True),
        sa.Column("assigned_staff_id", sa.Integer(), nullable=True),
        sa.Column("assigned_trainer_id", sa.Integer(), nullable=True),
        sa.Column("target_domain", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("current_stage", sa.String(length=80), nullable=False, server_default="role_intake"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
        sa.Column("target_start_date", sa.Date(), nullable=True),
        sa.Column("target_market_date", sa.Date(), nullable=True),
        sa.Column("readiness_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocker_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("positioning_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["consultant_id"], ["consultant_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["marketing_role_id"], ["marketing_roles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["training_program_id"], ["training_programs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_staff_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_trainer_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_consultant_role_journeys_consultant", "consultant_role_journeys", ["consultant_id"])
    op.create_index("ix_consultant_role_journeys_stage", "consultant_role_journeys", ["current_stage"])
    op.create_index("ix_consultant_role_journeys_status", "consultant_role_journeys", ["status"])

    op.create_table(
        "consultant_journey_activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("journey_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("key", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("stage", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=220), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="todo"),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_id", sa.Integer(), nullable=True),
        sa.Column("evidence_url", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["journey_id"], ["consultant_role_journeys.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["completed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_consultant_journey_activities_journey", "consultant_journey_activities", ["journey_id"])
    op.create_index("ix_consultant_journey_activities_stage", "consultant_journey_activities", ["stage"])
    op.create_index("ix_consultant_journey_activities_status", "consultant_journey_activities", ["status"])


def downgrade() -> None:
    op.drop_index("ix_consultant_journey_activities_status", table_name="consultant_journey_activities")
    op.drop_index("ix_consultant_journey_activities_stage", table_name="consultant_journey_activities")
    op.drop_index("ix_consultant_journey_activities_journey", table_name="consultant_journey_activities")
    op.drop_table("consultant_journey_activities")
    op.drop_index("ix_consultant_role_journeys_status", table_name="consultant_role_journeys")
    op.drop_index("ix_consultant_role_journeys_stage", table_name="consultant_role_journeys")
    op.drop_index("ix_consultant_role_journeys_consultant", table_name="consultant_role_journeys")
    op.drop_table("consultant_role_journeys")
