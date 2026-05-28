"""mock interview role visibility

Revision ID: 0014_mock_role_scope
Revises: 0013_operations_backbone
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa


revision = "0014_mock_role_scope"
down_revision = "0013_operations_backbone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mock_interviews", sa.Column("marketing_role_id", sa.Integer(), nullable=True))
    op.add_column("mock_interviews", sa.Column("assigned_staff_id", sa.Integer(), nullable=True))
    op.add_column("mock_interviews", sa.Column("scheduled_time", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("mock_interviews", sa.Column("timezone", sa.String(length=80), nullable=False, server_default="America/Chicago"))
    op.add_column("mock_interviews", sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="60"))
    op.add_column("mock_interviews", sa.Column("meeting_link", sa.String(length=500), nullable=False, server_default=""))
    op.add_column("mock_interviews", sa.Column("round_type", sa.String(length=80), nullable=False, server_default="mock"))
    op.add_column("mock_interviews", sa.Column("questions_asked", sa.Text(), nullable=False, server_default=""))
    op.add_column("mock_interviews", sa.Column("prep_pack_snapshot", sa.Text(), nullable=False, server_default=""))
    op.create_foreign_key("fk_mock_interviews_marketing_role", "mock_interviews", "marketing_roles", ["marketing_role_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_mock_interviews_assigned_staff", "mock_interviews", "users", ["assigned_staff_id"], ["id"], ondelete="SET NULL")
    op.execute(
        """
        UPDATE mock_interviews
        SET marketing_role_id = COALESCE(
            (SELECT training_programs.marketing_role_id FROM training_programs WHERE training_programs.id = mock_interviews.training_program_id),
            (SELECT consultant_profiles.marketing_role_id FROM consultant_profiles WHERE consultant_profiles.id = mock_interviews.consultant_id)
        )
        WHERE marketing_role_id IS NULL
        """
    )
    op.create_index("ix_mock_interviews_marketing_role", "mock_interviews", ["marketing_role_id"])
    op.create_index("ix_mock_interviews_assigned_staff", "mock_interviews", ["assigned_staff_id"])
    op.create_index("ix_mock_interviews_scheduled_on", "mock_interviews", ["scheduled_on"])


def downgrade() -> None:
    op.drop_index("ix_mock_interviews_scheduled_on", table_name="mock_interviews")
    op.drop_index("ix_mock_interviews_assigned_staff", table_name="mock_interviews")
    op.drop_index("ix_mock_interviews_marketing_role", table_name="mock_interviews")
    op.drop_constraint("fk_mock_interviews_assigned_staff", "mock_interviews", type_="foreignkey")
    op.drop_constraint("fk_mock_interviews_marketing_role", "mock_interviews", type_="foreignkey")
    op.drop_column("mock_interviews", "prep_pack_snapshot")
    op.drop_column("mock_interviews", "questions_asked")
    op.drop_column("mock_interviews", "round_type")
    op.drop_column("mock_interviews", "meeting_link")
    op.drop_column("mock_interviews", "duration_minutes")
    op.drop_column("mock_interviews", "timezone")
    op.drop_column("mock_interviews", "scheduled_time")
    op.drop_column("mock_interviews", "assigned_staff_id")
    op.drop_column("mock_interviews", "marketing_role_id")
