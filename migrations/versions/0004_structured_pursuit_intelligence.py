"""structured pursuit intelligence

Revision ID: 0004_pursuit_intel
Revises: 0003_pursuit_intelligence_tabs
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa


revision = "0004_pursuit_intel"
down_revision = "0003_pursuit_intelligence_tabs"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("company_pursuits", sa.Column("next_follow_up_date", sa.Date(), nullable=True))
    op.add_column("company_pursuits", sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("company_pursuits", sa.Column("closing_probability", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("company_pursuits", sa.Column("decision", sa.String(length=40), nullable=False, server_default=""))

    op.create_table(
        "pursuit_research_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("model", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_response", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
        sa.ForeignKeyConstraint(["pursuit_id"], ["company_pursuits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pursuit_research_jobs_pursuit_id", "pursuit_research_jobs", ["pursuit_id"])
    op.create_index("ix_pursuit_research_jobs_status", "pursuit_research_jobs", ["status"])

    op.create_table(
        "pursuit_requirements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("location", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("posted_or_seen_date", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("employment_type", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("technologies", sa.Text(), nullable=False, server_default=""),
        sa.Column("work_auth_language", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_url", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("confidence", sa.String(length=20), nullable=False, server_default=""),
        *_timestamps(),
        sa.ForeignKeyConstraint(["pursuit_id"], ["company_pursuits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pursuit_requirements_pursuit_id", "pursuit_requirements", ["pursuit_id"])
    op.create_index("ix_pursuit_requirements_title", "pursuit_requirements", ["title"])

    op.create_table(
        "pursuit_technologies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.String(length=20), nullable=False, server_default=""),
        *_timestamps(),
        sa.ForeignKeyConstraint(["pursuit_id"], ["company_pursuits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pursuit_id", "category", "name", name="uq_pursuit_technology"),
    )
    op.create_index("ix_pursuit_technologies_pursuit_id", "pursuit_technologies", ["pursuit_id"])

    op.create_table(
        "pursuit_contacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("department", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("location", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("linkedin_url", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("source_url", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("confidence", sa.String(length=20), nullable=False, server_default=""),
        *_timestamps(),
        sa.ForeignKeyConstraint(["pursuit_id"], ["company_pursuits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pursuit_contacts_pursuit_id", "pursuit_contacts", ["pursuit_id"])
    op.create_index("ix_pursuit_contacts_name", "pursuit_contacts", ["name"])

    op.create_table(
        "pursuit_prime_vendors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=False),
        sa.Column("vendor_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("relationship_evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("technology_or_role_focus", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_url", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("confidence", sa.String(length=20), nullable=False, server_default=""),
        *_timestamps(),
        sa.ForeignKeyConstraint(["pursuit_id"], ["company_pursuits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pursuit_prime_vendors_pursuit_id", "pursuit_prime_vendors", ["pursuit_id"])
    op.create_index("ix_pursuit_prime_vendors_name", "pursuit_prime_vendors", ["vendor_name"])

    op.create_table(
        "pursuit_c2c_managers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("company_or_vendor", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("role_focus", sa.Text(), nullable=False, server_default=""),
        sa.Column("linkedin_url", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("source_url", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("confidence", sa.String(length=20), nullable=False, server_default=""),
        *_timestamps(),
        sa.ForeignKeyConstraint(["pursuit_id"], ["company_pursuits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pursuit_c2c_managers_pursuit_id", "pursuit_c2c_managers", ["pursuit_id"])
    op.create_index("ix_pursuit_c2c_managers_name", "pursuit_c2c_managers", ["name"])

    op.create_table(
        "pursuit_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("url", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.String(length=20), nullable=False, server_default=""),
        *_timestamps(),
        sa.ForeignKeyConstraint(["pursuit_id"], ["company_pursuits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pursuit_evidence_pursuit_id", "pursuit_evidence", ["pursuit_id"])
    op.create_index("ix_pursuit_evidence_kind", "pursuit_evidence", ["kind"])

    op.create_table(
        "pursuit_activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("activity_type", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("due_at", sa.String(length=80), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["pursuit_id"], ["company_pursuits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pursuit_activities_pursuit_id", "pursuit_activities", ["pursuit_id"])
    op.create_index("ix_pursuit_activities_pursuit_created", "pursuit_activities", ["pursuit_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_pursuit_activities_pursuit_created", table_name="pursuit_activities")
    op.drop_index("ix_pursuit_activities_pursuit_id", table_name="pursuit_activities")
    op.drop_table("pursuit_activities")
    op.drop_index("ix_pursuit_evidence_kind", table_name="pursuit_evidence")
    op.drop_index("ix_pursuit_evidence_pursuit_id", table_name="pursuit_evidence")
    op.drop_table("pursuit_evidence")
    op.drop_index("ix_pursuit_c2c_managers_name", table_name="pursuit_c2c_managers")
    op.drop_index("ix_pursuit_c2c_managers_pursuit_id", table_name="pursuit_c2c_managers")
    op.drop_table("pursuit_c2c_managers")
    op.drop_index("ix_pursuit_prime_vendors_name", table_name="pursuit_prime_vendors")
    op.drop_index("ix_pursuit_prime_vendors_pursuit_id", table_name="pursuit_prime_vendors")
    op.drop_table("pursuit_prime_vendors")
    op.drop_index("ix_pursuit_contacts_name", table_name="pursuit_contacts")
    op.drop_index("ix_pursuit_contacts_pursuit_id", table_name="pursuit_contacts")
    op.drop_table("pursuit_contacts")
    op.drop_index("ix_pursuit_technologies_pursuit_id", table_name="pursuit_technologies")
    op.drop_table("pursuit_technologies")
    op.drop_index("ix_pursuit_requirements_title", table_name="pursuit_requirements")
    op.drop_index("ix_pursuit_requirements_pursuit_id", table_name="pursuit_requirements")
    op.drop_table("pursuit_requirements")
    op.drop_index("ix_pursuit_research_jobs_status", table_name="pursuit_research_jobs")
    op.drop_index("ix_pursuit_research_jobs_pursuit_id", table_name="pursuit_research_jobs")
    op.drop_table("pursuit_research_jobs")
    op.drop_column("company_pursuits", "decision")
    op.drop_column("company_pursuits", "closing_probability")
    op.drop_column("company_pursuits", "last_activity_at")
    op.drop_column("company_pursuits", "next_follow_up_date")
