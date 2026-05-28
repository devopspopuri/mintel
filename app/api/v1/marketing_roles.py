from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.pursuit_intelligence import MarketingRole


router = APIRouter()


@router.get("")
def list_marketing_roles(db: Session = Depends(get_db)) -> dict:
    roles = db.scalars(select(MarketingRole).where(MarketingRole.active.is_(True)).order_by(MarketingRole.id)).all()
    return {
        "items": [
            {
                "id": role.id,
                "code": role.code,
                "name": role.name,
                "description": role.description,
                "covers": role.covers,
                "common_tools": role.common_tools,
                "aliases": role.aliases,
                "owner_id": role.owner_id,
            }
            for role in roles
        ]
    }
