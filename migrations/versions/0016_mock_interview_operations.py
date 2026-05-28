"""mock interview operations

Revision ID: 0016_mock_interview_operations
Revises: 0015_consultant_exchange_profile
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_mock_interview_operations"
down_revision = "0015_consultant_exchange_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mock_interviews", sa.Column("consultant_ack_status", sa.String(length=40), nullable=False, server_default="not_required"))
    op.add_column("mock_interviews", sa.Column("consultant_acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("mock_interviews", sa.Column("consultant_acknowledged_by_id", sa.Integer(), nullable=True))
    op.add_column("mock_interviews", sa.Column("consultant_ack_note", sa.Text(), nullable=False, server_default=""))
    op.add_column("mock_interviews", sa.Column("request_note", sa.Text(), nullable=False, server_default=""))
    op.add_column("mock_interviews", sa.Column("conflict_overridden", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("mock_interviews", sa.Column("conflict_override_reason", sa.Text(), nullable=False, server_default=""))
    op.create_foreign_key(
        "fk_mock_interviews_ack_user",
        "mock_interviews",
        "users",
        ["consultant_acknowledged_by_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "trainer_weekly_availability",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("staff_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["staff_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trainer_weekly_availability_staff", "trainer_weekly_availability", ["staff_id"])
    op.create_index("ix_trainer_weekly_availability_weekday", "trainer_weekly_availability", ["weekday"])

    op.create_table(
        "trainer_adhoc_availability",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("staff_id", sa.Integer(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["staff_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trainer_adhoc_availability_staff", "trainer_adhoc_availability", ["staff_id"])
    op.create_index("ix_trainer_adhoc_availability_start", "trainer_adhoc_availability", ["start_at"])

    op.create_table(
        "consultant_availability_blocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("consultant_id", sa.Integer(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("reason", sa.String(length=160), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["consultant_id"], ["consultant_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_consultant_availability_blocks_consultant", "consultant_availability_blocks", ["consultant_id"])
    op.create_index("ix_consultant_availability_blocks_start", "consultant_availability_blocks", ["start_at"])

    op.create_table(
        "mock_interview_status_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mock_interview_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("from_status", sa.String(length=40), nullable=False),
        sa.Column("to_status", sa.String(length=40), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["mock_interview_id"], ["mock_interviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mock_interview_status_events_mock", "mock_interview_status_events", ["mock_interview_id"])
    op.create_index("ix_mock_interview_status_events_actor", "mock_interview_status_events", ["actor_id"])


def downgrade() -> None:
    op.drop_index("ix_mock_interview_status_events_actor", table_name="mock_interview_status_events")
    op.drop_index("ix_mock_interview_status_events_mock", table_name="mock_interview_status_events")
    op.drop_table("mock_interview_status_events")
    op.drop_index("ix_consultant_availability_blocks_start", table_name="consultant_availability_blocks")
    op.drop_index("ix_consultant_availability_blocks_consultant", table_name="consultant_availability_blocks")
    op.drop_table("consultant_availability_blocks")
    op.drop_index("ix_trainer_adhoc_availability_start", table_name="trainer_adhoc_availability")
    op.drop_index("ix_trainer_adhoc_availability_staff", table_name="trainer_adhoc_availability")
    op.drop_table("trainer_adhoc_availability")
    op.drop_index("ix_trainer_weekly_availability_weekday", table_name="trainer_weekly_availability")
    op.drop_index("ix_trainer_weekly_availability_staff", table_name="trainer_weekly_availability")
    op.drop_table("trainer_weekly_availability")
    op.drop_constraint("fk_mock_interviews_ack_user", "mock_interviews", type_="foreignkey")
    op.drop_column("mock_interviews", "conflict_override_reason")
    op.drop_column("mock_interviews", "conflict_overridden")
    op.drop_column("mock_interviews", "request_note")
    op.drop_column("mock_interviews", "consultant_ack_note")
    op.drop_column("mock_interviews", "consultant_acknowledged_by_id")
    op.drop_column("mock_interviews", "consultant_acknowledged_at")
    op.drop_column("mock_interviews", "consultant_ack_status")
