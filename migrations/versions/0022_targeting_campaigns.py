"""targeting campaigns

Revision ID: 0022_targeting_campaigns
Revises: 0021_company_job_intelligence
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_targeting_campaigns"
down_revision = "0021_company_job_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "targeting_campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("consultant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=220), nullable=False, server_default=""),
        sa.Column("target_role", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("target_region", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="planned"),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("goal_count", sa.Integer(), nullable=False, server_default="25"),
        sa.Column("min_match_score", sa.Integer(), nullable=False, server_default="35"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["consultant_id"], ["consultant_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_targeting_campaigns_consultant", "targeting_campaigns", ["consultant_id"])
    op.create_index("ix_targeting_campaigns_owner", "targeting_campaigns", ["owner_id"])
    op.create_index("ix_targeting_campaigns_status", "targeting_campaigns", ["status"])

    op.create_table(
        "targeting_campaign_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("submission_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="queued"),
        sa.Column("match_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("company_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("role_fit", sa.Text(), nullable=False, server_default=""),
        sa.Column("skill_overlap", sa.Text(), nullable=False, server_default=""),
        sa.Column("gaps", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["targeting_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pursuit_id"], ["company_pursuits.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["job_opportunities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submission_id"], ["consultant_submissions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_targeting_campaign_targets_campaign", "targeting_campaign_targets", ["campaign_id"])
    op.create_index("ix_targeting_campaign_targets_company", "targeting_campaign_targets", ["company_id"])
    op.create_index("ix_targeting_campaign_targets_status", "targeting_campaign_targets", ["status"])


def downgrade() -> None:
    op.drop_index("ix_targeting_campaign_targets_status", table_name="targeting_campaign_targets")
    op.drop_index("ix_targeting_campaign_targets_company", table_name="targeting_campaign_targets")
    op.drop_index("ix_targeting_campaign_targets_campaign", table_name="targeting_campaign_targets")
    op.drop_table("targeting_campaign_targets")
    op.drop_index("ix_targeting_campaigns_status", table_name="targeting_campaigns")
    op.drop_index("ix_targeting_campaigns_owner", table_name="targeting_campaigns")
    op.drop_index("ix_targeting_campaigns_consultant", table_name="targeting_campaigns")
    op.drop_table("targeting_campaigns")
