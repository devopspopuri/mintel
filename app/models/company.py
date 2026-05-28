from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SponsorshipTier(str, Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PursuitStatus(str, Enum):
    ANALYSIS = "analysis"
    PROMOTED = "promoted"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    CLOSED = "closed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Company(TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        Index("ix_companies_sponsorship_tier", "sponsorship_tier"),
        Index("ix_companies_opt_friendly", "opt_friendly"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(280), unique=True, index=True)
    website: Mapped[str] = mapped_column(String(500), default="")
    linkedin_url: Mapped[str] = mapped_column(String(500), default="")
    careers_url: Mapped[str] = mapped_column(String(500), default="")
    ats_api_url: Mapped[str] = mapped_column(String(500), default="")
    ats_type: Mapped[str] = mapped_column(String(40), default="")
    ats_platform: Mapped[str] = mapped_column(String(120), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    industry: Mapped[str] = mapped_column(String(160), default="")
    headquarters_city: Mapped[str] = mapped_column(String(120), default="")
    headquarters_state: Mapped[str] = mapped_column(String(80), default="")
    managed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    application_time_minutes: Mapped[int] = mapped_column(Integer, default=0)
    requires_account_creation: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_email_verification: Mapped[bool] = mapped_column(Boolean, default=False)
    accepts_cover_letter: Mapped[bool] = mapped_column(Boolean, default=False)
    onsite_interview_required: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_status: Mapped[str] = mapped_column(String(30), default="unknown")
    stem_opt_status: Mapped[str] = mapped_column(String(30), default="unknown")
    sponsorship_status: Mapped[str] = mapped_column(String(30), default="unknown")
    opt_risk: Mapped[str] = mapped_column(String(30), default="low")
    opt_recent_hires: Mapped[int] = mapped_column(Integer, default=0)
    h1b_filings_recent: Mapped[int] = mapped_column(Integer, default=0)
    opt_last_verified: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    opt_notes: Mapped[str] = mapped_column(Text, default="")
    tech_stack: Mapped[str] = mapped_column(Text, default="")
    background_process: Mapped[str] = mapped_column(Text, default="")
    submission_guidance: Mapped[str] = mapped_column(Text, default="")
    sponsorship_tier: Mapped[SponsorshipTier] = mapped_column(String(20), default=SponsorshipTier.UNKNOWN)
    h1b_approval_count: Mapped[int] = mapped_column(Integer, default=0)
    h1b_denial_count: Mapped[int] = mapped_column(Integer, default=0)
    opt_friendly: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_friendliness_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.00"))
    notes: Mapped[str] = mapped_column(Text, default="")

    aliases: Mapped[list["CompanyAlias"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    h1b_disclosures: Mapped[list["H1BDisclosure"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    uscis_yearly_stats: Mapped[list["UscisEmployerYearlyStat"]] = relationship(back_populates="company")
    pursuit: Mapped[Optional["CompanyPursuit"]] = relationship(back_populates="company")
    jobs: Mapped[list["JobOpportunity"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    interviews: Mapped[list["InterviewExperience"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class CompanyAlias(TimestampMixin, Base):
    __tablename__ = "company_aliases"
    __table_args__ = (UniqueConstraint("raw_name", name="uq_company_aliases_raw_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    raw_name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    source: Mapped[str] = mapped_column(String(80), default="h1b")

    company: Mapped[Company] = relationship(back_populates="aliases")


class CompanyMergeAudit(TimestampMixin, Base):
    __tablename__ = "company_merge_audits"
    __table_args__ = (
        Index("ix_company_merge_audits_source", "source_company_id"),
        Index("ix_company_merge_audits_target", "target_company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_company_id: Mapped[int] = mapped_column(Integer, index=True)
    source_company_name: Mapped[str] = mapped_column(String(255), default="")
    target_company_id: Mapped[int] = mapped_column(Integer, index=True)
    target_company_name: Mapped[str] = mapped_column(String(255), default="")
    actor: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class Region(TimestampMixin, Base):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    staff_owner_name: Mapped[str] = mapped_column(String(160), default="")
    staff_owner_email: Mapped[str] = mapped_column(String(255), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    pursuits: Mapped[list["CompanyPursuit"]] = relationship(back_populates="region")


class CompanyPursuit(TimestampMixin, Base):
    __tablename__ = "company_pursuits"
    __table_args__ = (
        Index("ix_company_pursuits_status", "status"),
        Index("ix_company_pursuits_assigned_staff_email", "assigned_staff_email"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), unique=True, index=True)
    region_id: Mapped[Optional[int]] = mapped_column(ForeignKey("regions.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[PursuitStatus] = mapped_column(String(30), default=PursuitStatus.PROMOTED)
    assigned_staff_name: Mapped[str] = mapped_column(String(160), default="")
    assigned_staff_email: Mapped[str] = mapped_column(String(255), default="")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    next_follow_up_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closing_probability: Mapped[int] = mapped_column(Integer, default=0)
    decision: Mapped[str] = mapped_column(String(40), default="")
    pursuit_reason: Mapped[str] = mapped_column(Text, default="")
    next_action: Mapped[str] = mapped_column(Text, default="")
    recent_requirements: Mapped[str] = mapped_column(Text, default="")
    technology_stack: Mapped[str] = mapped_column(Text, default="")
    submission_intelligence: Mapped[str] = mapped_column(Text, default="")
    company_contacts: Mapped[str] = mapped_column(Text, default="")
    prime_vendors: Mapped[str] = mapped_column(Text, default="")
    c2c_managers: Mapped[str] = mapped_column(Text, default="")
    research_prompt: Mapped[str] = mapped_column(Text, default="")
    research_summary: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    company: Mapped[Company] = relationship(back_populates="pursuit")
    region: Mapped[Optional[Region]] = relationship(back_populates="pursuits")
