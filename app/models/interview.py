from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from sqlalchemy import Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.company import Base, TimestampMixin


class InterviewOutcome(str, Enum):
    UNKNOWN = "unknown"
    PASSED = "passed"
    REJECTED = "rejected"
    OFFER = "offer"
    WITHDRAWN = "withdrawn"


class InterviewExperience(TimestampMixin, Base):
    __tablename__ = "interview_experiences"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("job_opportunities.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    interview_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    rounds: Mapped[int] = mapped_column(Integer, default=0)
    difficulty: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[InterviewOutcome] = mapped_column(String(30), default=InterviewOutcome.UNKNOWN)
    summary: Mapped[str] = mapped_column(Text, default="")

    company = relationship("Company", back_populates="interviews")
    job = relationship("JobOpportunity", back_populates="interviews")
