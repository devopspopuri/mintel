from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.company import Base, TimestampMixin


class CaseStatus(str, Enum):
    CERTIFIED = "certified"
    CERTIFIED_WITHDRAWN = "certified_withdrawn"
    DENIED = "denied"
    WITHDRAWN = "withdrawn"
    UNKNOWN = "unknown"


class H1BDisclosure(TimestampMixin, Base):
    __tablename__ = "h1b_disclosures"
    __table_args__ = (
        Index("uq_h1b_case_number_fiscal_year", "case_number", "fiscal_year", unique=True, postgresql_where=sa.text("case_number <> ''")),
        Index("ix_h1b_fiscal_year_status", "fiscal_year", "case_status"),
        Index("ix_h1b_worksite_state", "worksite_state"),
        Index("ix_h1b_job_title", "job_title"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    fiscal_year: Mapped[int] = mapped_column(index=True)
    case_number: Mapped[str] = mapped_column(String(80), default="")
    employer_name_raw: Mapped[str] = mapped_column(String(255))
    job_title: Mapped[str] = mapped_column(String(255), default="")
    soc_code: Mapped[str] = mapped_column(String(30), default="")
    soc_title: Mapped[str] = mapped_column(String(255), default="")
    case_status: Mapped[CaseStatus] = mapped_column(String(30), default=CaseStatus.UNKNOWN)
    worksite_city: Mapped[str] = mapped_column(String(120), default="")
    worksite_state: Mapped[str] = mapped_column(String(80), default="")
    wage_rate_from: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    wage_unit: Mapped[str] = mapped_column(String(40), default="")
    source_file: Mapped[str] = mapped_column(String(255), default="")

    company = relationship("Company", back_populates="h1b_disclosures")
