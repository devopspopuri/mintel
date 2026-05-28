from __future__ import annotations

from collections import Counter
from datetime import date
import re
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.company import Company, CompanyPursuit, PursuitStatus, Region
from app.models.consultant import ConsultantProfile
from app.models.job import JobOpportunity
from app.models.operations import ConsultantSubmission, MockInterview, SubmissionStatus, TargetingCampaign
from app.models.pursuit_intelligence import MarketingRole, PursuitJobPostingEvidence, PursuitRequirement
from app.models.training import TrainingProgram
from app.models.uscis import UscisEmployerYearlyStat
from app.models.user import StaffMarketingRoleAssignment, StaffRegionAssignment, User
from app.services.operating_rules import MARKETING_READY_STATUSES, marketing_ready_context


ACTIVE_SUBMISSION_VALUES = {
    SubmissionStatus.SUBMITTED.value,
    SubmissionStatus.CLIENT_REVIEW.value,
    SubmissionStatus.INTERVIEW.value,
    SubmissionStatus.OFFER.value,
}


def build_landing_index(db: Session, user: User) -> dict[str, Any]:
    regions = db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all()
    roles = db.scalars(select(MarketingRole).where(MarketingRole.active.is_(True)).order_by(MarketingRole.name)).all()
    consultant = _consultant_for_user(db, user)
    return {
        "regions": regions,
        "roles": roles,
        "consultant": consultant,
        "summary": {
            "regions": len(regions),
            "roles": len(roles),
            "consultants": db.scalar(select(func.count(ConsultantProfile.id)).where(ConsultantProfile.active.is_(True))) or 0,
            "promoted_companies": db.scalar(select(func.count(CompanyPursuit.id)).where(CompanyPursuit.status != PursuitStatus.CLOSED.value)) or 0,
        },
    }


def build_admin_landing(db: Session) -> dict[str, Any]:
    today = date.today()
    role_rows = [{"role": role, "summary": _role_summary(db, role)} for role in db.scalars(select(MarketingRole).where(MarketingRole.active.is_(True)).order_by(MarketingRole.name)).all()]
    region_rows = [{"region": region, "summary": _region_summary(db, region, today=today)} for region in db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all()]
    consultant_total = db.scalar(select(func.count(ConsultantProfile.id)).where(ConsultantProfile.active.is_(True))) or 0
    ready_total = db.scalar(select(func.count(ConsultantProfile.id)).where(ConsultantProfile.active.is_(True), ConsultantProfile.marketing_status.in_(MARKETING_READY_STATUSES))) or 0
    return {
        "summary": {
            "regions": len(region_rows),
            "roles": len(role_rows),
            "consultants": consultant_total,
            "marketing_ready": ready_total,
            "active_submissions": db.scalar(select(func.count(ConsultantSubmission.id)).where(ConsultantSubmission.status.in_(ACTIVE_SUBMISSION_VALUES))) or 0,
            "promoted_companies": db.scalar(select(func.count(CompanyPursuit.id)).where(CompanyPursuit.status != PursuitStatus.CLOSED.value)) or 0,
        },
        "role_rows": role_rows,
        "region_rows": region_rows,
        "admin_actions": _admin_actions(db, role_rows, region_rows),
    }


def build_region_landing(db: Session, region: Region) -> dict[str, Any]:
    today = date.today()
    summary = _region_summary(db, region, today=today)
    pursuits = db.scalars(
        select(CompanyPursuit)
        .join(Company)
        .where(CompanyPursuit.region_id == region.id, CompanyPursuit.status != PursuitStatus.CLOSED.value)
        .order_by(CompanyPursuit.priority.desc(), Company.name.asc())
        .limit(12)
    ).all()
    needs_action = [
        pursuit
        for pursuit in pursuits
        if not pursuit.assigned_staff_email or not pursuit.next_action or pursuit.status in {PursuitStatus.PROMOTED.value, PursuitStatus.ANALYSIS.value}
    ][:8]
    pursuit_ids = [pursuit.id for pursuit in pursuits]
    current_jobs = []
    if pursuit_ids:
        current_jobs = db.scalars(
            select(PursuitJobPostingEvidence)
            .where(PursuitJobPostingEvidence.pursuit_id.in_(pursuit_ids), PursuitJobPostingEvidence.included.is_(True))
            .order_by(PursuitJobPostingEvidence.confidence_score.desc(), PursuitJobPostingEvidence.updated_at.desc())
            .limit(10)
        ).all()
    staff = db.scalars(
        select(User)
        .join(StaffRegionAssignment, StaffRegionAssignment.user_id == User.id)
        .where(StaffRegionAssignment.region_id == region.id, StaffRegionAssignment.active.is_(True), User.active.is_(True))
        .order_by(User.name, User.email)
    ).all()
    return {
        "region": region,
        "summary": summary,
        "staff": staff,
        "pursuits": pursuits,
        "needs_action": needs_action,
        "current_jobs": current_jobs,
        "links": {
            "workbench": f"/reports/operating-workbench?region_id={region.id}",
            "targeting": f"/reports/company-targeting?region_id={region.id}",
            "matches": f"/reports/candidate-company-matches?region_id={region.id}",
            "pursuits": f"/pursuits?region_id={region.id}",
        },
    }


def build_role_landing(db: Session, role: MarketingRole) -> dict[str, Any]:
    summary = _role_summary(db, role)
    consultants = db.scalars(
        select(ConsultantProfile)
        .where(ConsultantProfile.active.is_(True), ConsultantProfile.marketing_role_id == role.id)
        .order_by(ConsultantProfile.updated_at.desc())
        .limit(12)
    ).all()
    blockers = _role_blockers(consultants)
    jobs = _role_job_evidence(db, role, limit=12)
    top_tech = _top_tech_from_jobs(jobs)
    team = db.scalars(
        select(User)
        .join(StaffMarketingRoleAssignment, StaffMarketingRoleAssignment.user_id == User.id)
        .where(StaffMarketingRoleAssignment.marketing_role_id == role.id, StaffMarketingRoleAssignment.active.is_(True), User.active.is_(True))
        .order_by(User.name, User.email)
    ).all()
    programs = db.scalars(
        select(TrainingProgram)
        .where(TrainingProgram.active.is_(True), TrainingProgram.marketing_role_id == role.id)
        .order_by(TrainingProgram.display_order, TrainingProgram.industry_domain)
        .limit(6)
    ).all()
    return {
        "role": role,
        "summary": summary,
        "team": team,
        "consultants": consultants,
        "blockers": blockers,
        "jobs": jobs,
        "top_tech": top_tech,
        "programs": programs,
        "links": {
            "journeys": f"/reports/role-journeys?marketing_role_id={role.id}",
            "matches": f"/reports/candidate-company-matches?marketing_role_id={role.id}",
            "consultants": f"/consultants?marketing_role_id={role.id}",
            "mocks": f"/mock-interviews?marketing_role_id={role.id}",
        },
    }


def build_consultant_landing(db: Session, user: User) -> dict[str, Any]:
    consultant = _consultant_for_user(db, user)
    if consultant:
        submissions = db.scalars(
            select(ConsultantSubmission)
            .where(ConsultantSubmission.consultant_id == consultant.id)
            .order_by(ConsultantSubmission.updated_at.desc())
            .limit(8)
        ).all()
        campaigns = db.scalars(
            select(TargetingCampaign)
            .where(TargetingCampaign.consultant_id == consultant.id)
            .order_by(TargetingCampaign.updated_at.desc())
            .limit(8)
        ).all()
        mocks = db.scalars(
            select(MockInterview)
            .where(MockInterview.consultant_id == consultant.id)
            .order_by(MockInterview.scheduled_on.desc().nulls_last(), MockInterview.updated_at.desc())
            .limit(8)
        ).all()
        return {
            "mode": "consultant",
            "consultant": consultant,
            "gate": marketing_ready_context(consultant),
            "submissions": submissions,
            "campaigns": campaigns,
            "mocks": mocks,
            "links": {
                "profile": f"/consultants/{consultant.id}",
                "journey": f"/consultants/{consultant.id}/journey",
                "positioning": f"/consultants/{consultant.id}/positioning",
                "matches": f"/reports/candidate-company-matches?consultant_id={consultant.id}",
            },
        }
    owner = (user.email or user.name or "").strip()
    owned = []
    if owner:
        owned = db.scalars(
            select(ConsultantProfile)
            .where(ConsultantProfile.active.is_(True), or_(func.lower(ConsultantProfile.staff_owner) == owner.lower(), ConsultantProfile.staff_owner.ilike(f"%{owner}%")))
            .order_by(ConsultantProfile.updated_at.desc())
            .limit(12)
        ).all()
    return {
        "mode": "staff",
        "consultant": None,
        "owned_consultants": owned,
        "summary": {
            "owned": len(owned),
            "marketing_ready": sum(1 for row in owned if row.marketing_status in MARKETING_READY_STATUSES),
            "needs_mock": sum(1 for row in owned if not row.mock_interview_passed),
            "needs_evidence": sum(1 for row in owned if not row.marketing_brief_ready),
        },
    }


def _consultant_for_user(db: Session, user: User) -> ConsultantProfile | None:
    email = (user.email or "").strip().lower()
    if not email:
        return None
    return db.scalar(select(ConsultantProfile).where(func.lower(ConsultantProfile.email) == email, ConsultantProfile.active.is_(True)))


def _region_summary(db: Session, region: Region, *, today: date) -> dict[str, Any]:
    base = select(CompanyPursuit).where(CompanyPursuit.region_id == region.id, CompanyPursuit.status != PursuitStatus.CLOSED.value)
    pursuit_ids = [row[0] for row in db.execute(select(CompanyPursuit.id).where(CompanyPursuit.region_id == region.id)).all()]
    return {
        "active_companies": db.scalar(select(func.count()).select_from(base.subquery())) or 0,
        "needs_owner": db.scalar(select(func.count(CompanyPursuit.id)).where(CompanyPursuit.region_id == region.id, CompanyPursuit.status != PursuitStatus.CLOSED.value, CompanyPursuit.assigned_staff_email == "")) or 0,
        "needs_research": db.scalar(select(func.count(CompanyPursuit.id)).where(CompanyPursuit.region_id == region.id, CompanyPursuit.status.in_([PursuitStatus.ANALYSIS.value, PursuitStatus.PROMOTED.value]))) or 0,
        "overdue": db.scalar(select(func.count(CompanyPursuit.id)).where(CompanyPursuit.region_id == region.id, CompanyPursuit.status != PursuitStatus.CLOSED.value, CompanyPursuit.next_follow_up_date.is_not(None), CompanyPursuit.next_follow_up_date < today)) or 0,
        "current_jobs": db.scalar(select(func.count(PursuitJobPostingEvidence.id)).where(PursuitJobPostingEvidence.pursuit_id.in_(pursuit_ids or [-1]), PursuitJobPostingEvidence.included.is_(True))) or 0,
        "latest_uscis_year": db.scalar(select(func.max(UscisEmployerYearlyStat.fiscal_year))),
    }


def _role_summary(db: Session, role: MarketingRole) -> dict[str, Any]:
    consultant_count = db.scalar(select(func.count(ConsultantProfile.id)).where(ConsultantProfile.active.is_(True), ConsultantProfile.marketing_role_id == role.id)) or 0
    ready_count = db.scalar(select(func.count(ConsultantProfile.id)).where(ConsultantProfile.active.is_(True), ConsultantProfile.marketing_role_id == role.id, ConsultantProfile.marketing_status.in_(MARKETING_READY_STATUSES))) or 0
    active_submissions = (
        db.scalar(
            select(func.count(ConsultantSubmission.id))
            .join(ConsultantProfile, ConsultantProfile.id == ConsultantSubmission.consultant_id)
            .where(ConsultantProfile.marketing_role_id == role.id, ConsultantSubmission.status.in_(ACTIVE_SUBMISSION_VALUES))
        )
        or 0
    )
    mocks_waiting = db.scalar(select(func.count(MockInterview.id)).where(MockInterview.marketing_role_id == role.id, MockInterview.status.in_(["planned", "waiting_feedback", "needs_work"]))) or 0
    requirement_count = db.scalar(select(func.count(PursuitRequirement.id)).where(PursuitRequirement.marketing_role_id == role.id)) or 0
    current_jobs = len(_role_job_evidence(db, role, limit=500))
    return {
        "consultants": consultant_count,
        "marketing_ready": ready_count,
        "blocked": max(consultant_count - ready_count, 0),
        "active_submissions": active_submissions,
        "mocks_waiting": mocks_waiting,
        "requirements": requirement_count,
        "current_jobs": current_jobs,
    }


def _role_job_evidence(db: Session, role: MarketingRole, *, limit: int) -> list[PursuitJobPostingEvidence]:
    keys = {_role_key(role.name), _role_key(role.code), _role_key((role.code or "").replace("-", "_"))}
    rows = db.scalars(
        select(PursuitJobPostingEvidence)
        .where(PursuitJobPostingEvidence.included.is_(True))
        .order_by(PursuitJobPostingEvidence.confidence_score.desc(), PursuitJobPostingEvidence.updated_at.desc())
        .limit(800)
    ).all()
    matched = [
        row
        for row in rows
        if _role_key(row.primary_marketing_role) in keys or _role_key(row.primary_role_slug) in keys or _role_key(role.name) in _role_key(row.secondary_marketing_roles_json)
    ]
    return matched[:limit]


def _role_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _role_blockers(consultants: list[ConsultantProfile]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    rows = []
    for consultant in consultants:
        gate = marketing_ready_context(consultant)
        if gate["ready"]:
            continue
        for label in gate["missing_labels"]:
            counter[label] += 1
        rows.append({"consultant": consultant, "missing": gate["missing_labels"][:4]})
    return [{"label": label, "count": count} for label, count in counter.most_common()] + [{"label": "Blocked consultants", "count": len(rows)}]


def _top_tech_from_jobs(jobs: list[PursuitJobPostingEvidence]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for job in jobs:
        for value in re.findall(r'"([^"]+)"', job.technology_signals_json or ""):
            normalized = value.strip()
            if normalized:
                counter[normalized] += 1
    return [{"name": name, "count": count} for name, count in counter.most_common(16)]


def _admin_actions(db: Session, role_rows: list[dict[str, Any]], region_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if any(row["summary"]["needs_owner"] for row in region_rows):
        actions.append({"label": "Assign company owners", "detail": "Some active region companies do not have a staff owner.", "url": "/pursuits?queue=needs_owner"})
    if any(row["summary"]["blocked"] for row in role_rows):
        actions.append({"label": "Clear consultant readiness blockers", "detail": "Role teams have consultants blocked before Marketing Ready.", "url": "/reports/role-journeys"})
    if db.scalar(select(func.count(CompanyPursuit.id)).where(CompanyPursuit.status.in_([PursuitStatus.ANALYSIS.value, PursuitStatus.PROMOTED.value]))) or 0:
        actions.append({"label": "Review promoted companies", "detail": "Companies need current posting research and a pursue/watch/reject decision.", "url": "/pursuits?queue=needs_research"})
    return actions[:6]
