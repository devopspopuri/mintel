"""india registration fields

Revision ID: 0026_india_registration_fields
Revises: 0025_placement_fields
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_india_registration_fields"
down_revision = "0025_placement_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("consultant_profiles", sa.Column("india_company_name", sa.String(length=180), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_name_as_per_aadhaar", sa.String(length=180), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_father_name", sa.String(length=180), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_dob", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_pan_no", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_aadhaar_no", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_technology", sa.String(length=160), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_offer_designation", sa.String(length=180), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_current_designation", sa.String(length=180), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_offer_package", sa.String(length=80), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_current_package", sa.String(length=80), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_offer_date", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_joining_date", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_native_address", sa.Text(), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_mail_id", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_mobile_num", sa.String(length=80), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_resignation_date", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_relieving_date", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_bank_name", sa.String(length=180), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_bank_account_num", sa.String(length=80), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_pf_uan_notes", sa.Text(), nullable=False, server_default=""))
    op.add_column("consultant_profiles", sa.Column("india_document_notes", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("consultant_profiles", "india_document_notes")
    op.drop_column("consultant_profiles", "india_pf_uan_notes")
    op.drop_column("consultant_profiles", "india_bank_account_num")
    op.drop_column("consultant_profiles", "india_bank_name")
    op.drop_column("consultant_profiles", "india_relieving_date")
    op.drop_column("consultant_profiles", "india_resignation_date")
    op.drop_column("consultant_profiles", "india_mobile_num")
    op.drop_column("consultant_profiles", "india_mail_id")
    op.drop_column("consultant_profiles", "india_native_address")
    op.drop_column("consultant_profiles", "india_joining_date")
    op.drop_column("consultant_profiles", "india_offer_date")
    op.drop_column("consultant_profiles", "india_current_package")
    op.drop_column("consultant_profiles", "india_offer_package")
    op.drop_column("consultant_profiles", "india_current_designation")
    op.drop_column("consultant_profiles", "india_offer_designation")
    op.drop_column("consultant_profiles", "india_technology")
    op.drop_column("consultant_profiles", "india_aadhaar_no")
    op.drop_column("consultant_profiles", "india_pan_no")
    op.drop_column("consultant_profiles", "india_dob")
    op.drop_column("consultant_profiles", "india_father_name")
    op.drop_column("consultant_profiles", "india_name_as_per_aadhaar")
    op.drop_column("consultant_profiles", "india_company_name")
