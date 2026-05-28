from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.company import Base, TimestampMixin


class UscisDecisionType(str, Enum):
    ALL = "all"
    NEW_EMPLOYMENT = "new_employment"
    CONTINUATION = "continuation"
    CHANGE_SAME_EMPLOYER = "change_same_employer"
    NEW_CONCURRENT = "new_concurrent"
    CHANGE_EMPLOYER = "change_employer"
    AMENDED = "amended"


class UscisEmployerYearlyStat(TimestampMixin, Base):
    __tablename__ = "uscis_employer_yearly_stats"
    __table_args__ = (
        UniqueConstraint(
            "fiscal_year",
            "normalized_employer_name",
            "tax_id",
            "naics_code",
            "petitioner_city",
            "petitioner_state",
            name="uq_uscis_employer_yearly_identity",
        ),
        Index("ix_uscis_normalized_year", "normalized_employer_name", "fiscal_year"),
        Index("ix_uscis_year_state", "fiscal_year", "petitioner_state"),
        Index("ix_uscis_year_naics", "fiscal_year", "naics_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, index=True)
    employer_name: Mapped[str] = mapped_column(String(255), index=True)
    normalized_employer_name: Mapped[str] = mapped_column(String(255), index=True)
    tax_id: Mapped[str] = mapped_column(String(64), default="")
    naics_code: Mapped[str] = mapped_column(String(32), default="", index=True)
    naics_label: Mapped[str] = mapped_column(String(255), default="")
    petitioner_city: Mapped[str] = mapped_column(String(128), default="", index=True)
    petitioner_state: Mapped[str] = mapped_column(String(8), default="", index=True)
    petitioner_zip_code: Mapped[str] = mapped_column(String(16), default="")
    new_employment_approval: Mapped[int] = mapped_column(Integer, default=0)
    new_employment_denial: Mapped[int] = mapped_column(Integer, default=0)
    continuation_approval: Mapped[int] = mapped_column(Integer, default=0)
    continuation_denial: Mapped[int] = mapped_column(Integer, default=0)
    change_same_employer_approval: Mapped[int] = mapped_column(Integer, default=0)
    change_same_employer_denial: Mapped[int] = mapped_column(Integer, default=0)
    new_concurrent_approval: Mapped[int] = mapped_column(Integer, default=0)
    new_concurrent_denial: Mapped[int] = mapped_column(Integer, default=0)
    change_employer_approval: Mapped[int] = mapped_column(Integer, default=0)
    change_employer_denial: Mapped[int] = mapped_column(Integer, default=0)
    amended_approval: Mapped[int] = mapped_column(Integer, default=0)
    amended_denial: Mapped[int] = mapped_column(Integer, default=0)
    total_approvals: Mapped[int] = mapped_column(Integer, default=0, index=True)
    total_denials: Mapped[int] = mapped_column(Integer, default=0, index=True)
    total_decisions: Mapped[int] = mapped_column(Integer, default=0, index=True)
    source_file: Mapped[str] = mapped_column(String(255), default="")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    company = relationship("Company", back_populates="uscis_yearly_stats")


class UscisImportJob(TimestampMixin, Base):
    __tablename__ = "uscis_import_jobs"
    __table_args__ = (
        Index("ix_uscis_import_jobs_status", "status"),
        Index("ix_uscis_import_jobs_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file: Mapped[str] = mapped_column(String(255), default="")
    stored_path: Mapped[str] = mapped_column(String(1000), default="")
    requested_by: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    processed_rows: Mapped[int] = mapped_column(Integer, default=0)
    imported: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
