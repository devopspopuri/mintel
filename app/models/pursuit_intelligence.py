from __future__ import annotations

from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.company import Base, TimestampMixin


class ResearchJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MarketingRole(TimestampMixin, Base):
    __tablename__ = "marketing_roles"
    __table_args__ = (
        UniqueConstraint("code", name="uq_marketing_roles_code"),
        Index("ix_marketing_roles_active", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), default="")
    name: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    covers: Mapped[str] = mapped_column(Text, default="")
    common_tools: Mapped[str] = mapped_column(Text, default="")
    aliases: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(default=True)
    requirements: Mapped[list["PursuitRequirement"]] = relationship(back_populates="marketing_role")
    owner = relationship("User")


class PursuitResearchJob(TimestampMixin, Base):
    __tablename__ = "pursuit_research_jobs"
    __table_args__ = (Index("ix_pursuit_research_jobs_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pursuit_id: Mapped[int] = mapped_column(ForeignKey("company_pursuits.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default=ResearchJobStatus.QUEUED.value)
    model: Mapped[str] = mapped_column(String(80), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    raw_response: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")


class PursuitRequirement(TimestampMixin, Base):
    __tablename__ = "pursuit_requirements"
    __table_args__ = (Index("ix_pursuit_requirements_title", "title"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pursuit_id: Mapped[int] = mapped_column(ForeignKey("company_pursuits.id", ondelete="CASCADE"), index=True)
    marketing_role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("marketing_roles.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str] = mapped_column(String(160), default="")
    posted_or_seen_date: Mapped[str] = mapped_column(String(80), default="")
    employment_type: Mapped[str] = mapped_column(String(80), default="")
    technologies: Mapped[str] = mapped_column(Text, default="")
    work_auth_language: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    confidence: Mapped[str] = mapped_column(String(20), default="")
    marketing_role: Mapped[Optional[MarketingRole]] = relationship(back_populates="requirements")


class PursuitTechnology(TimestampMixin, Base):
    __tablename__ = "pursuit_technologies"
    __table_args__ = (UniqueConstraint("pursuit_id", "category", "name", name="uq_pursuit_technology"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pursuit_id: Mapped[int] = mapped_column(ForeignKey("company_pursuits.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(80), default="")
    name: Mapped[str] = mapped_column(String(160), default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(20), default="")


class PursuitIntelligenceSnapshot(TimestampMixin, Base):
    __tablename__ = "pursuit_intelligence_snapshots"
    __table_args__ = (
        Index("ix_pursuit_intel_snapshots_pursuit_created", "pursuit_id", "created_at"),
        Index("ix_pursuit_intel_snapshots_rating", "company_rating"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pursuit_id: Mapped[int] = mapped_column(ForeignKey("company_pursuits.id", ondelete="CASCADE"), index=True)
    company_name: Mapped[str] = mapped_column(String(255), default="")
    requested_research_window: Mapped[str] = mapped_column(String(80), default="last_12_months")
    actual_evidence_window: Mapped[str] = mapped_column(String(120), default="")
    requested_location: Mapped[str] = mapped_column(String(80), default="USA")
    research_date: Mapped[str] = mapped_column(String(40), default="")
    count_type: Mapped[str] = mapped_column(String(120), default="")
    is_full_window_coverage: Mapped[bool] = mapped_column(Boolean, default=False)
    coverage_gap_reason: Mapped[str] = mapped_column(Text, default="")
    total_eligible_usa_job_signal: Mapped[int] = mapped_column(Integer, default=0)
    verified_below_8_year_usa_jobs: Mapped[int] = mapped_column(Integer, default=0)
    estimated_below_8_year_usa_jobs: Mapped[int] = mapped_column(Integer, default=0)
    role_counts_json: Mapped[str] = mapped_column(Text, default="{}")
    company_tech_stack_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    company_level_use_cases_json: Mapped[str] = mapped_column(Text, default="[]")
    role_wise_tech_stack_json: Mapped[str] = mapped_column(Text, default="{}")
    role_wise_use_cases_json: Mapped[str] = mapped_column(Text, default="{}")
    mintel_training_recommendation_json: Mapped[str] = mapped_column(Text, default="{}")
    top_marketing_role: Mapped[str] = mapped_column(String(160), default="")
    second_best_role: Mapped[str] = mapped_column(String(160), default="")
    company_rating: Mapped[str] = mapped_column(String(80), default="")
    data_quality_notes: Mapped[str] = mapped_column(Text, default="")
    raw_json: Mapped[str] = mapped_column(Text, default="")
    imported_by: Mapped[str] = mapped_column(String(160), default="")


class PursuitJobPostingEvidence(TimestampMixin, Base):
    __tablename__ = "pursuit_job_posting_evidence"
    __table_args__ = (
        Index("ix_pursuit_job_evidence_pursuit_included", "pursuit_id", "included"),
        Index("ix_pursuit_job_evidence_title", "job_title"),
        Index("ix_pursuit_job_evidence_role", "primary_role_slug"),
        Index("ix_pursuit_job_evidence_location", "location"),
        Index("ix_pursuit_job_evidence_url", "official_job_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pursuit_id: Mapped[int] = mapped_column(ForeignKey("company_pursuits.id", ondelete="CASCADE"), index=True)
    snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pursuit_intelligence_snapshots.id", ondelete="SET NULL"), nullable=True, index=True)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    exclusion_group: Mapped[str] = mapped_column(String(100), default="")
    exclusion_reason: Mapped[str] = mapped_column(Text, default="")
    job_title: Mapped[str] = mapped_column(String(255), default="")
    company: Mapped[str] = mapped_column(String(255), default="")
    job_id: Mapped[str] = mapped_column(String(160), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    usa_location_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    work_mode: Mapped[str] = mapped_column(String(80), default="")
    published_date: Mapped[str] = mapped_column(String(80), default="")
    source_type: Mapped[str] = mapped_column(String(80), default="")
    official_job_url: Mapped[str] = mapped_column(String(1000), default="")
    supporting_urls_json: Mapped[str] = mapped_column(Text, default="[]")
    primary_marketing_role: Mapped[str] = mapped_column(String(160), default="")
    primary_role_slug: Mapped[str] = mapped_column(String(120), default="")
    secondary_marketing_roles_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence_score: Mapped[int] = mapped_column(Integer, default=0)
    match_strength: Mapped[str] = mapped_column(String(80), default="")
    experience_requirement_mentioned: Mapped[bool] = mapped_column(Boolean, default=False)
    exact_experience_text_from_jd: Mapped[str] = mapped_column(Text, default="")
    minimum_years_required: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    maximum_years_required: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    experience_evidence_type: Mapped[str] = mapped_column(String(120), default="")
    estimated_experience_band: Mapped[str] = mapped_column(String(80), default="")
    experience_filter_result: Mapped[str] = mapped_column(String(120), default="")
    experience_filter_reason: Mapped[str] = mapped_column(Text, default="")
    technology_signals_json: Mapped[str] = mapped_column(Text, default="[]")
    extracted_tech_stack_json: Mapped[str] = mapped_column(Text, default="{}")
    primary_use_cases_json: Mapped[str] = mapped_column(Text, default="[]")
    role_specific_use_cases_json: Mapped[str] = mapped_column(Text, default="{}")
    resume_positioning_use_cases_json: Mapped[str] = mapped_column(Text, default="[]")
    interview_preparation_use_cases_json: Mapped[str] = mapped_column(Text, default="[]")
    why_counted: Mapped[str] = mapped_column(Text, default="")
    duplicate_check: Mapped[str] = mapped_column(Text, default="")
    duplicate_source_urls_json: Mapped[str] = mapped_column(Text, default="[]")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class PursuitContact(TimestampMixin, Base):
    __tablename__ = "pursuit_contacts"
    __table_args__ = (Index("ix_pursuit_contacts_name", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pursuit_id: Mapped[int] = mapped_column(ForeignKey("company_pursuits.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    title: Mapped[str] = mapped_column(String(255), default="")
    department: Mapped[str] = mapped_column(String(120), default="")
    location: Mapped[str] = mapped_column(String(160), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(80), default="")
    linkedin_url: Mapped[str] = mapped_column(String(1000), default="")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    confidence: Mapped[str] = mapped_column(String(20), default="")


class PursuitPrimeVendor(TimestampMixin, Base):
    __tablename__ = "pursuit_prime_vendors"
    __table_args__ = (Index("ix_pursuit_prime_vendors_name", "vendor_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pursuit_id: Mapped[int] = mapped_column(ForeignKey("company_pursuits.id", ondelete="CASCADE"), index=True)
    vendor_name: Mapped[str] = mapped_column(String(255), default="")
    relationship_evidence: Mapped[str] = mapped_column(Text, default="")
    technology_or_role_focus: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    confidence: Mapped[str] = mapped_column(String(20), default="")


class PursuitC2CManager(TimestampMixin, Base):
    __tablename__ = "pursuit_c2c_managers"
    __table_args__ = (Index("ix_pursuit_c2c_managers_name", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pursuit_id: Mapped[int] = mapped_column(ForeignKey("company_pursuits.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    company_or_vendor: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(String(255), default="")
    role_focus: Mapped[str] = mapped_column(Text, default="")
    linkedin_url: Mapped[str] = mapped_column(String(1000), default="")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    confidence: Mapped[str] = mapped_column(String(20), default="")


class PursuitEvidence(TimestampMixin, Base):
    __tablename__ = "pursuit_evidence"
    __table_args__ = (Index("ix_pursuit_evidence_kind", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pursuit_id: Mapped[int] = mapped_column(ForeignKey("company_pursuits.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(60), default="")
    label: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(20), default="")


class PursuitNote(TimestampMixin, Base):
    __tablename__ = "pursuit_notes"
    __table_args__ = (
        Index("ix_pursuit_notes_pursuit_created", "pursuit_id", "created_at"),
        Index("ix_pursuit_notes_active", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pursuit_id: Mapped[int] = mapped_column(ForeignKey("company_pursuits.id", ondelete="CASCADE"), index=True)
    author: Mapped[str] = mapped_column(String(160), default="")
    category: Mapped[str] = mapped_column(String(60), default="owner_note")
    body: Mapped[str] = mapped_column(Text, default="")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PursuitActivity(TimestampMixin, Base):
    __tablename__ = "pursuit_activities"
    __table_args__ = (Index("ix_pursuit_activities_pursuit_created", "pursuit_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pursuit_id: Mapped[int] = mapped_column(ForeignKey("company_pursuits.id", ondelete="CASCADE"), index=True)
    actor: Mapped[str] = mapped_column(String(160), default="")
    activity_type: Mapped[str] = mapped_column(String(80), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
