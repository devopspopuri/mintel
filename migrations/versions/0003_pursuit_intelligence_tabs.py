"""pursuit intelligence tabs

Revision ID: 0003_pursuit_intelligence_tabs
Revises: 0002_web_auth_uscis_pursuits
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_pursuit_intelligence_tabs"
down_revision = "0002_web_auth_uscis_pursuits"
branch_labels = None
depends_on = None


TEXT_COLUMNS = (
    "recent_requirements",
    "technology_stack",
    "submission_intelligence",
    "company_contacts",
    "prime_vendors",
    "c2c_managers",
    "research_prompt",
    "research_summary",
)


def upgrade() -> None:
    for column in TEXT_COLUMNS:
        op.add_column("company_pursuits", sa.Column(column, sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    for column in reversed(TEXT_COLUMNS):
        op.drop_column("company_pursuits", column)
