from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.company import Base, TimestampMixin


class ConsultantProfile(TimestampMixin, Base):
    __tablename__ = "consultant_profiles"
    __table_args__ = (
        Index("ix_consultant_profiles_active", "active"),
        Index("ix_consultant_profiles_email", "email"),
        Index("ix_consultant_profiles_marketing_role_id", "marketing_role_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(80), default="")
    preferred_name: Mapped[str] = mapped_column(String(120), default="")
    linkedin_url: Mapped[str] = mapped_column(String(500), default="")
    current_location: Mapped[str] = mapped_column(String(160), default="")
    relocation_preference: Mapped[str] = mapped_column(String(160), default="")
    onsite_preference: Mapped[str] = mapped_column(String(120), default="")
    work_authorization: Mapped[str] = mapped_column(String(80), default="")
    visa_valid_until: Mapped[str] = mapped_column(String(40), default="")
    ead_valid_until: Mapped[str] = mapped_column(String(40), default="")
    years_experience: Mapped[str] = mapped_column(String(40), default="")
    professional_experience: Mapped[str] = mapped_column(Text, default="")
    domain_experience: Mapped[str] = mapped_column(Text, default="")
    marketing_role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("marketing_roles.id", ondelete="SET NULL"), nullable=True)
    target_industry_domain: Mapped[str] = mapped_column(String(120), default="")
    marketing_status: Mapped[str] = mapped_column(String(80), default="profile_intake")
    primary_skills: Mapped[str] = mapped_column(Text, default="")
    certifications: Mapped[str] = mapped_column(Text, default="")
    education_summary: Mapped[str] = mapped_column(Text, default="")
    resume_summary: Mapped[str] = mapped_column(Text, default="")
    base_resume_reference: Mapped[str] = mapped_column(String(500), default="")
    latest_project_title: Mapped[str] = mapped_column(String(220), default="")
    latest_project_domain: Mapped[str] = mapped_column(String(120), default="")
    latest_project_summary: Mapped[str] = mapped_column(Text, default="")
    resume_readiness_score: Mapped[int] = mapped_column(default=0)
    technical_readiness_score: Mapped[int] = mapped_column(default=0)
    interview_readiness_score: Mapped[int] = mapped_column(default=0)
    communication_score: Mapped[int] = mapped_column(default=0)
    profile_intake_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    education_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    certifications_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    experience_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    base_resume_received: Mapped[bool] = mapped_column(Boolean, default=False)
    resume_tailoring_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    latest_project_updated: Mapped[bool] = mapped_column(Boolean, default=False)
    project_story_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    basics_prep_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    training_plan_assigned: Mapped[bool] = mapped_column(Boolean, default=False)
    glossary_review_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    mock_interview_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    marketing_brief_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    checklist_notes: Mapped[str] = mapped_column(Text, default="")
    availability: Mapped[str] = mapped_column(String(80), default="")
    rate_expectation: Mapped[str] = mapped_column(String(80), default="")
    staff_owner: Mapped[str] = mapped_column(String(160), default="")
    recruiter_owner: Mapped[str] = mapped_column(String(160), default="")
    profile_strengths: Mapped[str] = mapped_column(Text, default="")
    profile_gaps: Mapped[str] = mapped_column(Text, default="")
    marketing_notes: Mapped[str] = mapped_column(Text, default="")
    placement_company: Mapped[str] = mapped_column(String(180), default="")
    placement_role: Mapped[str] = mapped_column(String(180), default="")
    placement_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    placement_notes: Mapped[str] = mapped_column(Text, default="")
    india_company_name: Mapped[str] = mapped_column(String(180), default="")
    india_name_as_per_aadhaar: Mapped[str] = mapped_column(String(180), default="")
    india_father_name: Mapped[str] = mapped_column(String(180), default="")
    india_dob: Mapped[str] = mapped_column(String(40), default="")
    india_pan_no: Mapped[str] = mapped_column(String(40), default="")
    india_aadhaar_no: Mapped[str] = mapped_column(String(40), default="")
    india_technology: Mapped[str] = mapped_column(String(160), default="")
    india_offer_designation: Mapped[str] = mapped_column(String(180), default="")
    india_current_designation: Mapped[str] = mapped_column(String(180), default="")
    india_offer_package: Mapped[str] = mapped_column(String(80), default="")
    india_current_package: Mapped[str] = mapped_column(String(80), default="")
    india_offer_date: Mapped[str] = mapped_column(String(40), default="")
    india_joining_date: Mapped[str] = mapped_column(String(40), default="")
    india_native_address: Mapped[str] = mapped_column(Text, default="")
    india_mail_id: Mapped[str] = mapped_column(String(255), default="")
    india_mobile_num: Mapped[str] = mapped_column(String(80), default="")
    india_resignation_date: Mapped[str] = mapped_column(String(40), default="")
    india_relieving_date: Mapped[str] = mapped_column(String(40), default="")
    india_bank_name: Mapped[str] = mapped_column(String(180), default="")
    india_bank_account_num: Mapped[str] = mapped_column(String(80), default="")
    india_pf_uan_notes: Mapped[str] = mapped_column(Text, default="")
    india_document_notes: Mapped[str] = mapped_column(Text, default="")
    maas_profile_id: Mapped[str] = mapped_column(String(120), default="")
    maas_sync_status: Mapped[str] = mapped_column(String(80), default="not_synced")
    maas_last_synced_at: Mapped[str] = mapped_column(String(80), default="")
    maas_payload_notes: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    marketing_role = relationship("MarketingRole")
    resume_versions = relationship("ResumeVersion", back_populates="consultant", cascade="all, delete-orphan")
    submissions = relationship("ConsultantSubmission", back_populates="consultant", cascade="all, delete-orphan")
    mock_interviews = relationship("MockInterview", back_populates="consultant", cascade="all, delete-orphan")

    @property
    def marketing_readiness_count(self) -> int:
        return sum(
            bool(value)
            for value in (
                self.profile_intake_complete,
                self.education_verified,
                self.certifications_verified,
                self.experience_verified,
                self.base_resume_received,
                self.resume_tailoring_complete,
                self.latest_project_updated,
                self.project_story_validated,
                self.basics_prep_complete,
                self.training_plan_assigned,
                self.glossary_review_complete,
                self.mock_interview_passed,
                self.marketing_brief_ready,
            )
        )

    @property
    def marketing_readiness_percent(self) -> int:
        return round(self.marketing_readiness_count / 13 * 100)
