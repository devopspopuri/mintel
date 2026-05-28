from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.company import Base, TimestampMixin


class UserRole(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    REGIONAL_STAFF = "regional_staff"
    VIEWER = "viewer"
    CONSULTANT = "consultant"


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_role", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(150), unique=True, index=True, nullable=True)
    first_name: Mapped[str] = mapped_column(String(80), default="")
    last_name: Mapped[str] = mapped_column(String(80), default="")
    name: Mapped[str] = mapped_column(String(160), default="")
    timezone: Mapped[str] = mapped_column(String(80), default="America/Chicago")
    role: Mapped[UserRole] = mapped_column(String(40), default=UserRole.VIEWER)
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    region_assignments: Mapped[list["StaffRegionAssignment"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    marketing_role_assignments: Mapped[list["StaffMarketingRoleAssignment"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    region_group_memberships: Mapped[list["RegionGroupMember"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class RegionGroup(TimestampMixin, Base):
    __tablename__ = "region_groups"
    __table_args__ = (
        UniqueConstraint("name", name="uq_region_groups_name"),
        Index("ix_region_groups_active", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    members: Mapped[list["RegionGroupMember"]] = relationship(back_populates="group", cascade="all, delete-orphan")
    regions: Mapped[list["RegionGroupRegion"]] = relationship(back_populates="group", cascade="all, delete-orphan")


class RegionGroupMember(TimestampMixin, Base):
    __tablename__ = "region_group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_region_group_member"),
        Index("ix_region_group_members_group", "group_id"),
        Index("ix_region_group_members_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("region_groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    group: Mapped[RegionGroup] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="region_group_memberships")


class RegionGroupRegion(TimestampMixin, Base):
    __tablename__ = "region_group_regions"
    __table_args__ = (
        UniqueConstraint("group_id", "region_id", name="uq_region_group_region"),
        Index("ix_region_group_regions_group", "group_id"),
        Index("ix_region_group_regions_region", "region_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("region_groups.id", ondelete="CASCADE"))
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id", ondelete="CASCADE"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    group: Mapped[RegionGroup] = relationship(back_populates="regions")
    region = relationship("Region")


class StaffRegionAssignment(TimestampMixin, Base):
    __tablename__ = "staff_region_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "region_id", name="uq_staff_region_assignment"),
        Index("ix_staff_region_assignments_user", "user_id"),
        Index("ix_staff_region_assignments_region", "region_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id", ondelete="CASCADE"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="region_assignments")
    region = relationship("Region")


class StaffMarketingRoleAssignment(TimestampMixin, Base):
    __tablename__ = "staff_marketing_role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "marketing_role_id", name="uq_staff_marketing_role_assignment"),
        Index("ix_staff_marketing_role_assignments_user", "user_id"),
        Index("ix_staff_marketing_role_assignments_role", "marketing_role_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    marketing_role_id: Mapped[int] = mapped_column(ForeignKey("marketing_roles.id", ondelete="CASCADE"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="marketing_role_assignments")
    marketing_role = relationship("MarketingRole")
