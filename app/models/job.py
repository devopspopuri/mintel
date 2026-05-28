from datetime import date
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.company import Base, TimestampMixin


class JobSource(str, Enum):
    CAREERS_PAGE = "careers_page"
    ATS = "ats"
    REFERRAL = "referral"
    STAFF_MANUAL = "staff_manual"
    OTHER = "other"


class JobOpportunity(TimestampMixin, Base):
    __tablename__ = "job_opportunities"
    __table_args__ = (
        Index("ix_jobs_active", "active"),
        Index("ix_jobs_title", "title"),
        Index("uq_jobs_company_requirement_key", "company_id", "requirement_key", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    requirement_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    marketing_role_ids: Mapped[str] = mapped_column(String(500), default="")
    additional_cloud_specializations: Mapped[str] = mapped_column(String(120), default="")
    certifications_required: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(160), default="")
    job_type: Mapped[str] = mapped_column(String(40), default="")
    experience_level: Mapped[str] = mapped_column(String(20), default="")
    source: Mapped[JobSource] = mapped_column(String(40), default=JobSource.OTHER)
    source_type: Mapped[str] = mapped_column(String(40), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    posted_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    ats_platform: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    decision_payload: Mapped[str] = mapped_column(Text, default="")
    sponsorship_notes: Mapped[str] = mapped_column(Text, default="")
    approval_status: Mapped[str] = mapped_column(String(40), default="pending")
    created_by: Mapped[str] = mapped_column(String(255), default="")
    job_alerts_created: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    company = relationship("Company", back_populates="jobs")
    interviews = relationship("InterviewExperience", back_populates="job")
    submissions = relationship("ConsultantSubmission", back_populates="job", cascade="all, delete-orphan")
