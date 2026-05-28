from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.api.v1.training_programs import router as training_programs_api_router
from app.core.config import settings
from app.db import base as _models  # noqa: F401
from app.db.session import SessionLocal
from app.services.auth import ensure_bootstrap_admin
from app.services.marketing_roles import ensure_default_marketing_roles
from app.services.regions import ensure_default_regions
from app.services.training_programs import ensure_training_programs
from app.web.auth import LoginRedirect, PermissionDenied, login_redirect_response
from app.web.auth import require_user
from app.web.router import router as web_router
from app.web.templates import templates


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Company, jobs, and interviews intelligence service for sponsorship and OPT-friendly signals.",
        root_path="",
    )
    if settings.allowed_host_list:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site=settings.session_cookie_same_site,
        https_only=settings.session_cookie_secure,
    )
    app.mount("/static", StaticFiles(directory="static"), name="static")
    app.include_router(web_router)
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(training_programs_api_router, prefix="/api/training-programs", tags=["training-programs"], dependencies=[Depends(require_user)])

    @app.on_event("startup")
    def seed_bootstrap_data() -> None:
        with SessionLocal() as db:
            ensure_bootstrap_admin(db)
            ensure_default_regions(db)
            ensure_default_marketing_roles(db)
            ensure_training_programs(db)

    @app.exception_handler(LoginRedirect)
    def handle_login_redirect(request, exc: LoginRedirect):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return login_redirect_response(exc.next_url)

    @app.exception_handler(PermissionDenied)
    def handle_permission_denied(request, exc: PermissionDenied):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.message}, status_code=403)
        return templates.TemplateResponse("web/access_denied.html", {"request": request, "message": exc.message}, status_code=403)

    @app.exception_handler(RequestValidationError)
    def handle_request_validation_error(request, exc: RequestValidationError):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.errors()}, status_code=422)
        locations = {tuple(error.get("loc", ()))[:1] for error in exc.errors()}
        if request.method == "GET" and locations == {("query",)}:
            return RedirectResponse(str(request.url.replace(query="")), status_code=303)
        return HTMLResponse("Invalid request. Please go back and check the form values.", status_code=400)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name}

    return app


app = create_app()
