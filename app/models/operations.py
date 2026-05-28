from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.company import Base, TimestampMixin


class SubmissionStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    CLIENT_REVIEW = "client_review"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class MockInterviewStatus(str, Enum):
    PLANNED = "planned"
    PENDING_ACK = "pending_ack"
    RESCHEDULE_REQUESTED = "reschedule_requested"
    CANCELLATION_REQUESTED = "cancellation_requested"
    WAITING_FEEDBACK = "waiting_feedback"
    COMPLETED = "completed"
    NEEDS_WORK = "needs_work"
    MARKET_READY = "market_ready"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class TargetingCampaignStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class TargetingCampaignTargetStatus(str, Enum):
    QUEUED = "queued"
    RESEARCH = "research"
    RESUME_TAILORING = "resume_tailoring"
    READY_TO_SUBMIT = "ready_to_submit"
    SUBMITTED = "submitted"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class ConsultantJourneyStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    PLACED = "placed"
    POST_PLACEMENT = "post_placement"
    ARCHIVED = "archived"


class ConsultantJourneyActivityStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class ResumeVersion(TimestampMixin, Base):
    __tablename__ = "resume_versions"
    __table_args__ = (
        Index("ix_resume_versions_consultant", "consultant_id"),
        Index("ix_resume_versions_active", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    consultant_id: Mapped[int] = mapped_column(ForeignKey("consultant_profiles.id", ondelete="CASCADE"), index=True)
    version_name: Mapped[str] = mapped_column(String(160), default="")
    base_resume_name: Mapped[str] = mapped_column(String(255), default="")
    target_role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("marketing_roles.id", ondelete="SET NULL"), nullable=True)
    target_domain: Mapped[str] = mapped_column(String(120), default="")
    target_job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("job_opportunities.id", ondelete="SET NULL"), nullable=True)
    latest_project_update: Mapped[str] = mapped_column(Text, default="")
    supporting_project_improvements: Mapped[str] = mapped_column(Text, default="")
    ats_score: Mapped[int] = mapped_column(Integer, default=0)
    tailoring_notes: Mapped[str] = mapped_column(Text, default="")
    file_reference: Mapped[str] = mapped_column(String(500), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    consultant = relationship("ConsultantProfile", back_populates="resume_versions")
    target_role = relationship("MarketingRole")
    target_job = relationship("JobOpportunity")
    submissions = relationship("ConsultantSubmission", back_populates="resume_version")


class ConsultantSubmission(TimestampMixin, Base):
    __tablename__ = "consultant_submissions"
    __table_args__ = (
        Index("ix_consultant_submissions_consultant", "consultant_id"),
        Index("ix_consultant_submissions_job", "job_id"),
        Index("ix_consultant_submissions_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    consultant_id: Mapped[int] = mapped_column(ForeignKey("consultant_profiles.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_opportunities.id", ondelete="CASCADE"), index=True)
    resume_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"), nullable=True)
    submitted_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[SubmissionStatus] = mapped_column(String(40), default=SubmissionStatus.DRAFT)
    vendor_contact: Mapped[str] = mapped_column(String(255), default="")
    bill_rate: Mapped[str] = mapped_column(String(80), default="")
    submission_notes: Mapped[str] = mapped_column(Text, default="")
    next_step: Mapped[str] = mapped_column(Text, default="")

    consultant = relationship("ConsultantProfile", back_populates="submissions")
    job = relationship("JobOpportunity", back_populates="submissions")
    resume_version = relationship("ResumeVersion", back_populates="submissions")
    mock_interviews = relationship("MockInterview", back_populates="submission")


class TargetingCampaign(TimestampMixin, Base):
    __tablename__ = "targeting_campaigns"
    __table_args__ = (
        Index("ix_targeting_campaigns_consultant", "consultant_id"),
        Index("ix_targeting_campaigns_status", "status"),
        Index("ix_targeting_campaigns_owner", "owner_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    consultant_id: Mapped[int] = mapped_column(ForeignKey("consultant_profiles.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(220), default="")
    target_role: Mapped[str] = mapped_column(String(160), default="")
    target_region: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(40), default=TargetingCampaignStatus.PLANNED.value)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    goal_count: Mapped[int] = mapped_column(Integer, default=25)
    min_match_score: Mapped[int] = mapped_column(Integer, default=35)
    notes: Mapped[str] = mapped_column(Text, default="")

    consultant = relationship("ConsultantProfile")
    owner = relationship("User")
    targets = relationship("TargetingCampaignTarget", back_populates="campaign", cascade="all, delete-orphan")


class TargetingCampaignTarget(TimestampMixin, Base):
    __tablename__ = "targeting_campaign_targets"
    __table_args__ = (
        Index("ix_targeting_campaign_targets_campaign", "campaign_id"),
        Index("ix_targeting_campaign_targets_company", "company_id"),
        Index("ix_targeting_campaign_targets_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("targeting_campaigns.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    pursuit_id: Mapped[Optional[int]] = mapped_column(ForeignKey("company_pursuits.id", ondelete="SET NULL"), nullable=True)
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("job_opportunities.id", ondelete="SET NULL"), nullable=True)
    submission_id: Mapped[Optional[int]] = mapped_column(ForeignKey("consultant_submissions.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default=TargetingCampaignTargetStatus.QUEUED.value)
    match_score: Mapped[int] = mapped_column(Integer, default=0)
    company_score: Mapped[int] = mapped_column(Integer, default=0)
    role_fit: Mapped[str] = mapped_column(Text, default="")
    skill_overlap: Mapped[str] = mapped_column(Text, default="")
    gaps: Mapped[str] = mapped_column(Text, default="")
    next_action: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    campaign = relationship("TargetingCampaign", back_populates="targets")
    company = relationship("Company")
    pursuit = relationship("CompanyPursuit")
    job = relationship("JobOpportunity")
    submission = relationship("ConsultantSubmission")


class ConsultantRoleJourney(TimestampMixin, Base):
    __tablename__ = "consultant_role_journeys"
    __table_args__ = (
        Index("ix_consultant_role_journeys_consultant", "consultant_id"),
        Index("ix_consultant_role_journeys_status", "status"),
        Index("ix_consultant_role_journeys_stage", "current_stage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    consultant_id: Mapped[int] = mapped_column(ForeignKey("consultant_profiles.id", ondelete="CASCADE"), index=True)
    marketing_role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("marketing_roles.id", ondelete="SET NULL"), nullable=True, index=True)
    training_program_id: Mapped[Optional[int]] = mapped_column(ForeignKey("training_programs.id", ondelete="SET NULL"), nullable=True, index=True)
    assigned_staff_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    assigned_trainer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    target_domain: Mapped[str] = mapped_column(String(120), default="")
    current_stage: Mapped[str] = mapped_column(String(80), default="role_intake")
    status: Mapped[str] = mapped_column(String(40), default=ConsultantJourneyStatus.ACTIVE.value)
    target_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    target_market_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    readiness_score: Mapped[int] = mapped_column(Integer, default=0)
    blocker_summary: Mapped[str] = mapped_column(Text, default="")
    positioning_summary: Mapped[str] = mapped_column(Text, default="")
    next_action: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    consultant = relationship("ConsultantProfile")
    marketing_role = relationship("MarketingRole")
    training_program = relationship("TrainingProgram")
    assigned_staff = relationship("User", foreign_keys=[assigned_staff_id])
    assigned_trainer = relationship("User", foreign_keys=[assigned_trainer_id])
    activities = relationship("ConsultantJourneyActivity", back_populates="journey", cascade="all, delete-orphan")


class ConsultantJourneyActivity(TimestampMixin, Base):
    __tablename__ = "consultant_journey_activities"
    __table_args__ = (
        Index("ix_consultant_journey_activities_journey", "journey_id"),
        Index("ix_consultant_journey_activities_status", "status"),
        Index("ix_consultant_journey_activities_stage", "stage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    journey_id: Mapped[int] = mapped_column(ForeignKey("consultant_role_journeys.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    key: Mapped[str] = mapped_column(String(80), default="")
    stage: Mapped[str] = mapped_column(String(80), default="")
    title: Mapped[str] = mapped_column(String(220), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default=ConsultantJourneyActivityStatus.TODO.value)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    evidence_url: Mapped[str] = mapped_column(String(1000), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    journey = relationship("ConsultantRoleJourney", back_populates="activities")
    owner = relationship("User", foreign_keys=[owner_id])
    completed_by = relationship("User", foreign_keys=[completed_by_id])


class MockInterview(TimestampMixin, Base):
    __tablename__ = "mock_interviews"
    __table_args__ = (
        Index("ix_mock_interviews_consultant", "consultant_id"),
        Index("ix_mock_interviews_status", "status"),
        Index("ix_mock_interviews_marketing_role", "marketing_role_id"),
        Index("ix_mock_interviews_assigned_staff", "assigned_staff_id"),
        Index("ix_mock_interviews_scheduled_on", "scheduled_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    consultant_id: Mapped[int] = mapped_column(ForeignKey("consultant_profiles.id", ondelete="CASCADE"), index=True)
    submission_id: Mapped[Optional[int]] = mapped_column(ForeignKey("consultant_submissions.id", ondelete="SET NULL"), nullable=True)
    training_program_id: Mapped[Optional[int]] = mapped_column(ForeignKey("training_programs.id", ondelete="SET NULL"), nullable=True)
    marketing_role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("marketing_roles.id", ondelete="SET NULL"), nullable=True)
    assigned_staff_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    scheduled_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    scheduled_time: Mapped[str] = mapped_column(String(40), default="")
    timezone: Mapped[str] = mapped_column(String(80), default="America/Chicago")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    meeting_link: Mapped[str] = mapped_column(String(500), default="")
    round_type: Mapped[str] = mapped_column(String(80), default="mock")
    interviewer_name: Mapped[str] = mapped_column(String(160), default="")
    role_snapshot: Mapped[str] = mapped_column(String(160), default="")
    domain_snapshot: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[MockInterviewStatus] = mapped_column(String(40), default=MockInterviewStatus.PLANNED)
    score: Mapped[int] = mapped_column(Integer, default=0)
    strengths: Mapped[str] = mapped_column(Text, default="")
    gaps: Mapped[str] = mapped_column(Text, default="")
    action_items: Mapped[str] = mapped_column(Text, default="")
    question_coverage: Mapped[str] = mapped_column(Text, default="")
    questions_asked: Mapped[str] = mapped_column(Text, default="")
    prep_pack_snapshot: Mapped[str] = mapped_column(Text, default="")
    consultant_ack_status: Mapped[str] = mapped_column(String(40), default="not_required")
    consultant_acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consultant_acknowledged_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    consultant_ack_note: Mapped[str] = mapped_column(Text, default="")
    request_note: Mapped[str] = mapped_column(Text, default="")
    conflict_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    conflict_override_reason: Mapped[str] = mapped_column(Text, default="")

    consultant = relationship("ConsultantProfile", back_populates="mock_interviews")
    submission = relationship("ConsultantSubmission", back_populates="mock_interviews")
    training_program = relationship("TrainingProgram")
    marketing_role = relationship("MarketingRole")
    assigned_staff = relationship("User", foreign_keys=[assigned_staff_id])
    consultant_acknowledged_by = relationship("User", foreign_keys=[consultant_acknowledged_by_id])
    status_events = relationship("MockInterviewStatusEvent", back_populates="mock_interview", cascade="all, delete-orphan")


class TrainerWeeklyAvailability(TimestampMixin, Base):
    __tablename__ = "trainer_weekly_availability"
    __table_args__ = (
        Index("ix_trainer_weekly_availability_staff", "staff_id"),
        Index("ix_trainer_weekly_availability_weekday", "weekday"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    timezone: Mapped[str] = mapped_column(String(80), default="America/Chicago")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(String(255), default="")

    staff = relationship("User")


class TrainerAdhocAvailability(TimestampMixin, Base):
    __tablename__ = "trainer_adhoc_availability"
    __table_args__ = (
        Index("ix_trainer_adhoc_availability_staff", "staff_id"),
        Index("ix_trainer_adhoc_availability_start", "start_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(80), default="America/Chicago")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(String(255), default="")

    staff = relationship("User")


class ConsultantAvailabilityBlock(TimestampMixin, Base):
    __tablename__ = "consultant_availability_blocks"
    __table_args__ = (
        Index("ix_consultant_availability_blocks_consultant", "consultant_id"),
        Index("ix_consultant_availability_blocks_start", "start_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    consultant_id: Mapped[int] = mapped_column(ForeignKey("consultant_profiles.id", ondelete="CASCADE"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(80), default="America/Chicago")
    reason: Mapped[str] = mapped_column(String(160), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    consultant = relationship("ConsultantProfile")
    created_by = relationship("User")


class MockInterviewStatusEvent(TimestampMixin, Base):
    __tablename__ = "mock_interview_status_events"
    __table_args__ = (
        Index("ix_mock_interview_status_events_mock", "mock_interview_id"),
        Index("ix_mock_interview_status_events_actor", "actor_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mock_interview_id: Mapped[int] = mapped_column(ForeignKey("mock_interviews.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(80), default="update")
    from_status: Mapped[str] = mapped_column(String(40), default="")
    to_status: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(Text, default="")

    mock_interview = relationship("MockInterview", back_populates="status_events")
    actor = relationship("User")
