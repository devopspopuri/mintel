from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.pursuit_intelligence import MarketingRole
from app.models.training import TrainingProgram
from app.schemas.training import TrainingProgramList, TrainingProgramRead
from app.services.training_programs import INDUSTRY_DOMAINS, MARKETING_ROLE_NAMES


router = APIRouter()


@router.get("", response_model=TrainingProgramList)
def list_training_programs(
    marketingRole: str = Query(default=""),
    industryDomain: str = Query(default=""),
    search: str = Query(default=""),
    db: Session = Depends(get_db),
) -> TrainingProgramList:
    query = select(TrainingProgram).join(MarketingRole).where(TrainingProgram.active.is_(True))
    if marketingRole:
        query = query.where(MarketingRole.name == marketingRole)
    if industryDomain:
        query = query.where(TrainingProgram.industry_domain == industryDomain)
    if search:
        pattern = f"%{search.strip().lower()}%"
        query = query.where(
            func.lower(TrainingProgram.title).like(pattern)
            | func.lower(TrainingProgram.short_description).like(pattern)
            | func.lower(TrainingProgram.enterprise_context).like(pattern)
            | func.lower(TrainingProgram.cloud_architecture_json).like(pattern)
            | func.lower(TrainingProgram.tools_and_technologies_json).like(pattern)
            | func.lower(TrainingProgram.key_deliverables_json).like(pattern)
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(TrainingProgram.display_order.asc(), MarketingRole.name.asc(), TrainingProgram.industry_domain.asc())).all()
    return TrainingProgramList(
        items=[_program_payload(program) for program in rows],
        total=total,
        filters={"marketingRoles": MARKETING_ROLE_NAMES, "industryDomains": INDUSTRY_DOMAINS},
    )


@router.get("/{program_id}", response_model=TrainingProgramRead)
def get_training_program(program_id: int, db: Session = Depends(get_db)) -> TrainingProgramRead:
    program = db.get(TrainingProgram, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Training program not found")
    return _program_payload(program)


def _program_payload(program: TrainingProgram) -> TrainingProgramRead:
    return TrainingProgramRead(
        id=program.id,
        marketingRole=program.marketing_role.name,
        industryDomain=program.industry_domain,
        title=program.title,
        shortDescription=program.short_description,
        enterpriseContext=program.enterprise_context,
        applicationLandscape=program.application_landscape,
        cloudArchitecture=program.cloud_architecture,
        projectResponsibilities=program.project_responsibilities,
        threeYearDeliveryTimeline=program.three_year_delivery_timeline,
        keyDeliverables=program.key_deliverables,
        toolsAndTechnologies=program.tools_and_technologies,
        interviewStory=program.interview_story,
        resumeProjectSummary=program.resume_project_summary,
        productionSupportScenarios=program.production_support_scenarios,
        interviewQuestions=program.interview_questions,
        displayOrder=program.display_order,
        isActive=program.active,
    )
