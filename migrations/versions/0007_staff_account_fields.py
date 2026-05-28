"""staff account fields

Revision ID: 0007_staff_account_fields
Revises: 0006_staff_assignments
Create Date: 2026-05-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0007_staff_account_fields"
down_revision = "0006_staff_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=150), nullable=True))
    op.add_column("users", sa.Column("first_name", sa.String(length=80), nullable=False, server_default=""))
    op.add_column("users", sa.Column("last_name", sa.String(length=80), nullable=False, server_default=""))
    op.add_column("users", sa.Column("timezone", sa.String(length=80), nullable=False, server_default="America/Chicago"))
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.execute(
        """
        update users
        set username = regexp_replace(split_part(email, '@', 1), '[^a-zA-Z0-9_.-]', '_', 'g')
        where username is null and email is not null and email <> ''
        """
    )


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "timezone")
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
    op.drop_column("users", "username")
