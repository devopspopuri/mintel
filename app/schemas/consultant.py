from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ConsultantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    preferred_name: str
    email: str
    phone: str
    linkedin_url: str
    current_location: str
    relocation_preference: str
    onsite_preference: str
    work_authorization: str
    visa_valid_until: str
    ead_valid_until: str
    years_experience: str
    professional_experience: str
    domain_experience: str
    target_industry_domain: str
    marketing_status: str
    primary_skills: str
    certifications: str
    education_summary: str
    resume_summary: str
    base_resume_reference: str
    latest_project_title: str
    latest_project_domain: str
    latest_project_summary: str
    resume_readiness_score: int
    technical_readiness_score: int
    interview_readiness_score: int
    communication_score: int
    marketing_readiness_percent: int
    availability: str
    rate_expectation: str
    staff_owner: str
    recruiter_owner: str
    profile_strengths: str
    profile_gaps: str
    marketing_notes: str
    placement_company: str
    placement_role: str
    placement_start_date: Optional[date]
    placement_notes: str
    maas_profile_id: str
    maas_sync_status: str
    maas_last_synced_at: str
    maas_payload_notes: str
    active: bool


class ConsultantExchangeRead(ConsultantRead):
    marketing_role: str
    checklist: dict[str, bool]
    activity: dict[str, int]


class ConsultantList(BaseModel):
    items: list[ConsultantExchangeRead]
    total: int
