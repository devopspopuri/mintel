from decimal import Decimal
from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CompanyBase(BaseModel):
    name: str
    website: str = ""
    linkedin_url: str = ""
    careers_url: str = ""
    ats_api_url: str = ""
    ats_type: str = ""
    ats_platform: str = ""
    location: str = ""
    industry: str = ""
    headquarters_city: str = ""
    headquarters_state: str = ""
    managed_by_id: Optional[int] = None
    application_time_minutes: int = 0
    requires_account_creation: bool = False
    requires_email_verification: bool = False
    accepts_cover_letter: bool = False
    onsite_interview_required: bool = False
    opt_status: str = "unknown"
    stem_opt_status: str = "unknown"
    sponsorship_status: str = "unknown"
    opt_risk: str = "low"
    opt_recent_hires: int = 0
    h1b_filings_recent: int = 0
    opt_last_verified: Optional[date] = None
    opt_notes: str = ""
    tech_stack: str = ""
    background_process: str = ""
    submission_guidance: str = ""
    notes: str = ""


class CompanyCreate(CompanyBase):
    pass


class CompanyRead(CompanyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    sponsorship_tier: str
    h1b_approval_count: int
    h1b_denial_count: int
    opt_friendly: bool
    opt_friendliness_score: Decimal


class CompanyList(BaseModel):
    items: list[CompanyRead]
    total: int
