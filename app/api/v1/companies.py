from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.company import Company, CompanyAlias, CompanyPursuit
from app.schemas.company import CompanyList, CompanyRead
from app.web.auth import PermissionDenied, require_user
from app.web.router import _can_view_pursuit, _pursuit_visibility_clause


router = APIRouter()


@router.get("", response_model=CompanyList)
def list_companies(
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user=Depends(require_user),
    db: Session = Depends(get_db),
) -> CompanyList:
    filters = []
    visible_clause = _pursuit_visibility_clause(user)
    if q:
        pattern = f"%{q}%"
        filters.append(or_(Company.name.ilike(pattern), CompanyAlias.raw_name.ilike(pattern)))

    if filters:
        query = select(Company).outerjoin(CompanyAlias).outerjoin(CompanyPursuit).where(*filters).distinct()
        count_query = select(func.count(func.distinct(Company.id))).outerjoin(CompanyAlias).outerjoin(CompanyPursuit).where(*filters)
    else:
        query = select(Company).outerjoin(CompanyPursuit)
        count_query = select(func.count(func.distinct(Company.id))).outerjoin(CompanyPursuit)
    if visible_clause is not None:
        query = query.where(visible_clause)
        count_query = count_query.where(visible_clause)

    total = db.scalar(count_query) or 0
    rows = db.scalars(
        query.order_by(Company.opt_friendliness_score.desc(), Company.h1b_approval_count.desc(), Company.name).limit(limit).offset(offset)
    ).all()
    return CompanyList(items=rows, total=total)


@router.get("/{company_id}", response_model=CompanyRead)
def get_company(company_id: int, user=Depends(require_user), db: Session = Depends(get_db)) -> Company:
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if company.pursuit and not _can_view_pursuit(user, company.pursuit):
        raise PermissionDenied("This company belongs to another region group.")
    if not company.pursuit and _pursuit_visibility_clause(user) is not None:
        raise PermissionDenied("Company is not promoted for your region group.")
    return company
