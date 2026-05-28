from fastapi import APIRouter, Depends

from app.api.v1 import companies, consultants, imports, marketing_roles, pursuits, training_programs
from app.web.auth import require_user


api_router = APIRouter(dependencies=[Depends(require_user)])
api_router.include_router(companies.router, prefix="/companies", tags=["companies"])
api_router.include_router(consultants.router, prefix="/consultants", tags=["consultants"])
api_router.include_router(imports.router, prefix="/imports", tags=["imports"])
api_router.include_router(marketing_roles.router, prefix="/marketing-roles", tags=["marketing-roles"])
api_router.include_router(pursuits.router, prefix="/pursuits", tags=["pursuits"])
api_router.include_router(training_programs.router, prefix="/training-programs", tags=["training-programs"])
