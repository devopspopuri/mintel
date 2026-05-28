from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.company import CompanyPursuit
from app.models.pursuit_intelligence import PursuitC2CManager, PursuitContact, PursuitEvidence, PursuitNote, PursuitPrimeVendor, PursuitRequirement, PursuitTechnology
from app.services.company_research import build_company_research_prompt
from app.services.pursuit_intelligence import ingest_research_json, structured_context
from app.services.rbac import Permission
from app.web.auth import PermissionDenied, require_permission, require_user
from app.web.router import _can_edit_pursuit_workspace, _can_view_pursuit, _company_uscis_context, _pursuit_visibility_clause


router = APIRouter()


class ResearchImportPayload(BaseModel):
    research_json: str


@router.get("")
def list_pursuits(limit: int = 50, offset: int = 0, user=Depends(require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    query = select(CompanyPursuit)
    visible_clause = _pursuit_visibility_clause(user)
    if visible_clause is not None:
        query = query.where(visible_clause)
    total = len(db.scalars(query.with_only_columns(CompanyPursuit.id)).all())
    rows = db.scalars(query.order_by(CompanyPursuit.updated_at.desc()).limit(limit).offset(offset)).all()
    return {
        "total": total,
        "items": [_pursuit_payload(item) for item in rows],
    }


@router.get("/{pursuit_id}")
def get_pursuit(pursuit_id: int, user=Depends(require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    pursuit = _get_pursuit(db, pursuit_id)
    if not _can_view_pursuit(user, pursuit):
        raise PermissionDenied("This company belongs to another region group.")
    return {**_pursuit_payload(pursuit), "intelligence": _structured_payload(db, pursuit_id)}


@router.get("/{pursuit_id}/prompt")
def get_research_prompt(pursuit_id: int, user=Depends(require_user), db: Session = Depends(get_db)) -> dict[str, str]:
    pursuit = _get_pursuit(db, pursuit_id)
    if not _can_view_pursuit(user, pursuit):
        raise PermissionDenied("This company belongs to another region group.")
    context = _company_uscis_context(db, pursuit.company)
    return {"prompt": pursuit.research_prompt or build_company_research_prompt(pursuit.company, context)}


@router.post("/{pursuit_id}/research/import-json")
def import_research_json(pursuit_id: int, payload: ResearchImportPayload, user=Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)), db: Session = Depends(get_db)) -> dict[str, Any]:
    pursuit = _get_pursuit(db, pursuit_id)
    if not _can_edit_pursuit_workspace(user, pursuit):
        raise PermissionDenied("This company belongs to another region group.")
    counts = ingest_research_json(db, pursuit, payload.research_json, actor=user.email)
    db.commit()
    return {"status": "imported", "counts": counts}


def _get_pursuit(db: Session, pursuit_id: int) -> CompanyPursuit:
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if not pursuit:
        raise HTTPException(status_code=404, detail="Pursuit not found")
    return pursuit


def _pursuit_payload(pursuit: CompanyPursuit) -> dict[str, Any]:
    return {
        "id": pursuit.id,
        "company_id": pursuit.company_id,
        "company_name": pursuit.company.name,
        "status": pursuit.status,
        "priority": pursuit.priority,
        "decision": pursuit.decision,
        "closing_probability": pursuit.closing_probability,
        "assigned_staff_name": pursuit.assigned_staff_name,
        "assigned_staff_email": pursuit.assigned_staff_email,
        "next_follow_up_date": pursuit.next_follow_up_date.isoformat() if pursuit.next_follow_up_date else None,
        "next_action": pursuit.next_action,
    }


def _structured_payload(db: Session, pursuit_id: int) -> dict[str, Any]:
    context = structured_context(db, pursuit_id)
    return {
        "requirements": [{**_row(item, ["id", "title", "location", "posted_or_seen_date", "employment_type", "technologies", "work_auth_language", "source_url", "confidence"]), "marketing_role": item.marketing_role.name if item.marketing_role else None} for item in context["requirements"]],
        "technologies": [_row(item, ["id", "category", "name", "evidence", "confidence"]) for item in context["technologies"]],
        "contacts": [_row(item, ["id", "name", "title", "department", "location", "email", "phone", "linkedin_url", "source_url", "confidence"]) for item in context["contacts"]],
        "prime_vendors": [_row(item, ["id", "vendor_name", "relationship_evidence", "technology_or_role_focus", "source_url", "confidence"]) for item in context["vendors"]],
        "c2c_managers": [_row(item, ["id", "name", "company_or_vendor", "title", "role_focus", "linkedin_url", "source_url", "confidence"]) for item in context["managers"]],
        "evidence": [_row(item, ["id", "kind", "label", "url", "notes", "confidence"]) for item in context["evidence"]],
        "notes": [_row(item, ["id", "category", "body", "author", "pinned", "created_at"]) for item in context["notes"]],
    }


def _row(item: PursuitRequirement | PursuitTechnology | PursuitContact | PursuitPrimeVendor | PursuitC2CManager | PursuitEvidence | PursuitNote, fields: list[str]) -> dict[str, Any]:
    return {field: getattr(item, field) for field in fields}
