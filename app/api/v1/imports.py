from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.importers.h1b_csv import import_h1b_rows
from app.services.rbac import Permission
from app.web.auth import require_permission


router = APIRouter()


@router.post("/h1b")
def import_h1b_disclosures(
    fiscal_year: int = Form(...),
    file: UploadFile = File(...),
    user=Depends(require_permission(Permission.IMPORT_USCIS)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = import_h1b_rows(db, file.file, fiscal_year=fiscal_year, source_file=file.filename or "upload.csv")
    return {"status": "ok", **result}
