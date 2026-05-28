from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.company import Base, TimestampMixin


class TrainingProgram(TimestampMixin, Base):
    __tablename__ = "training_programs"
    __table_args__ = (
        UniqueConstraint("marketing_role_id", "industry_domain", name="uq_training_programs_role_domain"),
        Index("ix_training_programs_active", "active"),
        Index("ix_training_programs_industry_domain", "industry_domain"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    marketing_role_id: Mapped[int] = mapped_column(ForeignKey("marketing_roles.id", ondelete="CASCADE"), index=True)
    industry_domain: Mapped[str] = mapped_column(String(120), default="")
    short_description: Mapped[str] = mapped_column(Text, default="")
    enterprise_context: Mapped[str] = mapped_column(Text, default="")
    application_landscape_json: Mapped[str] = mapped_column(Text, default="[]")
    cloud_architecture_json: Mapped[str] = mapped_column(Text, default="{}")
    project_responsibilities_json: Mapped[str] = mapped_column(Text, default="[]")
    three_year_delivery_timeline_json: Mapped[str] = mapped_column(Text, default="{}")
    key_deliverables_json: Mapped[str] = mapped_column(Text, default="[]")
    tools_and_technologies_json: Mapped[str] = mapped_column(Text, default="[]")
    interview_story: Mapped[str] = mapped_column(Text, default="")
    resume_project_summary: Mapped[str] = mapped_column(Text, default="")
    production_support_scenarios_json: Mapped[str] = mapped_column(Text, default="[]")
    interview_questions_json: Mapped[str] = mapped_column(Text, default="[]")
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(180), default="")
    duration_weeks: Mapped[int] = mapped_column(Integer, default=6)
    target_audience: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(Text, default="")
    vocabulary_plan: Mapped[str] = mapped_column(Text, default="")
    concepts_plan: Mapped[str] = mapped_column(Text, default="")
    usecases_plan: Mapped[str] = mapped_column(Text, default="")
    interview_plan: Mapped[str] = mapped_column(Text, default="")
    resume_plan: Mapped[str] = mapped_column(Text, default="")
    labs_plan: Mapped[str] = mapped_column(Text, default="")
    readiness_checklist: Mapped[str] = mapped_column(Text, default="")
    missing_areas: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    marketing_role = relationship("MarketingRole")
    job_descriptions: Mapped[list["TrainingJobDescription"]] = relationship(back_populates="program", cascade="all, delete-orphan")

    @property
    def application_landscape(self) -> list[str]:
        return _json_list(self.application_landscape_json)

    @property
    def cloud_architecture(self) -> dict[str, Any]:
        return _json_dict(self.cloud_architecture_json)

    @property
    def project_responsibilities(self) -> list[str]:
        return _json_list(self.project_responsibilities_json)

    @property
    def three_year_delivery_timeline(self) -> dict[str, list[str]]:
        return _json_dict(self.three_year_delivery_timeline_json)

    @property
    def key_deliverables(self) -> list[str]:
        return _json_list(self.key_deliverables_json)

    @property
    def tools_and_technologies(self) -> list[str]:
        return _json_list(self.tools_and_technologies_json)

    @property
    def production_support_scenarios(self) -> list[str]:
        return _json_list(self.production_support_scenarios_json)

    @property
    def interview_questions(self) -> list[str]:
        return _json_list(self.interview_questions_json)


class TrainingJobDescription(TimestampMixin, Base):
    __tablename__ = "training_job_descriptions"
    __table_args__ = (
        Index("ix_training_job_descriptions_program", "program_id"),
        Index("ix_training_job_descriptions_pattern", "pattern_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("training_programs.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    pattern_type: Mapped[str] = mapped_column(String(80), default="")
    title: Mapped[str] = mapped_column(String(220), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    responsibilities: Mapped[str] = mapped_column(Text, default="")
    required_skills: Mapped[str] = mapped_column(Text, default="")
    nice_to_have: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str] = mapped_column(String(120), default="")
    difficulty: Mapped[str] = mapped_column(String(40), default="")
    work_auth_signal: Mapped[str] = mapped_column(Text, default="")

    program: Mapped[TrainingProgram] = relationship(back_populates="job_descriptions")


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_dict(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
