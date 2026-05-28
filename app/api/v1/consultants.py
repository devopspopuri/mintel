from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.consultant import ConsultantProfile
from app.models.pursuit_intelligence import MarketingRole
from app.schemas.consultant import ConsultantExchangeRead, ConsultantList


router = APIRouter()


@router.get("", response_model=ConsultantList)
def list_consultants(
    q: str = Query(default=""),
    marketing_role_id: Optional[int] = Query(default=None),
    maas_profile_id: str = Query(default=""),
    active: Optional[bool] = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ConsultantList:
    query = select(ConsultantProfile).outerjoin(MarketingRole)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(ConsultantProfile.name).like(pattern),
                func.lower(ConsultantProfile.email).like(pattern),
                func.lower(ConsultantProfile.primary_skills).like(pattern),
                func.lower(ConsultantProfile.professional_experience).like(pattern),
                func.lower(ConsultantProfile.education_summary).like(pattern),
                func.lower(ConsultantProfile.certifications).like(pattern),
            )
        )
    if marketing_role_id:
        query = query.where(ConsultantProfile.marketing_role_id == marketing_role_id)
    if maas_profile_id:
        query = query.where(ConsultantProfile.maas_profile_id == maas_profile_id.strip())
    if active is not None:
        query = query.where(ConsultantProfile.active.is_(active))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(ConsultantProfile.updated_at.desc(), ConsultantProfile.name).limit(limit).offset(offset)).all()
    return ConsultantList(items=[_consultant_exchange_payload(row) for row in rows], total=total)


@router.get("/{consultant_id}", response_model=ConsultantExchangeRead)
def get_consultant(consultant_id: int, db: Session = Depends(get_db)) -> ConsultantExchangeRead:
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant:
        raise HTTPException(status_code=404, detail="Consultant not found")
    return _consultant_exchange_payload(consultant)


def _consultant_exchange_payload(consultant: ConsultantProfile) -> ConsultantExchangeRead:
    return ConsultantExchangeRead(
        id=consultant.id,
        name=consultant.name,
        preferred_name=consultant.preferred_name,
        email=consultant.email,
        phone=consultant.phone,
        linkedin_url=consultant.linkedin_url,
        current_location=consultant.current_location,
        relocation_preference=consultant.relocation_preference,
        onsite_preference=consultant.onsite_preference,
        work_authorization=consultant.work_authorization,
        visa_valid_until=consultant.visa_valid_until,
        ead_valid_until=consultant.ead_valid_until,
        years_experience=consultant.years_experience,
        professional_experience=consultant.professional_experience,
        domain_experience=consultant.domain_experience,
        target_industry_domain=consultant.target_industry_domain,
        marketing_status=consultant.marketing_status,
        primary_skills=consultant.primary_skills,
        certifications=consultant.certifications,
        education_summary=consultant.education_summary,
        resume_summary=consultant.resume_summary,
        base_resume_reference=consultant.base_resume_reference,
        latest_project_title=consultant.latest_project_title,
        latest_project_domain=consultant.latest_project_domain,
        latest_project_summary=consultant.latest_project_summary,
        resume_readiness_score=consultant.resume_readiness_score,
        technical_readiness_score=consultant.technical_readiness_score,
        interview_readiness_score=consultant.interview_readiness_score,
        communication_score=consultant.communication_score,
        marketing_readiness_percent=consultant.marketing_readiness_percent,
        availability=consultant.availability,
        rate_expectation=consultant.rate_expectation,
        staff_owner=consultant.staff_owner,
        recruiter_owner=consultant.recruiter_owner,
        profile_strengths=consultant.profile_strengths,
        profile_gaps=consultant.profile_gaps,
        marketing_notes=consultant.marketing_notes,
        placement_company=consultant.placement_company,
        placement_role=consultant.placement_role,
        placement_start_date=consultant.placement_start_date,
        placement_notes=consultant.placement_notes,
        maas_profile_id=consultant.maas_profile_id,
        maas_sync_status=consultant.maas_sync_status,
        maas_last_synced_at=consultant.maas_last_synced_at,
        maas_payload_notes=consultant.maas_payload_notes,
        active=consultant.active,
        marketing_role=consultant.marketing_role.name if consultant.marketing_role else "",
        checklist={
            "profile_intake_complete": consultant.profile_intake_complete,
            "education_verified": consultant.education_verified,
            "certifications_verified": consultant.certifications_verified,
            "experience_verified": consultant.experience_verified,
            "base_resume_received": consultant.base_resume_received,
            "resume_tailoring_complete": consultant.resume_tailoring_complete,
            "latest_project_updated": consultant.latest_project_updated,
            "project_story_validated": consultant.project_story_validated,
            "basics_prep_complete": consultant.basics_prep_complete,
            "training_plan_assigned": consultant.training_plan_assigned,
            "glossary_review_complete": consultant.glossary_review_complete,
            "mock_interview_passed": consultant.mock_interview_passed,
            "marketing_brief_ready": consultant.marketing_brief_ready,
        },
        activity={
            "resume_versions": len(consultant.resume_versions),
            "submissions": len(consultant.submissions),
            "mock_interviews": len(consultant.mock_interviews),
        },
    )
