from __future__ import annotations

import csv
import json
from datetime import date, datetime, time as datetime_time, timezone
from html import escape
from html.parser import HTMLParser
from io import StringIO
from math import ceil
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any, Optional
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import delete, distinct, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.importers.uscis_employer import import_uscis_employer_rows, refresh_companies_from_uscis
from app.models.company import Company, CompanyAlias, CompanyMergeAudit, CompanyPursuit, PursuitStatus, Region
from app.models.consultant import ConsultantProfile
from app.models.h1b import H1BDisclosure
from app.models.interview import InterviewExperience
from app.models.job import JobOpportunity, JobSource
from app.models.operations import (
    ConsultantAvailabilityBlock,
    ConsultantJourneyActivity,
    ConsultantJourneyActivityStatus,
    ConsultantJourneyStatus,
    ConsultantRoleJourney,
    ConsultantSubmission,
    MockInterview,
    MockInterviewStatus,
    MockInterviewStatusEvent,
    ResumeVersion,
    SubmissionStatus,
    TargetingCampaign,
    TargetingCampaignStatus,
    TargetingCampaignTarget,
    TargetingCampaignTargetStatus,
    TrainerAdhocAvailability,
    TrainerWeeklyAvailability,
)
from app.models.pursuit_intelligence import MarketingRole, PursuitC2CManager, PursuitContact, PursuitEvidence, PursuitIntelligenceSnapshot, PursuitJobPostingEvidence, PursuitNote, PursuitPrimeVendor, PursuitRequirement, PursuitTechnology, ResearchJobStatus
from app.models.uscis import UscisDecisionType, UscisEmployerYearlyStat, UscisImportJob
from app.models.training import TrainingJobDescription, TrainingProgram
from app.models.user import RegionGroup, RegionGroupMember, RegionGroupRegion, StaffMarketingRoleAssignment, StaffRegionAssignment, User, UserRole
from app.services.auth import authenticate_user, hash_password, verify_password
from app.services.company_research import build_company_research_prompt
from app.services.consultant_access import consultant_access_gate_plan, consultant_training_scope_matches
from app.services.lifecycle import build_lifecycle_backbone
from app.services.landing_pages import build_admin_landing, build_consultant_landing, build_landing_index, build_region_landing, build_role_landing
from app.services.marketing_glossary import MARKETING_ROLE_GLOSSARY, ROLE_TERMS, glossary_categories, glossary_item, glossary_roles, star_word_count
from app.services.marketing_roles import classify_marketing_role
from app.services.openai_research import create_research_job, run_research_job
from app.services.operating_rules import MARKETING_READY_STATUSES, company_pursue_context, marketing_ready_context, submission_eligibility_context, user_can_mark_marketing_ready
from app.services.product_systems import product_system_cards, product_system_detail, product_system_link_map, product_system_slug_lookup
from app.services.pursuit_intelligence import activity, consolidated_tech_stack_context, decision_readiness_context, ingest_research_json, job_posting_review_context, structured_context
from app.services.rbac import Permission, ROLE_PERMISSIONS, has_permission
from app.services.regions import all_region_metadata, recommended_region_for_company, region_code_for_state, region_signal_for_company, region_signals_for_companies, states_for_region
from app.services.training_programs import INDUSTRY_DOMAINS, MARKETING_ROLE_NAMES
from app.web.auth import PermissionDenied, require_admin, require_manager, require_permission, require_user
from app.web.templates import templates


router = APIRouter()
_USCIS_ANALYSIS_CACHE_TTL_SECONDS = 120
_USCIS_ANALYSIS_CACHE_LOCK = threading.Lock()
_USCIS_ANALYSIS_CACHE: dict[tuple[tuple[str, str], ...], tuple[float, dict[str, Any]]] = {}
_RICH_TEXT_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li", "a"}
_RICH_TEXT_BLOCK_TAGS = {"p", "ul", "ol", "li", "br"}


def _flash(request: Request, message: str, kind: str = "success") -> None:
    flashes = request.session.setdefault("_flash", [])
    flashes.append({"message": message, "kind": kind})
    request.session["_flash"] = flashes


def _safe_next_url(next_url: str) -> str:
    parsed = urlparse(next_url or "")
    if parsed.scheme or parsed.netloc or not (next_url or "").startswith("/"):
        return "/dashboard"
    return next_url or "/dashboard"


def _default_landing_for_user(user: User) -> str:
    role = getattr(user, "role", "")
    return "/training-basics" if getattr(role, "value", role) == UserRole.CONSULTANT.value else "/dashboard"


def _optional_query_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _bounded_query_int(value: Any, default: int, *, minimum: int, maximum: Optional[int] = None) -> int:
    parsed = _optional_query_int(value)
    if parsed is None:
        return default
    parsed = max(minimum, parsed)
    return min(parsed, maximum) if maximum is not None else parsed


class _RichTextSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.anchor_stack: list[bool] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag not in _RICH_TEXT_TAGS:
            return
        if tag == "a":
            href = ""
            for key, value in attrs:
                if key.lower() == "href":
                    href = (value or "").strip()
                    break
            if href and urlparse(href).scheme in {"http", "https", "mailto"}:
                self.parts.append(f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">')
                self.anchor_stack.append(True)
            else:
                self.anchor_stack.append(False)
            return
        if tag == "br":
            self.parts.append("<br>")
        else:
            self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in _RICH_TEXT_TAGS and tag not in {"br", "a"}:
            self.parts.append(f"</{tag}>")
        elif tag == "a" and self.anchor_stack:
            opened = self.anchor_stack.pop()
            if opened:
                self.parts.append("</a>")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        self.parts.append(escape(data))

    def get_html(self) -> str:
        return "".join(self.parts).strip()


class _RichTextPlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() in _RICH_TEXT_BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _RICH_TEXT_BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def get_text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.parts)).strip()


def _sanitize_rich_text(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if not re.search(r"</?[a-zA-Z][^>]*>", raw):
        paragraphs = [line.strip() for line in re.split(r"\n{2,}", raw) if line.strip()]
        return "".join(f"<p>{escape(line).replace(chr(10), '<br>')}</p>" for line in paragraphs)
    sanitizer = _RichTextSanitizer()
    sanitizer.feed(raw)
    sanitizer.close()
    return sanitizer.get_html()


def _rich_text_plain_text(value: str) -> str:
    raw = value or ""
    if not re.search(r"</?[a-zA-Z][^>]*>", raw):
        return re.sub(r"\s+", " ", raw).strip()
    parser = _RichTextPlainText()
    parser.feed(raw)
    parser.close()
    return parser.get_text()


def _bounded_form_score(value: Any) -> int:
    return _bounded_query_int(value, 0, minimum=0, maximum=100)


@router.get("/", response_class=HTMLResponse)
def root(request: Request, user: User = Depends(require_user)):
    return RedirectResponse(_default_landing_for_user(user), status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/dashboard", db: Session = Depends(get_db)):
    next_url = _safe_next_url(next)
    if request.session.get("user_id"):
        return RedirectResponse(next_url, status_code=303)
    return templates.TemplateResponse("auth/login.html", {"request": request, "next": next_url, "error": ""})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard"),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, email, password)
    if not user:
        return templates.TemplateResponse("auth/login.html", {"request": request, "next": _safe_next_url(next), "error": "Invalid email or password."}, status_code=401)
    request.session.clear()
    request.session["user_id"] = user.id
    next_url = _safe_next_url(next)
    if _is_consultant_user(user) and next_url == "/dashboard":
        next_url = _default_landing_for_user(user)
    return RedirectResponse(next_url, status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, error: str = "", user: User = Depends(require_user)):
    return templates.TemplateResponse("web/account.html", {"request": request, "user": user, "error": error})


@router.post("/account/password")
def change_own_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not verify_password(current_password, user.password_hash):
        return RedirectResponse("/account?error=Current+password+is+incorrect", status_code=303)
    if len(new_password) < 10:
        return RedirectResponse("/account?error=New+password+must+be+at+least+10+characters", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse("/account?error=New+passwords+do+not+match", status_code=303)
    user.password_hash = hash_password(new_password)
    db.add(user)
    db.commit()
    _flash(request, "Password updated.")
    return RedirectResponse("/account", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if _is_consultant_user(user):
        return RedirectResponse("/training-basics", status_code=303)
    today = date.today()
    owner_needed_query = _owner_needed_pursuits_query(db)
    visible_clause = _pursuit_visibility_clause(user)
    my_queue_query = select(CompanyPursuit).join(Company).where(CompanyPursuit.status != PursuitStatus.CLOSED.value)
    if visible_clause is not None:
        my_queue_query = my_queue_query.where(visible_clause)
    if not has_permission(user, Permission.ASSIGN_PURSUITS):
        my_queue_query = my_queue_query.where(func.lower(CompanyPursuit.assigned_staff_email) == (user.email or "").lower())
    totals = {
        "companies": db.scalar(select(func.count(Company.id))) or 0,
        "uscis_rows": db.scalar(select(func.count(UscisEmployerYearlyStat.id))) or 0,
        "promoted": db.scalar(select(func.count(CompanyPursuit.id))) or 0,
        "regions": db.scalar(select(func.count(Region.id)).where(Region.active.is_(True))) or 0,
        "unassigned": db.scalar(select(func.count(CompanyPursuit.id)).where(CompanyPursuit.assigned_staff_email == "")) or 0,
        "needs_owner": db.scalar(select(func.count()).select_from(owner_needed_query.subquery())) or 0,
        "overdue": db.scalar(select(func.count()).select_from(_visible_followups_query(user, today, db).subquery())) or 0,
        "my_queue": db.scalar(select(func.count()).select_from(my_queue_query.subquery())) or 0,
    }
    consultant_workbench = _consultant_onboarding_workbench_context(db, user, limit=8)
    db.commit()
    top_companies = db.scalars(select(Company).order_by(Company.h1b_approval_count.desc(), Company.opt_friendliness_score.desc()).limit(10)).all()
    followups = db.scalars(_visible_followups_query(user, None, db).order_by(CompanyPursuit.next_follow_up_date.asc()).limit(8)).all()
    owner_needed = db.scalars(owner_needed_query.order_by(Company.name.asc()).limit(8)).all()
    my_queue = db.scalars(my_queue_query.order_by(CompanyPursuit.next_follow_up_date.asc().nulls_last(), CompanyPursuit.priority.desc(), Company.name.asc()).limit(8)).all()
    return templates.TemplateResponse("web/dashboard.html", {"request": request, "user": user, "totals": totals, "top_companies": top_companies, "followups": followups, "owner_needed": owner_needed, "my_queue": my_queue, "consultant_workbench": consultant_workbench})


@router.get("/landings", response_class=HTMLResponse)
def landing_index(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return templates.TemplateResponse("web/landing_index.html", {"request": request, "user": user, **build_landing_index(db, user)})


@router.get("/landings/admin", response_class=HTMLResponse)
def admin_landing(request: Request, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    return templates.TemplateResponse("web/landing_admin.html", {"request": request, "user": user, **build_admin_landing(db)})


@router.get("/landings/consultant", response_class=HTMLResponse)
def consultant_landing(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return templates.TemplateResponse("web/landing_consultant.html", {"request": request, "user": user, **build_consultant_landing(db, user)})


@router.get("/landings/regions/{region_id}", response_class=HTMLResponse)
def region_landing(region_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    region = db.get(Region, region_id)
    if not region:
        return RedirectResponse("/landings", status_code=303)
    return templates.TemplateResponse("web/landing_region.html", {"request": request, "user": user, **build_region_landing(db, region)})


@router.get("/landings/roles/{role_id}", response_class=HTMLResponse)
def role_landing(role_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    role = db.get(MarketingRole, role_id)
    if not role:
        return RedirectResponse("/landings", status_code=303)
    return templates.TemplateResponse("web/landing_role.html", {"request": request, "user": user, **build_role_landing(db, role)})


@router.get("/uscis/import", response_class=HTMLResponse)
def uscis_import_form(request: Request, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    return templates.TemplateResponse("web/uscis_import.html", {"request": request, "user": user, "result": None, "import_sources": _import_sources(db), "import_jobs": _recent_import_jobs(db)})


@router.post("/uscis/import", response_class=HTMLResponse)
def uscis_import_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    default_year: Optional[int] = Form(None),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    upload_dir = _import_upload_dir()
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", file.filename or "uscis-upload.csv").strip("-") or "uscis-upload.csv"
    stored_path = upload_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{safe_name}"
    with stored_path.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    job = UscisImportJob(source_file=file.filename or safe_name, stored_path=str(stored_path), requested_by=user.email, status="queued")
    db.add(job)
    db.commit()
    background_tasks.add_task(_run_uscis_import_job, job.id, default_year)
    _flash(request, f"Queued USCIS import job #{job.id} for {job.source_file}.")
    return RedirectResponse(f"/uscis/import/jobs/{job.id}", status_code=303)


@router.get("/uscis/import/jobs/{job_id}", response_class=HTMLResponse)
def uscis_import_job_detail(job_id: int, request: Request, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    job = db.get(UscisImportJob, job_id)
    if not job:
        return RedirectResponse("/uscis/import", status_code=303)
    return templates.TemplateResponse("web/uscis_import_job.html", {"request": request, "user": user, "job": job})


@router.get("/uscis/import/jobs/{job_id}/status", response_class=HTMLResponse)
def uscis_import_job_status(job_id: int, request: Request, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    job = db.get(UscisImportJob, job_id)
    if not job:
        return HTMLResponse("<div class=\"flash error\">Import job not found.</div>", status_code=404)
    return templates.TemplateResponse("web/partials/uscis_import_job_status.html", {"request": request, "user": user, "job": job})


@router.get("/uscis/analysis", response_class=HTMLResponse)
def uscis_analysis(
    request: Request,
    q: str = "",
    state: str = "",
    region: str = "",
    naics: str = "",
    decision_type: str = UscisDecisionType.ALL.value,
    profile: str = "h1b",
    target_size: str = "all",
    min_approvals: str = "10",
    min_approval_rate: str = "80",
    sort: str = "fit",
    format: str = "",
    start_year: str = "",
    end_year: str = "",
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    selected_min_approvals = _bounded_query_int(min_approvals, 10, minimum=0)
    selected_min_approval_rate = _bounded_query_int(min_approval_rate, 80, minimum=0, maximum=100)
    selected_start_year = _optional_query_int(start_year)
    selected_end_year = _optional_query_int(end_year)
    context = _uscis_analysis_context(
        db,
        q=q,
        state=state,
        region=region,
        naics=naics,
        decision_type=decision_type,
        profile=profile,
        target_size=target_size,
        min_approvals=selected_min_approvals,
        min_approval_rate=selected_min_approval_rate,
        sort=sort,
        start_year=selected_start_year,
        end_year=selected_end_year,
        page=page,
        per_page=per_page,
        include_export_rows=format == "csv",
    )
    if format == "csv":
        return _csv_response(
            "uscis-analysis.csv",
            context["export_rows"],
            [
                "company_id",
                "employer_name",
                "years",
                "states",
                "cities",
                "latest_year",
                "decisions",
                "approvals",
                "denials",
                "approval_rate",
                "new_employment_approvals",
                "change_employer_approvals",
                "continuation_approvals",
                "fit_label",
                "fit_score",
                "sponsor_label",
                "sponsor_score",
                "target_size_label",
            ],
        )
    context.update({"request": request, "user": user})
    return templates.TemplateResponse("web/uscis_analysis.html", context)


@router.get("/reports", response_class=HTMLResponse)
def reports_home(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    available_years = _available_uscis_years(db)
    default_start, default_end = _default_year_range(available_years)
    return templates.TemplateResponse(
        "web/reports_home.html",
        {
            "request": request,
            "user": user,
            "available_years": available_years,
            "default_start": default_start,
            "default_end": default_end,
            "decision_options": _decision_options(),
            "regions": db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all(),
            "marketing_roles": _active_marketing_roles(db),
        },
    )


@router.get("/reports/location-expansion", response_class=HTMLResponse)
def location_expansion_report(
    request: Request,
    fiscal_year: str = "",
    plan_size: int = Query(10, ge=5, le=25),
    slots_per_region: int = Query(0, ge=0, le=5),
    max_cost_tier: str = "medium",
    min_new_employment: int = Query(25, ge=0, le=100000),
    include_existing_houston: bool = Query(True),
    format: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    available_years = _available_uscis_years(db)
    selected_year = _optional_query_int(fiscal_year) or (max(available_years) if available_years else None)
    report = _location_expansion_report(
        db,
        fiscal_year=selected_year,
        plan_size=plan_size,
        slots_per_region=slots_per_region,
        max_cost_tier=max_cost_tier,
        min_new_employment=min_new_employment,
        include_existing_houston=include_existing_houston,
    )
    if format == "csv":
        return _csv_response(
            "location-expansion.csv",
            report["rows"],
            [
                "rank",
                "hub",
                "region_name",
                "cost_tier",
                "recommendation",
                "new_employment",
                "approvals",
                "decisions",
                "approval_rate",
                "employer_count",
                "opportunity_score",
                "affordability_score",
                "expansion_score",
                "coverage_notes",
            ],
        )
    params = {
        "fiscal_year": selected_year or "",
        "plan_size": plan_size,
        "slots_per_region": slots_per_region,
        "max_cost_tier": max_cost_tier,
        "min_new_employment": min_new_employment,
        "include_existing_houston": str(include_existing_houston).lower(),
    }
    return templates.TemplateResponse(
        "web/report_location_expansion.html",
        {
            "request": request,
            "user": user,
            "available_years": available_years,
            "fiscal_year": selected_year,
            "plan_size": plan_size,
            "slots_per_region": slots_per_region,
            "max_cost_tier": max_cost_tier,
            "min_new_employment": min_new_employment,
            "include_existing_houston": include_existing_houston,
            "cost_tier_options": _location_cost_tier_options(),
            "rows": report["rows"],
            "plan_rows": report["plan_rows"],
            "region_rows": report["region_rows"],
            "summary": report["summary"],
            "export_url": f"/reports/location-expansion?{urlencode({**params, 'format': 'csv'})}",
        },
    )


@router.get("/reports/operating-workbench", response_class=HTMLResponse)
def operating_workbench_report(
    request: Request,
    fiscal_year: str = "",
    region_id: str = "",
    staff_email: str = "",
    capacity_per_staff: int = Query(100, ge=10, le=500),
    min_approvals: int = Query(5, ge=0, le=100000),
    min_approval_rate: int = Query(75, ge=0, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    available_years = _available_uscis_years(db)
    selected_year = _optional_query_int(fiscal_year) or (max(available_years) if available_years else None)
    selected_region_id = _optional_query_int(region_id) or _default_workbench_region_id(user, db)
    selected_staff_email = staff_email.strip() or (user.email if selected_region_id in _staff_region_ids_for_user(db, user) else "")
    report = _operating_workbench_report(
        db,
        fiscal_year=selected_year,
        region_id=selected_region_id,
        staff_email=selected_staff_email,
        capacity_per_staff=capacity_per_staff,
        min_approvals=min_approvals,
        min_approval_rate=min_approval_rate,
    )
    db.commit()
    params = {
        "fiscal_year": selected_year or "",
        "region_id": selected_region_id or "",
        "staff_email": selected_staff_email,
        "capacity_per_staff": capacity_per_staff,
        "min_approvals": min_approvals,
        "min_approval_rate": min_approval_rate,
    }
    return templates.TemplateResponse(
        "web/report_operating_workbench.html",
        {
            "request": request,
            "user": user,
            "available_years": available_years,
            "fiscal_year": selected_year,
            "regions": db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all(),
            "selected_region_id": selected_region_id,
            "staff_email": selected_staff_email,
            "staff_options": report["staff_options"],
            "capacity_per_staff": capacity_per_staff,
            "min_approvals": min_approvals,
            "min_approval_rate": min_approval_rate,
            "summary": report["summary"],
            "perspectives": report["perspectives"],
            "priorities": report["priorities"],
            "role_rows": report["role_rows"],
            "handoff_rows": report["handoff_rows"],
            "company_lanes": report["company_lanes"],
            "company_lane_counts": report["company_lane_counts"],
            "journey_lanes": report["journey_lanes"],
            "journey_lane_counts": report["journey_lane_counts"],
            "submission_rows": report["submission_rows"],
            "mock_rows": report["mock_rows"],
            "lifecycle": report["lifecycle"],
            "staff_rows": report["staff_rows"],
            "queue_url": f"/reports/company-targeting?{urlencode(params)}",
            "journey_url": f"/reports/role-journeys?{urlencode({'owner': report['owner_filter']})}" if report["owner_filter"] else "/reports/role-journeys",
            "matches_url": f"/reports/candidate-company-matches?{urlencode({'fiscal_year': selected_year or '', 'region_id': selected_region_id or ''})}",
        },
    )


@router.get("/reports/location-expansion/{hub_key}", response_class=HTMLResponse)
def location_expansion_hub_drilldown(
    hub_key: str,
    request: Request,
    fiscal_year: str = "",
    status: str = "all",
    min_approvals: int = Query(1, ge=0, le=100000),
    limit: int = Query(100, ge=25, le=500),
    sort: str = "new_employment",
    format: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if hub_key not in _LOCATION_HUBS:
        return RedirectResponse("/reports/location-expansion", status_code=303)
    available_years = _available_uscis_years(db)
    selected_year = _optional_query_int(fiscal_year) or (max(available_years) if available_years else None)
    report = _location_hub_company_report(
        db,
        hub_key=hub_key,
        fiscal_year=selected_year,
        status=status,
        min_approvals=min_approvals,
        limit=limit,
        sort=sort,
    )
    if format == "csv":
        return _csv_response(
            f"location-{hub_key}-companies.csv",
            report["rows"],
            [
                "rank",
                "company_name",
                "hub",
                "region_name",
                "suggested_staff",
                "approvals",
                "denials",
                "decisions",
                "approval_rate",
                "new_employment",
                "change_employer",
                "continuation",
                "cities_label",
                "promoted",
            ],
        )
    params = {"fiscal_year": selected_year or "", "status": status, "min_approvals": min_approvals, "limit": limit, "sort": sort}
    return templates.TemplateResponse(
        "web/report_location_hub.html",
        {
            "request": request,
            "user": user,
            "hub_key": hub_key,
            "hub": report["hub"],
            "rows": report["rows"],
            "summary": report["summary"],
            "available_years": available_years,
            "fiscal_year": selected_year,
            "status": status,
            "status_options": [("all", "All companies"), ("uscis_only", "USCIS only"), ("promoted", "Promoted only")],
            "sort": sort,
            "sort_options": [("new_employment", "New employment"), ("approvals", "Approvals"), ("approval_rate", "Approval rate"), ("company", "Company")],
            "min_approvals": min_approvals,
            "limit": limit,
            "page_params": urlencode(params),
            "export_url": f"/reports/location-expansion/{hub_key}?{urlencode({**params, 'format': 'csv'})}",
        },
    )


@router.get("/reports/company-watchlist", response_class=HTMLResponse)
def company_watchlist_report(
    request: Request,
    fiscal_year: str = "",
    region_id: str = "",
    source: str = "all",
    min_approvals: int = Query(5, ge=0, le=100000),
    min_approval_rate: int = Query(75, ge=0, le=100),
    capacity_per_staff: int = Query(100, ge=10, le=500),
    standard_filter: str = "exclude_very_high",
    sort: str = "watch_score",
    format: str = "",
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    available_years = _available_uscis_years(db)
    selected_year = _optional_query_int(fiscal_year) or (max(available_years) if available_years else None)
    selected_region_id = _optional_query_int(region_id)
    report = _company_watchlist_report(
        db,
        fiscal_year=selected_year,
        region_id=selected_region_id,
        source=source,
        min_approvals=min_approvals,
        min_approval_rate=min_approval_rate,
        standard_filter=standard_filter,
        capacity_per_staff=capacity_per_staff,
        sort=sort,
    )
    if format == "csv":
        return _csv_response(
            "company-watchlist.csv",
            report["rows"],
            [
                "rank",
                "company_name",
                "region_name",
                "assigned_staff",
                "watch_score",
                "recommendation",
                "standard_risk",
                "latest_year",
                "approvals",
                "approval_rate",
                "new_employment",
                "promoted",
                "eligible_jobs",
                "verified_jobs",
                "estimated_jobs",
                "excluded_seniority",
                "reason",
            ],
        )
    pagination = _pagination_context(len(report["rows"]), page, per_page)
    rows = report["rows"][pagination["offset"] : pagination["offset"] + per_page]
    params = {
        "fiscal_year": selected_year or "",
        "region_id": selected_region_id or "",
        "source": source,
        "min_approvals": min_approvals,
        "min_approval_rate": min_approval_rate,
        "capacity_per_staff": capacity_per_staff,
        "standard_filter": standard_filter,
        "sort": sort,
        "per_page": per_page,
    }
    return templates.TemplateResponse(
        "web/report_company_watchlist.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "summary": report["summary"],
            "staff_rows": report["staff_rows"],
            "regions": db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all(),
            "available_years": available_years,
            "fiscal_year": selected_year,
            "selected_region_id": selected_region_id,
            "source": source,
            "source_options": _watchlist_source_options(),
            "standard_filter": standard_filter,
            "standard_filter_options": _watchlist_standard_filter_options(),
            "sort": sort,
            "sort_options": _watchlist_sort_options(),
            "min_approvals": min_approvals,
            "min_approval_rate": min_approval_rate,
            "capacity_per_staff": capacity_per_staff,
            "sort_urls": _sort_urls("/reports/company-watchlist", params, ["watch_score", "approvals", "approval_rate", "new_employment", "standard_risk", "company", "staff"]),
            "page_params": urlencode(params),
            "export_url": f"/reports/company-watchlist?{urlencode({**params, 'format': 'csv'})}",
            **pagination,
        },
    )


@router.get("/reports/company-targeting", response_class=HTMLResponse)
def company_targeting_queue_report(
    request: Request,
    fiscal_year: str = "",
    region_id: str = "",
    staff_email: str = "",
    source: str = "all",
    min_approvals: int = Query(5, ge=0, le=100000),
    min_approval_rate: int = Query(75, ge=0, le=100),
    capacity_per_staff: int = Query(100, ge=10, le=500),
    stage: str = "all",
    sort: str = "watch_score",
    format: str = "",
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    available_years = _available_uscis_years(db)
    selected_year = _optional_query_int(fiscal_year) or (max(available_years) if available_years else None)
    selected_region_id = _optional_query_int(region_id)
    report = _company_targeting_queue_report(
        db,
        fiscal_year=selected_year,
        region_id=selected_region_id,
        staff_email=staff_email,
        source=source,
        min_approvals=min_approvals,
        min_approval_rate=min_approval_rate,
        capacity_per_staff=capacity_per_staff,
        stage=stage,
        sort=sort,
    )
    _attach_candidate_matches(db, report["rows"])
    if format == "csv":
        return _csv_response(
            "company-targeting-queue.csv",
            report["rows"],
            [
                "rank",
                "company_name",
                "region_name",
                "suggested_staff",
                "watch_score",
                "recommendation",
                "action_stage",
                "next_staff_action",
                "best_roles_label",
                "tech_stack_label",
                "candidate_match_count",
                "latest_year",
                "approvals",
                "approval_rate",
                "new_employment",
                "eligible_jobs",
                "verified_jobs",
                "estimated_jobs",
                "standard_risk",
                "promoted",
            ],
        )
    pagination = _pagination_context(len(report["rows"]), page, per_page)
    rows = report["rows"][pagination["offset"] : pagination["offset"] + per_page]
    params = {
        "fiscal_year": selected_year or "",
        "region_id": selected_region_id or "",
        "staff_email": staff_email,
        "source": source,
        "min_approvals": min_approvals,
        "min_approval_rate": min_approval_rate,
        "capacity_per_staff": capacity_per_staff,
        "stage": stage,
        "sort": sort,
        "per_page": per_page,
    }
    return templates.TemplateResponse(
        "web/report_company_targeting.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "summary": report["summary"],
            "staff_rows": report["staff_rows"],
            "regions": db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all(),
            "staff_options": report["staff_options"],
            "available_years": available_years,
            "fiscal_year": selected_year,
            "selected_region_id": selected_region_id,
            "staff_email": staff_email,
            "source": source,
            "source_options": _watchlist_source_options(),
            "stage": stage,
            "stage_options": _targeting_stage_options(),
            "sort": sort,
            "sort_options": _targeting_sort_options(),
            "min_approvals": min_approvals,
            "min_approval_rate": min_approval_rate,
            "capacity_per_staff": capacity_per_staff,
            "sort_urls": _sort_urls("/reports/company-targeting", params, ["watch_score", "approvals", "approval_rate", "new_employment", "standard_risk", "company", "staff", "stage"]),
            "page_params": urlencode(params),
            "export_url": f"/reports/company-targeting?{urlencode({**params, 'format': 'csv'})}",
            **pagination,
        },
    )


@router.get("/reports/candidate-company-matches", response_class=HTMLResponse)
def candidate_company_matches_report(
    request: Request,
    fiscal_year: str = "",
    consultant_id: str = "",
    marketing_role_id: str = "",
    region_id: str = "",
    source: str = "all",
    min_approvals: int = Query(5, ge=0, le=100000),
    min_approval_rate: int = Query(75, ge=0, le=100),
    min_match_score: int = Query(35, ge=0, le=100),
    format: str = "",
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    available_years = _available_uscis_years(db)
    selected_year = _optional_query_int(fiscal_year) or (max(available_years) if available_years else None)
    selected_consultant_id = _optional_query_int(consultant_id)
    selected_marketing_role_id = _optional_query_int(marketing_role_id)
    selected_region_id = _optional_query_int(region_id)
    consultant_profile = _consultant_profile_for_user(db, user) if _is_consultant_user(user) else None
    if _is_consultant_user(user):
        selected_consultant_id = consultant_profile.id if consultant_profile else -1
        selected_marketing_role_id = consultant_profile.marketing_role_id if consultant_profile else None
    report = _candidate_company_matches_report(
        db,
        fiscal_year=selected_year,
        consultant_id=selected_consultant_id,
        marketing_role_id=selected_marketing_role_id,
        region_id=selected_region_id,
        source=source,
        min_approvals=min_approvals,
        min_approval_rate=min_approval_rate,
        min_match_score=min_match_score,
    )
    if format == "csv":
        return _csv_response(
            "candidate-company-matches.csv",
            report["rows"],
            [
                "rank",
                "consultant_name",
                "consultant_email",
                "consultant_role",
                "company_name",
                "region_name",
                "match_score",
                "watch_score",
                "company_stage",
                "company_recommendation",
                "skill_hits_label",
                "candidate_gaps_label",
                "next_action",
                "approvals",
                "approval_rate",
                "eligible_jobs",
                "verified_jobs",
                "estimated_jobs",
            ],
        )
    pagination = _pagination_context(len(report["rows"]), page, per_page)
    rows = report["rows"][pagination["offset"] : pagination["offset"] + per_page]
    params = {
        "fiscal_year": selected_year or "",
        "consultant_id": selected_consultant_id or "",
        "marketing_role_id": selected_marketing_role_id or "",
        "region_id": selected_region_id or "",
        "source": source,
        "min_approvals": min_approvals,
        "min_approval_rate": min_approval_rate,
        "min_match_score": min_match_score,
        "per_page": per_page,
    }
    return templates.TemplateResponse(
        "web/report_candidate_company_matches.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "summary": report["summary"],
            "consultants": [consultant_profile] if consultant_profile else _active_consultants(db),
            "marketing_roles": _visible_marketing_roles_for_user(db, user),
            "regions": db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all(),
            "available_years": available_years,
            "fiscal_year": selected_year,
            "selected_consultant_id": selected_consultant_id,
            "selected_marketing_role_id": selected_marketing_role_id,
            "selected_region_id": selected_region_id,
            "source": source,
            "source_options": _watchlist_source_options(),
            "min_approvals": min_approvals,
            "min_approval_rate": min_approval_rate,
            "min_match_score": min_match_score,
            "page_params": urlencode(params),
            "export_url": f"/reports/candidate-company-matches?{urlencode({**params, 'format': 'csv'})}",
            **pagination,
        },
    )


@router.get("/reports/role-journeys", response_class=HTMLResponse)
def role_journeys_report(
    request: Request,
    marketing_role_id: str = "",
    owner: str = "",
    stage: str = "all",
    readiness: int = Query(0, ge=0, le=100),
    format: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    selected_marketing_role_id = _optional_query_int(marketing_role_id)
    report = _role_journey_report(
        db,
        marketing_role_id=selected_marketing_role_id,
        owner=owner,
        stage=stage,
        min_readiness=readiness,
    )
    db.commit()
    if format == "csv":
        return _csv_response(
            "role-journeys.csv",
            report["rows"],
            [
                "rank",
                "consultant_name",
                "consultant_email",
                "target_role",
                "target_domain",
                "journey_stage",
                "readiness_score",
                "training_program",
                "campaign_count",
                "submission_count",
                "active_submission_count",
                "next_action",
                "gaps_label",
                "owner_label",
            ],
        )
    params = {
        "marketing_role_id": selected_marketing_role_id or "",
        "owner": owner,
        "stage": stage,
        "readiness": readiness,
    }
    return templates.TemplateResponse(
        "web/report_role_journeys.html",
        {
            "request": request,
            "user": user,
            "rows": report["rows"],
            "summary": report["summary"],
            "stage_rows": report["stage_rows"],
            "owner_options": report["owner_options"],
            "marketing_roles": _active_marketing_roles(db),
            "selected_marketing_role_id": selected_marketing_role_id,
            "owner": owner,
            "stage": stage,
            "stage_options": _role_journey_stage_options(),
            "readiness": readiness,
            "export_url": f"/reports/role-journeys?{urlencode({**params, 'format': 'csv'})}",
        },
    )


@router.get("/reports/companies-by-region", response_class=HTMLResponse)
def companies_by_region_report(
    request: Request,
    decision_type: str = UscisDecisionType.ALL.value,
    start_year: str = "",
    end_year: str = "",
    summary_sort: str = "decisions",
    sort: str = "approvals",
    format: str = "",
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    available_years = _available_uscis_years(db)
    default_start, default_end = _default_year_range(available_years)
    start_year = _optional_query_int(start_year) or default_start
    end_year = _optional_query_int(end_year) or default_end
    if start_year and end_year and start_year > end_year:
        start_year, end_year = end_year, start_year
    report = _companies_by_region_report(db, decision_type=decision_type, start_year=start_year, end_year=end_year, summary_sort=summary_sort, sort=sort)
    if format == "csv":
        return _csv_response(
            "companies-by-region.csv",
            report["company_rows"],
            ["company_id", "company_name", "region_code", "region_name", "tier", "states", "approvals", "denials", "decisions", "approval_rate"],
        )
    pagination = _pagination_context(len(report["company_rows"]), page, per_page)
    company_rows = report["company_rows"][pagination["offset"] : pagination["offset"] + per_page]
    params = {"decision_type": decision_type, "start_year": start_year or "", "end_year": end_year or "", "summary_sort": summary_sort, "sort": sort, "per_page": per_page}
    drilldown_params = {"decision_type": decision_type, "start_year": start_year or "", "end_year": end_year or "", "sort": sort, "per_page": per_page}
    for row in report["summary_rows"]:
        row["drilldown_url"] = f"/reports/companies-by-region/{row['region_code']}?{urlencode(drilldown_params)}"
    return templates.TemplateResponse(
        "web/report_companies_by_region.html",
        {
            "request": request,
            "user": user,
            "decision_type": decision_type,
            "start_year": start_year,
            "end_year": end_year,
            "sort": sort,
            "summary_sort": summary_sort,
            "available_years": available_years,
            "decision_options": _decision_options(),
            "summary_rows": report["summary_rows"],
            "company_rows": company_rows,
            "totals": report["totals"],
            "summary_sort_urls": _named_sort_urls("/reports/companies-by-region", params, "summary_sort", ["region", "tier", "companies", "approvals", "denials", "decisions", "approval_rate"]),
            "sort_urls": _sort_urls("/reports/companies-by-region", params, ["region", "company", "approvals", "denials", "decisions", "approval_rate"]),
            "page_params": urlencode(params),
            "export_url": f"/reports/companies-by-region?{urlencode({**params, 'format': 'csv'})}",
            **pagination,
        },
    )


@router.get("/reports/companies-by-region/{region_code}", response_class=HTMLResponse)
def companies_by_region_drilldown(
    region_code: str,
    request: Request,
    decision_type: str = UscisDecisionType.ALL.value,
    start_year: str = "",
    end_year: str = "",
    sort: str = "approvals",
    format: str = "",
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    available_years = _available_uscis_years(db)
    default_start, default_end = _default_year_range(available_years)
    start_year = _optional_query_int(start_year) or default_start
    end_year = _optional_query_int(end_year) or default_end
    if start_year and end_year and start_year > end_year:
        start_year, end_year = end_year, start_year

    region_code = (region_code or "").strip()
    valid_codes = {item["code"] for item in all_region_metadata()} | {"unknown"}
    if region_code not in valid_codes:
        return RedirectResponse("/reports/companies-by-region", status_code=303)

    report = _companies_by_region_report(db, decision_type=decision_type, start_year=start_year, end_year=end_year, summary_sort="decisions", sort=sort)
    company_rows = [row for row in report["company_rows"] if row["region_code"] == region_code]
    if format == "csv":
        return _csv_response(
            f"companies-by-region-{region_code}.csv",
            company_rows,
            ["company_id", "company_name", "region_code", "region_name", "tier", "states", "approvals", "denials", "decisions", "approval_rate"],
        )
    pagination = _pagination_context(len(company_rows), page, per_page)
    page_rows = company_rows[pagination["offset"] : pagination["offset"] + per_page]
    selected_summary = next((row for row in report["summary_rows"] if row["region_code"] == region_code), _empty_region_summary(region_code))
    region_meta = _region_metadata(region_code)
    params = {"decision_type": decision_type, "start_year": start_year or "", "end_year": end_year or "", "sort": sort, "per_page": per_page}
    back_params = {"decision_type": decision_type, "start_year": start_year or "", "end_year": end_year or "", "summary_sort": "decisions", "sort": sort, "per_page": per_page}
    return templates.TemplateResponse(
        "web/report_region_companies.html",
        {
            "request": request,
            "user": user,
            "region_code": region_code,
            "region_meta": region_meta,
            "selected_summary": selected_summary,
            "company_rows": page_rows,
            "decision_type": decision_type,
            "start_year": start_year,
            "end_year": end_year,
            "sort": sort,
            "available_years": available_years,
            "decision_options": _decision_options(),
            "sort_urls": _sort_urls(f"/reports/companies-by-region/{region_code}", params, ["company", "approvals", "denials", "decisions", "approval_rate"]),
            "page_params": urlencode(params),
            "back_url": f"/reports/companies-by-region?{urlencode(back_params)}",
            "export_url": f"/reports/companies-by-region/{region_code}?{urlencode({**params, 'format': 'csv'})}",
            "standards": _region_drilldown_standards(region_meta),
            **pagination,
        },
    )


@router.get("/companies/{company_id}/uscis", response_class=HTMLResponse)
def company_uscis_detail(company_id: int, request: Request, back: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    company = db.get(Company, company_id)
    if not company:
        return RedirectResponse("/uscis/analysis", status_code=303)
    context = _company_uscis_context(db, company)
    context.update({"request": request, "user": user, "company": company, "back_url": back})
    return templates.TemplateResponse("web/company_uscis_detail.html", context)


@router.post("/companies/{company_id}/promote")
def promote_company(
    request: Request,
    company_id: int,
    reason: str = Form(""),
    next: str = Form("/pursuits"),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    refresh_companies_from_uscis(db, {company_id})
    pursuit = db.scalar(select(CompanyPursuit).where(CompanyPursuit.company_id == company_id))
    promoted_now = pursuit is None
    if pursuit is None:
        pursuit = CompanyPursuit(company_id=company_id, status=PursuitStatus.PROMOTED, pursuit_reason=reason)
        db.add(pursuit)
    else:
        pursuit.status = PursuitStatus.PROMOTED
        if reason:
            pursuit.pursuit_reason = reason
    if pursuit.region_id is None:
        region = recommended_region_for_company(db, company_id)
        if region:
            pursuit.region_id = region.id
    if pursuit.region_id and (promoted_now or not pursuit.assigned_staff_email):
        owner = _recommended_pursuit_owner(db, pursuit.region_id)
        if owner:
            _assign_pursuit_owner(pursuit, owner)
            pursuit.status = PursuitStatus.ASSIGNED
            if not pursuit.next_action:
                pursuit.next_action = "Gather company data, identify open roles and vendors, then add research notes."
        elif pursuit.region and pursuit.region.staff_owner_email and not pursuit.assigned_staff_email:
            pursuit.assigned_staff_name = pursuit.region.staff_owner_name
            pursuit.assigned_staff_email = pursuit.region.staff_owner_email
    db.flush()
    activity(db, pursuit.id, user.email, "promoted", reason or "Promoted company for pursuit")
    if pursuit.assigned_staff_email:
        activity(db, pursuit.id, user.email, "owner_assigned", f"Assigned to {pursuit.assigned_staff_name or pursuit.assigned_staff_email}")
    db.commit()
    _flash(request, f"Promoted {pursuit.company.name} for pursuit.")
    return RedirectResponse(next or "/pursuits", status_code=303)


@router.get("/companies/tools", response_class=HTMLResponse)
def company_tools(
    request: Request,
    q: str = "",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    query = select(Company)
    if q:
        pattern = f"%{q.strip()}%"
        query = query.outerjoin(CompanyAlias).where(Company.name.ilike(pattern) | CompanyAlias.raw_name.ilike(pattern)).distinct()
    query = query.order_by(Company.name.asc())
    total_rows = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = _pagination_context(total_rows, page, per_page)
    companies = db.scalars(query.limit(per_page).offset(pagination["offset"])).all()
    alias_counts = dict(
        db.execute(
            select(CompanyAlias.company_id, func.count(CompanyAlias.id))
            .where(CompanyAlias.company_id.in_([company.id for company in companies] or [0]))
            .group_by(CompanyAlias.company_id)
        ).all()
    )
    duplicate_aliases = db.execute(
        select(CompanyAlias.normalized_name, func.count(distinct(CompanyAlias.company_id)).label("companies"))
        .where(CompanyAlias.normalized_name != "")
        .group_by(CompanyAlias.normalized_name)
        .having(func.count(distinct(CompanyAlias.company_id)) > 1)
        .order_by(func.count(distinct(CompanyAlias.company_id)).desc(), CompanyAlias.normalized_name.asc())
        .limit(12)
    ).mappings().all()
    params = {"q": q, "per_page": per_page}
    return templates.TemplateResponse(
        "web/company_tools.html",
        {
            "request": request,
            "user": user,
            "q": q,
            "companies": companies,
            "alias_counts": alias_counts,
            "duplicate_aliases": duplicate_aliases,
            "page_params": urlencode(params),
            **pagination,
        },
    )


@router.get("/companies/{company_id}/aliases", response_class=HTMLResponse)
def company_aliases(company_id: int, request: Request, q: str = "", user: User = Depends(require_manager), db: Session = Depends(get_db)):
    company = db.get(Company, company_id)
    if not company:
        return RedirectResponse("/companies/tools", status_code=303)
    target_options = []
    if q:
        pattern = f"%{q.strip()}%"
        target_options = db.scalars(select(Company).where(Company.id != company_id, Company.name.ilike(pattern)).order_by(Company.name).limit(20)).all()
    aliases = db.scalars(select(CompanyAlias).where(CompanyAlias.company_id == company_id).order_by(CompanyAlias.normalized_name, CompanyAlias.raw_name)).all()
    merge_audits = db.scalars(
        select(CompanyMergeAudit)
        .where((CompanyMergeAudit.source_company_id == company_id) | (CompanyMergeAudit.target_company_id == company_id))
        .order_by(CompanyMergeAudit.created_at.desc())
        .limit(10)
    ).all()
    return templates.TemplateResponse(
        "web/company_aliases.html",
        {
            "request": request,
            "user": user,
            "company": company,
            "aliases": aliases,
            "q": q,
            "target_options": target_options,
            "merge_audits": merge_audits,
        },
    )


@router.post("/companies/{source_company_id}/merge")
def merge_company(
    source_company_id: int,
    request: Request,
    target_company_id: int = Form(...),
    notes: str = Form(""),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    source = db.get(Company, source_company_id)
    target = db.get(Company, target_company_id)
    if not source or not target or source.id == target.id:
        _flash(request, "Choose two different companies to merge.", "error")
        return RedirectResponse(f"/companies/{source_company_id}/aliases", status_code=303)
    if source.pursuit and target.pursuit:
        _flash(request, "Both companies already have pursuit workspaces. Close or move one workspace before merging.", "error")
        return RedirectResponse(f"/companies/{source_company_id}/aliases", status_code=303)
    _merge_company_records(db, source, target, user.email, notes)
    db.commit()
    _flash(request, f"Merged {source.name} into {target.name}.")
    return RedirectResponse(f"/companies/{target.id}/aliases", status_code=303)


@router.post("/companies/{company_id}/aliases/{alias_id}/move")
def move_company_alias(
    company_id: int,
    alias_id: int,
    request: Request,
    target_company_id: Optional[int] = Form(None),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    company = db.get(Company, company_id)
    alias = db.get(CompanyAlias, alias_id)
    if not company or not alias or alias.company_id != company.id:
        _flash(request, "Alias was not found for this company.", "error")
        return RedirectResponse("/companies/tools", status_code=303)
    target = db.get(Company, target_company_id) if target_company_id else None
    if target is None or target.id == company.id:
        _flash(request, "Pick an existing target company. Mintel companies are created only from USCIS/import normalization.", "error")
        return RedirectResponse(f"/companies/{company.id}/aliases", status_code=303)
    alias.company_id = target.id
    db.add(alias)
    db.execute(update(UscisEmployerYearlyStat).where(UscisEmployerYearlyStat.normalized_employer_name == alias.normalized_name).values(company_id=target.id))
    refresh_companies_from_uscis(db, {company.id, target.id})
    db.commit()
    _flash(request, f"Moved alias {alias.raw_name} to {target.name}.")
    return RedirectResponse(f"/companies/{target.id}/aliases", status_code=303)


@router.get("/pursuits", response_class=HTMLResponse)
def pursuits(
    request: Request,
    q: str = "",
    status: str = "all",
    region_id: str = "",
    owner: str = "all",
    queue: str = "",
    sort: str = "priority",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = select(CompanyPursuit).join(Company).outerjoin(Region)
    visible_clause = _pursuit_visibility_clause(user)
    if visible_clause is not None:
        query = query.where(visible_clause)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                Company.name.ilike(pattern),
                Company.industry.ilike(pattern),
                Company.location.ilike(pattern),
                Region.name.ilike(pattern),
                CompanyPursuit.assigned_staff_name.ilike(pattern),
                CompanyPursuit.assigned_staff_email.ilike(pattern),
            )
        )
    if status != "all" and status in {item["value"] for item in _pursuit_status_options()}:
        query = query.where(CompanyPursuit.status == status)
    selected_region_id = _optional_query_int(region_id)
    if selected_region_id:
        query = query.where(CompanyPursuit.region_id == selected_region_id)
    if owner == "unassigned":
        query = query.where(CompanyPursuit.assigned_staff_email == "")
    elif owner and owner != "all":
        query = query.where(CompanyPursuit.assigned_staff_email == owner)
    if queue == "overdue":
        query = query.where(CompanyPursuit.next_follow_up_date.is_not(None), CompanyPursuit.next_follow_up_date < date.today(), CompanyPursuit.status != PursuitStatus.CLOSED.value)
    elif queue == "needs_owner":
        query = query.where(CompanyPursuit.region_id.is_not(None), CompanyPursuit.assigned_staff_email == "")
    elif queue == "needs_research":
        query = query.where(CompanyPursuit.research_summary == "")
    total_rows = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    sort_map = {
        "company": Company.name.asc(),
        "region": Region.name.asc(),
        "staff": CompanyPursuit.assigned_staff_name.asc(),
        "status": CompanyPursuit.status.asc(),
        "priority": CompanyPursuit.priority.desc(),
        "updated": CompanyPursuit.updated_at.desc(),
    }
    query = query.order_by(sort_map.get(sort, CompanyPursuit.priority.desc()), Company.name.asc())
    pagination = _pagination_context(total_rows, page, per_page)
    rows = db.scalars(query.limit(per_page).offset(pagination["offset"])).all()
    regions = db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all()
    staff_by_region = _staff_options_by_region(db)
    all_staff = _assignable_staff_options(db)
    owner_options = db.scalars(select(CompanyPursuit.assigned_staff_email).where(CompanyPursuit.assigned_staff_email != "").distinct().order_by(CompanyPursuit.assigned_staff_email)).all()
    staff_options_json = _staff_options_payload(db)
    params = {"q": q, "status": status, "region_id": region_id, "owner": owner, "queue": queue, "sort": sort, "per_page": per_page}
    return_url = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return templates.TemplateResponse(
        "web/pursuits.html",
        {
            "request": request,
            "user": user,
            "pursuits": rows,
            "regions": regions,
            "staff_by_region": staff_by_region,
            "all_staff": all_staff,
            "staff_options_json": staff_options_json,
            "owner_options": owner_options,
            "q": q,
            "status": status,
            "selected_region_id": selected_region_id,
            "owner": owner,
            "queue": queue,
            "queue_options": _pursuit_queue_options(),
            "statuses": _pursuit_status_options(),
            "sort": sort,
            "sort_urls": _sort_urls("/pursuits", params, ["company", "region", "staff", "status", "priority", "updated"]),
            "page_params": urlencode(params),
            "return_url": return_url,
            **pagination,
        },
    )


@router.get("/marketing-roles", response_class=HTMLResponse)
def marketing_roles_page(
    request: Request,
    sort: str = "name",
    status: str = "active",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    error: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = select(MarketingRole).outerjoin(User, MarketingRole.owner_id == User.id)
    if status == "active":
        query = query.where(MarketingRole.active.is_(True))
    elif status == "inactive":
        query = query.where(MarketingRole.active.is_(False))
    sort_map = {
        "name": MarketingRole.name.asc(),
        "code": MarketingRole.code.asc(),
        "owner": User.name.asc(),
        "status": MarketingRole.active.desc(),
        "updated": MarketingRole.updated_at.desc(),
    }
    query = query.order_by(sort_map.get(sort, MarketingRole.name.asc()), MarketingRole.id.asc())
    total_rows = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = _pagination_context(total_rows, page, per_page)
    roles = db.scalars(query.limit(per_page).offset(pagination["offset"])).all()
    training_by_role = {
        program.marketing_role_id: program
        for program in db.scalars(
            select(TrainingProgram).where(TrainingProgram.marketing_role_id.in_([role.id for role in roles] or [0]))
        ).all()
    }
    params = {"sort": sort, "status": status, "per_page": per_page}
    return templates.TemplateResponse(
        "web/marketing_roles.html",
        {
            "request": request,
            "user": user,
            "roles": roles,
            "training_by_role": training_by_role,
            "sort": sort,
            "status": status,
            "status_options": [("active", "Active"), ("inactive", "Inactive"), ("all", "All")],
            "sort_urls": _sort_urls("/marketing-roles", params, ["name", "code", "owner", "status", "updated"]),
            "page_params": urlencode(params),
            "error": error,
            "can_manage": has_permission(user, Permission.MANAGE_OPERATIONS),
            **pagination,
        },
    )


@router.get("/consultants", response_class=HTMLResponse)
def consultants_page(
    request: Request,
    q: str = "",
    marketing_role_id: str = "",
    status: str = "active",
    sort: str = "name",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    error: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    selected_marketing_role_id = _optional_query_int(marketing_role_id)
    query = select(ConsultantProfile).outerjoin(MarketingRole)
    visibility_clause = _consultant_visibility_clause(user)
    if visibility_clause is not None:
        query = query.where(visibility_clause)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(
            func.lower(ConsultantProfile.name).like(pattern)
            | func.lower(ConsultantProfile.email).like(pattern)
            | func.lower(ConsultantProfile.primary_skills).like(pattern)
            | func.lower(ConsultantProfile.professional_experience).like(pattern)
            | func.lower(ConsultantProfile.education_summary).like(pattern)
            | func.lower(ConsultantProfile.certifications).like(pattern)
            | func.lower(ConsultantProfile.maas_profile_id).like(pattern)
        )
    if selected_marketing_role_id:
        query = query.where(ConsultantProfile.marketing_role_id == selected_marketing_role_id)
    if status == "active":
        query = query.where(ConsultantProfile.active.is_(True))
    elif status == "inactive":
        query = query.where(ConsultantProfile.active.is_(False))
    elif status in {value for value, _label in _consultant_marketing_status_options()}:
        query = query.where(ConsultantProfile.active.is_(True), ConsultantProfile.marketing_status == status)
    sort_map = {
        "name": ConsultantProfile.name.asc(),
        "email": ConsultantProfile.email.asc(),
        "role": MarketingRole.name.asc(),
        "status": ConsultantProfile.active.desc(),
        "updated": ConsultantProfile.updated_at.desc(),
    }
    query = query.order_by(sort_map.get(sort, ConsultantProfile.name.asc()), ConsultantProfile.email.asc())
    total_rows = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = _pagination_context(total_rows, page, per_page)
    consultants = db.scalars(query.limit(per_page).offset(pagination["offset"])).all()
    params = {"q": q, "marketing_role_id": selected_marketing_role_id or "", "status": status, "sort": sort, "per_page": per_page}
    return templates.TemplateResponse(
        "web/consultants.html",
        {
            "request": request,
            "user": user,
            "consultants": consultants,
            "q": q,
            "marketing_role_id": selected_marketing_role_id,
            "status": status,
            "sort": sort,
            "marketing_roles": _active_marketing_roles(db),
            "status_options": [("active", "Active"), ("inactive", "Inactive"), ("all", "All"), *_consultant_marketing_status_options()],
            "sort_urls": _sort_urls("/consultants", params, ["name", "email", "role", "status", "updated"]),
            "page_params": urlencode(params),
            "error": error,
            **pagination,
        },
    )


@router.get("/consultant-onboarding-workbench", response_class=HTMLResponse)
def consultant_onboarding_workbench(
    request: Request,
    stage: str = "all",
    owner: str = "",
    q: str = "",
    min_readiness: int = Query(0, ge=0, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    context = _consultant_onboarding_workbench_context(db, user, stage=stage, owner=owner, q=q, min_readiness=min_readiness)
    db.commit()
    return templates.TemplateResponse(
        "web/consultant_onboarding_workbench.html",
        {
            "request": request,
            "user": user,
            "stage": stage,
            "owner": owner,
            "q": q,
            "min_readiness": min_readiness,
            **context,
        },
    )


@router.get("/consultant-onboarding-questionnaire", response_class=HTMLResponse)
def consultant_onboarding_questionnaire(request: Request, user: User = Depends(require_manager)):
    return templates.TemplateResponse(
        "web/consultant_onboarding_questionnaire.html",
        {
            "request": request,
            "user": user,
            **_consultant_onboarding_questionnaire_context(),
        },
    )


@router.get("/public/consultant-intake", response_class=HTMLResponse)
def public_consultant_intake_form(request: Request, error: str = "", submitted: bool = False):
    return templates.TemplateResponse(
        "web/consultant_public_intake.html",
        {
            "request": request,
            "error": error,
            "submitted": submitted,
            "industry_domains": INDUSTRY_DOMAINS,
        },
    )


@router.post("/public/consultant-intake")
def submit_public_consultant_intake(
    request: Request,
    name: str = Form(...),
    preferred_name: str = Form(""),
    email: str = Form(...),
    phone: str = Form(""),
    linkedin_url: str = Form(""),
    current_location: str = Form(""),
    relocation_preference: str = Form(""),
    onsite_preference: str = Form(""),
    work_authorization: str = Form(""),
    visa_valid_until: str = Form(""),
    ead_valid_until: str = Form(""),
    availability: str = Form(""),
    rate_expectation: str = Form(""),
    years_experience: str = Form(""),
    undergrad_degree: str = Form(""),
    undergrad_university: str = Form(""),
    undergrad_start_date: str = Form(""),
    undergrad_end_date: str = Form(""),
    masters_degree: str = Form(""),
    masters_university: str = Form(""),
    masters_start_date: str = Form(""),
    masters_end_date: str = Form(""),
    certifications: str = Form(""),
    professional_experience: str = Form(""),
    previous_experience: str = Form(""),
    domain_experience: str = Form(""),
    learned_last_6_months: str = Form(""),
    target_marketing_role: str = Form(""),
    target_industry_domain: str = Form(""),
    primary_skills: str = Form(""),
    profile_strengths: str = Form(""),
    profile_gaps: str = Form(""),
    latest_project_title: str = Form(""),
    latest_project_domain: str = Form(""),
    latest_project_summary: str = Form(""),
    base_resume_reference: str = Form(""),
    resume_summary: str = Form(""),
    documents_available: str = Form(""),
    db: Session = Depends(get_db),
):
    normalized_email = _normalize_email(email)
    if not normalized_email:
        return RedirectResponse("/public/consultant-intake?error=Email+is+required", status_code=303)
    existing = db.scalar(select(ConsultantProfile).where(func.lower(ConsultantProfile.email) == normalized_email.lower()))
    if existing:
        return RedirectResponse("/public/consultant-intake?error=An+intake+or+profile+already+exists+for+this+email", status_code=303)
    education_summary = _public_intake_education_summary(
        undergrad_degree,
        undergrad_university,
        undergrad_start_date,
        undergrad_end_date,
        masters_degree,
        masters_university,
        masters_start_date,
        masters_end_date,
    )
    consultant = ConsultantProfile(
        name=name.strip(),
        preferred_name=preferred_name.strip(),
        email=normalized_email,
        phone=phone.strip(),
        linkedin_url=linkedin_url.strip(),
        current_location=current_location.strip(),
        relocation_preference=relocation_preference.strip(),
        onsite_preference=onsite_preference.strip(),
        work_authorization=work_authorization.strip(),
        visa_valid_until=visa_valid_until.strip(),
        ead_valid_until=ead_valid_until.strip(),
        availability=availability.strip(),
        rate_expectation=rate_expectation.strip(),
        years_experience=years_experience.strip(),
        education_summary=education_summary,
        certifications=certifications.strip(),
        professional_experience=_join_note_sections(
            ("Current experience", professional_experience),
            ("Previous experience", previous_experience),
            ("Learned in last 6 months", learned_last_6_months),
        ),
        domain_experience=domain_experience.strip(),
        target_industry_domain=target_industry_domain.strip(),
        primary_skills=primary_skills.strip(),
        profile_strengths=profile_strengths.strip(),
        profile_gaps=profile_gaps.strip(),
        latest_project_title=latest_project_title.strip(),
        latest_project_domain=latest_project_domain.strip(),
        latest_project_summary=latest_project_summary.strip(),
        base_resume_reference=base_resume_reference.strip(),
        resume_summary=resume_summary.strip(),
        marketing_status="profile_intake",
        maas_sync_status="needs_review",
        notes=_join_note_sections(
            ("Requested marketing role", target_marketing_role),
            ("Documents available", documents_available),
            ("Public intake source", "Submitted from /public/consultant-intake"),
        ),
        active=False,
    )
    db.add(consultant)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/public/consultant-intake?error=An+intake+or+profile+already+exists+for+this+email", status_code=303)
    return RedirectResponse("/public/consultant-intake?submitted=true", status_code=303)


@router.get("/consultants/{consultant_id}/onboarding-questionnaire", response_class=HTMLResponse)
def consultant_onboarding_questionnaire_for_consultant(consultant_id: int, request: Request, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant:
        return RedirectResponse("/consultants", status_code=303)
    if not _can_access_consultant(consultant, user):
        raise PermissionDenied("This consultant is outside your assigned onboarding coverage.")
    return templates.TemplateResponse(
        "web/consultant_onboarding_questionnaire.html",
        {
            "request": request,
            "user": user,
            **_consultant_onboarding_questionnaire_context(consultant),
        },
    )


@router.get("/consultants/new", response_class=HTMLResponse)
def new_consultant_form(request: Request, error: str = "", user: User = Depends(require_manager), db: Session = Depends(get_db)):
    return templates.TemplateResponse("web/consultant_form.html", {"request": request, "user": user, "consultant": None, "access_user": None, "marketing_roles": _active_marketing_roles(db), "industry_domains": INDUSTRY_DOMAINS, "marketing_status_options": _consultant_marketing_status_options(), "owner_options": _assignable_staff_options(db), "error": error, "marketing_ready_gate": None})


@router.post("/consultants")
def create_consultant(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    preferred_name: str = Form(""),
    linkedin_url: str = Form(""),
    current_location: str = Form(""),
    relocation_preference: str = Form(""),
    onsite_preference: str = Form(""),
    work_authorization: str = Form(""),
    visa_valid_until: str = Form(""),
    ead_valid_until: str = Form(""),
    years_experience: str = Form(""),
    professional_experience: str = Form(""),
    domain_experience: str = Form(""),
    marketing_role_id: Optional[int] = Form(None),
    target_industry_domain: str = Form(""),
    marketing_status: str = Form("profile_intake"),
    primary_skills: str = Form(""),
    certifications: str = Form(""),
    education_summary: str = Form(""),
    resume_summary: str = Form(""),
    base_resume_reference: str = Form(""),
    latest_project_title: str = Form(""),
    latest_project_domain: str = Form(""),
    latest_project_summary: str = Form(""),
    resume_readiness_score: str = Form("0"),
    technical_readiness_score: str = Form("0"),
    interview_readiness_score: str = Form("0"),
    communication_score: str = Form("0"),
    profile_intake_complete: bool = Form(False),
    education_verified: bool = Form(False),
    certifications_verified: bool = Form(False),
    experience_verified: bool = Form(False),
    base_resume_received: bool = Form(False),
    resume_tailoring_complete: bool = Form(False),
    latest_project_updated: bool = Form(False),
    project_story_validated: bool = Form(False),
    basics_prep_complete: bool = Form(False),
    training_plan_assigned: bool = Form(False),
    glossary_review_complete: bool = Form(False),
    mock_interview_passed: bool = Form(False),
    marketing_brief_ready: bool = Form(False),
    checklist_notes: str = Form(""),
    availability: str = Form(""),
    rate_expectation: str = Form(""),
    staff_owner: str = Form(""),
    recruiter_owner: str = Form(""),
    profile_strengths: str = Form(""),
    profile_gaps: str = Form(""),
    marketing_notes: str = Form(""),
    placement_company: str = Form(""),
    placement_role: str = Form(""),
    placement_start_date: str = Form(""),
    placement_notes: str = Form(""),
    maas_profile_id: str = Form(""),
    maas_sync_status: str = Form("not_synced"),
    maas_last_synced_at: str = Form(""),
    maas_payload_notes: str = Form(""),
    notes: str = Form(""),
    enable_consultant_access: bool = Form(False),
    consultant_access_password: str = Form(""),
    active: bool = Form(False),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    normalized_email = _normalize_email(email)
    if _consultant_profile_for_email(db, normalized_email):
        return RedirectResponse(
            f"/consultants/new?{urlencode({'error': f'A consultant profile already exists for {normalized_email}. Open the existing profile and enable portal access there.'})}",
            status_code=303,
        )
    existing_login = _consultant_access_user(db, normalized_email)
    if enable_consultant_access and existing_login and getattr(existing_login.role, "value", existing_login.role) != UserRole.CONSULTANT.value:
        return RedirectResponse(
            f"/consultants/new?{urlencode({'error': f'A non-consultant login already exists for {normalized_email}. Use a different email or convert that user before enabling consultant access.'})}",
            status_code=303,
        )
    consultant = ConsultantProfile(
        name=name.strip(),
        email=normalized_email,
        phone=phone.strip(),
        preferred_name=preferred_name.strip(),
        linkedin_url=linkedin_url.strip(),
        current_location=current_location.strip(),
        relocation_preference=relocation_preference.strip(),
        onsite_preference=onsite_preference.strip(),
        work_authorization=work_authorization.strip(),
        visa_valid_until=visa_valid_until.strip(),
        ead_valid_until=ead_valid_until.strip(),
        years_experience=years_experience.strip(),
        professional_experience=professional_experience.strip(),
        domain_experience=domain_experience.strip(),
        marketing_role_id=marketing_role_id,
        target_industry_domain=target_industry_domain.strip(),
        marketing_status=marketing_status.strip() or "profile_intake",
        primary_skills=primary_skills.strip(),
        certifications=certifications.strip(),
        education_summary=education_summary.strip(),
        resume_summary=resume_summary.strip(),
        base_resume_reference=base_resume_reference.strip(),
        latest_project_title=latest_project_title.strip(),
        latest_project_domain=latest_project_domain.strip(),
        latest_project_summary=latest_project_summary.strip(),
        resume_readiness_score=_bounded_form_score(resume_readiness_score),
        technical_readiness_score=_bounded_form_score(technical_readiness_score),
        interview_readiness_score=_bounded_form_score(interview_readiness_score),
        communication_score=_bounded_form_score(communication_score),
        profile_intake_complete=profile_intake_complete,
        education_verified=education_verified,
        certifications_verified=certifications_verified,
        experience_verified=experience_verified,
        base_resume_received=base_resume_received,
        resume_tailoring_complete=resume_tailoring_complete,
        latest_project_updated=latest_project_updated,
        project_story_validated=project_story_validated,
        basics_prep_complete=basics_prep_complete,
        training_plan_assigned=training_plan_assigned,
        glossary_review_complete=glossary_review_complete,
        mock_interview_passed=mock_interview_passed,
        marketing_brief_ready=marketing_brief_ready,
        checklist_notes=checklist_notes.strip(),
        availability=availability.strip(),
        rate_expectation=rate_expectation.strip(),
        staff_owner=staff_owner.strip(),
        recruiter_owner=recruiter_owner.strip(),
        profile_strengths=profile_strengths.strip(),
        profile_gaps=profile_gaps.strip(),
        marketing_notes=marketing_notes.strip(),
        placement_company=placement_company.strip(),
        placement_role=placement_role.strip(),
        placement_start_date=_parse_date(placement_start_date),
        placement_notes=placement_notes.strip(),
        maas_profile_id=maas_profile_id.strip(),
        maas_sync_status=maas_sync_status.strip() or "not_synced",
        maas_last_synced_at=maas_last_synced_at.strip(),
        maas_payload_notes=maas_payload_notes.strip(),
        notes=notes.strip(),
        active=active,
    )
    gate = marketing_ready_context(consultant)
    activation_error = _consultant_activation_error(db, consultant)
    if activation_error:
        return RedirectResponse(f"/consultants/new?{urlencode({'error': activation_error})}", status_code=303)
    if consultant.marketing_status in MARKETING_READY_STATUSES:
        if not gate["ready"]:
            return RedirectResponse(f"/consultants/new?{urlencode({'error': 'Marketing Ready requires: ' + ', '.join(gate['missing_labels'])})}", status_code=303)
        if not user_can_mark_marketing_ready(user, consultant):
            return RedirectResponse(f"/consultants/new?{urlencode({'error': 'Only the assigned staff owner or admin can mark a consultant Marketing Ready.'})}", status_code=303)
    db.add(consultant)
    try:
        db.flush()
        _sync_basics_to_training_assignment(db, consultant)
        if enable_consultant_access:
            _sync_consultant_access_user(db, consultant, consultant_access_password)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/consultants/new?{urlencode({'error': str(exc)})}", status_code=303)
    except IntegrityError:
        db.rollback()
        return RedirectResponse(f"/consultants/new?{urlencode({'error': 'Could not create consultant because a consultant profile, login email, or login username already exists. Search for the email/name and update the existing record.'})}", status_code=303)
    _flash(request, f"Created consultant {consultant.name}.")
    return RedirectResponse(f"/consultants/{consultant.id}", status_code=303)


@router.get("/consultants/{consultant_id}", response_class=HTMLResponse)
def consultant_detail(consultant_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant:
        return RedirectResponse("/consultants", status_code=303)
    if not _can_access_consultant(consultant, user):
        raise PermissionDenied("This consultant is outside your assigned onboarding coverage.")
    readiness_items = _consultant_readiness_items(consultant)
    completed = sum(1 for item in readiness_items if item["done"])
    lifecycle_hub = _consultant_lifecycle_hub_context(db, consultant) if consultant.active else _consultant_lifecycle_operating_model(consultant)
    if consultant.active:
        db.commit()
    return templates.TemplateResponse(
        "web/consultant_detail.html",
        {
            "request": request,
            "user": user,
            "consultant": consultant,
            "readiness_items": readiness_items,
            "readiness_completed": completed,
            "readiness_total": len(readiness_items),
            "marketing_ready_gate": marketing_ready_context(consultant),
            "lifecycle_hub": lifecycle_hub,
        },
    )


@router.get("/consultants/{consultant_id}/positioning", response_class=HTMLResponse)
def consultant_positioning_brief(consultant_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant or not consultant.active:
        return RedirectResponse("/consultants", status_code=303)
    if not _can_access_consultant(consultant, user):
        raise PermissionDenied("This consultant is outside your assigned onboarding coverage.")
    context = _consultant_positioning_context(db, consultant)
    return templates.TemplateResponse(
        "web/consultant_positioning.html",
        {
            "request": request,
            "user": user,
            "consultant": consultant,
            **context,
        },
    )


@router.get("/consultants/{consultant_id}/journey", response_class=HTMLResponse)
def consultant_journey_detail(consultant_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant or not consultant.active:
        return RedirectResponse("/consultants", status_code=303)
    if not _can_access_consultant(consultant, user):
        raise PermissionDenied("This consultant is outside your assigned onboarding coverage.")
    journey = _ensure_consultant_role_journey(db, consultant)
    db.commit()
    context = _consultant_journey_context(db, consultant, journey)
    return templates.TemplateResponse(
        "web/consultant_journey.html",
        {
            "request": request,
            "user": user,
            "consultant": consultant,
            "journey": journey,
            "can_manage_journey": _can_manage_consultant_journey(user, consultant),
            **context,
        },
    )


@router.get("/consultants/{consultant_id}/onboarding", response_class=HTMLResponse)
def consultant_onboarding_page(consultant_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant or not consultant.active:
        return RedirectResponse("/consultants", status_code=303)
    if not _can_access_consultant(consultant, user):
        raise PermissionDenied("This consultant is outside your assigned onboarding coverage.")
    journey = _ensure_consultant_role_journey(db, consultant)
    db.commit()
    context = _consultant_onboarding_context(db, consultant, journey)
    return templates.TemplateResponse(
        "web/consultant_onboarding.html",
        {
            "request": request,
            "user": user,
            "consultant": consultant,
            "journey": journey,
            **context,
        },
    )


@router.post("/consultants/{consultant_id}/onboarding/activities/{activity_id}")
def update_consultant_onboarding_activity(
    consultant_id: int,
    activity_id: int,
    request: Request,
    evidence_url: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant:
        return RedirectResponse("/consultants", status_code=303)
    if not _can_access_consultant(consultant, user):
        raise PermissionDenied("This consultant is outside your assigned onboarding coverage.")
    journey = _ensure_consultant_role_journey(db, consultant)
    activity = db.get(ConsultantJourneyActivity, activity_id)
    if not activity or activity.journey_id != journey.id:
        return RedirectResponse(f"/consultants/{consultant_id}/onboarding", status_code=303)
    activity.evidence_url = evidence_url.strip()
    activity.notes = notes.strip()
    if activity.status == ConsultantJourneyActivityStatus.TODO.value and (activity.evidence_url or activity.notes):
        activity.status = ConsultantJourneyActivityStatus.IN_PROGRESS.value
    db.add(activity)
    _refresh_consultant_role_journey(db, consultant, journey, preserve_manual=True)
    db.commit()
    _flash(request, "Saved your update for evidence review.")
    return RedirectResponse(f"/consultants/{consultant_id}/onboarding", status_code=303)


@router.post("/consultants/{consultant_id}/journey/activities/{activity_id}")
def update_consultant_journey_activity(
    consultant_id: int,
    activity_id: int,
    request: Request,
    status: str = Form("todo"),
    due_date: str = Form(""),
    evidence_url: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant:
        return RedirectResponse("/consultants", status_code=303)
    if not _can_access_consultant(consultant, user):
        raise PermissionDenied("This consultant is outside your assigned onboarding coverage.")
    if not _can_manage_consultant_journey(user, consultant):
        raise PermissionDenied("You can view this consultant, but cannot update the journey checklist.")
    journey = _ensure_consultant_role_journey(db, consultant)
    activity = db.get(ConsultantJourneyActivity, activity_id)
    if not activity or activity.journey_id != journey.id:
        return RedirectResponse(f"/consultants/{consultant_id}/journey", status_code=303)
    if status not in _journey_activity_status_values():
        status = ConsultantJourneyActivityStatus.TODO.value
    previous_status = activity.status
    activity.status = status
    activity.due_date = _parse_date(due_date)
    activity.evidence_url = evidence_url.strip()
    activity.notes = notes.strip()
    if status == ConsultantJourneyActivityStatus.COMPLETED.value and previous_status != ConsultantJourneyActivityStatus.COMPLETED.value:
        activity.completed_at = datetime.now(timezone.utc)
        activity.completed_by_id = user.id
    elif status != ConsultantJourneyActivityStatus.COMPLETED.value:
        activity.completed_at = None
        activity.completed_by_id = None
    db.add(activity)
    _sync_consultant_flag_from_activity(consultant, activity)
    _refresh_consultant_role_journey(db, consultant, journey)
    db.commit()
    _flash(request, f"Updated {activity.title}.")
    return RedirectResponse(f"/consultants/{consultant_id}/journey", status_code=303)


@router.post("/consultants/{consultant_id}/journey")
def update_consultant_journey(
    consultant_id: int,
    request: Request,
    current_stage: str = Form(""),
    status: str = Form("active"),
    target_market_date: str = Form(""),
    positioning_summary: str = Form(""),
    next_action: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant:
        return RedirectResponse("/consultants", status_code=303)
    if not _can_access_consultant(consultant, user):
        raise PermissionDenied("This consultant is outside your assigned onboarding coverage.")
    if not _can_manage_consultant_journey(user, consultant):
        raise PermissionDenied("You can view this consultant, but cannot update the journey checklist.")
    journey = _ensure_consultant_role_journey(db, consultant)
    if current_stage in {value for value, _ in _role_journey_stage_options() if value != "all"}:
        journey.current_stage = current_stage
    if status in _journey_status_values():
        journey.status = status
    journey.target_market_date = _parse_date(target_market_date)
    journey.positioning_summary = positioning_summary.strip()
    journey.next_action = next_action.strip()
    journey.notes = notes.strip()
    _refresh_consultant_role_journey(db, consultant, journey, preserve_manual=True)
    db.commit()
    _flash(request, "Updated consultant journey.")
    return RedirectResponse(f"/consultants/{consultant_id}/journey", status_code=303)


@router.get("/consultants/{consultant_id}/edit", response_class=HTMLResponse)
def edit_consultant_form(consultant_id: int, request: Request, error: str = "", user: User = Depends(require_manager), db: Session = Depends(get_db)):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant:
        return RedirectResponse("/consultants", status_code=303)
    return templates.TemplateResponse("web/consultant_form.html", {"request": request, "user": user, "consultant": consultant, "access_user": _consultant_access_user(db, consultant.email), "marketing_roles": _active_marketing_roles(db), "industry_domains": INDUSTRY_DOMAINS, "marketing_status_options": _consultant_marketing_status_options(), "owner_options": _assignable_staff_options(db), "error": error, "marketing_ready_gate": marketing_ready_context(consultant)})


@router.post("/consultants/{consultant_id}")
def update_consultant(
    request: Request,
    consultant_id: int,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    preferred_name: str = Form(""),
    linkedin_url: str = Form(""),
    current_location: str = Form(""),
    relocation_preference: str = Form(""),
    onsite_preference: str = Form(""),
    work_authorization: str = Form(""),
    visa_valid_until: str = Form(""),
    ead_valid_until: str = Form(""),
    years_experience: str = Form(""),
    professional_experience: str = Form(""),
    domain_experience: str = Form(""),
    marketing_role_id: Optional[int] = Form(None),
    target_industry_domain: str = Form(""),
    marketing_status: str = Form("profile_intake"),
    primary_skills: str = Form(""),
    certifications: str = Form(""),
    education_summary: str = Form(""),
    resume_summary: str = Form(""),
    base_resume_reference: str = Form(""),
    latest_project_title: str = Form(""),
    latest_project_domain: str = Form(""),
    latest_project_summary: str = Form(""),
    resume_readiness_score: str = Form("0"),
    technical_readiness_score: str = Form("0"),
    interview_readiness_score: str = Form("0"),
    communication_score: str = Form("0"),
    profile_intake_complete: bool = Form(False),
    education_verified: bool = Form(False),
    certifications_verified: bool = Form(False),
    experience_verified: bool = Form(False),
    base_resume_received: bool = Form(False),
    resume_tailoring_complete: bool = Form(False),
    latest_project_updated: bool = Form(False),
    project_story_validated: bool = Form(False),
    basics_prep_complete: bool = Form(False),
    training_plan_assigned: bool = Form(False),
    glossary_review_complete: bool = Form(False),
    mock_interview_passed: bool = Form(False),
    marketing_brief_ready: bool = Form(False),
    checklist_notes: str = Form(""),
    availability: str = Form(""),
    rate_expectation: str = Form(""),
    staff_owner: str = Form(""),
    recruiter_owner: str = Form(""),
    profile_strengths: str = Form(""),
    profile_gaps: str = Form(""),
    marketing_notes: str = Form(""),
    placement_company: str = Form(""),
    placement_role: str = Form(""),
    placement_start_date: str = Form(""),
    placement_notes: str = Form(""),
    maas_profile_id: str = Form(""),
    maas_sync_status: str = Form("not_synced"),
    maas_last_synced_at: str = Form(""),
    maas_payload_notes: str = Form(""),
    notes: str = Form(""),
    enable_consultant_access: bool = Form(False),
    consultant_access_password: str = Form(""),
    active: bool = Form(False),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant:
        return RedirectResponse("/consultants", status_code=303)
    previous_email = consultant.email
    normalized_email = _normalize_email(email)
    existing_consultant = _consultant_profile_for_email(db, normalized_email, exclude_id=consultant.id)
    if existing_consultant:
        return RedirectResponse(
            f"/consultants/{consultant_id}/edit?{urlencode({'error': f'Another consultant profile already exists for {normalized_email}: {existing_consultant.name or existing_consultant.email}.'})}",
            status_code=303,
        )
    existing_login = _consultant_access_user(db, normalized_email)
    if enable_consultant_access and existing_login and getattr(existing_login.role, "value", existing_login.role) != UserRole.CONSULTANT.value:
        return RedirectResponse(
            f"/consultants/{consultant_id}/edit?{urlencode({'error': f'A non-consultant login already exists for {normalized_email}. Use a different email or convert that user before enabling consultant access.'})}",
            status_code=303,
        )
    consultant.name = name.strip()
    consultant.email = normalized_email
    consultant.phone = phone.strip()
    consultant.preferred_name = preferred_name.strip()
    consultant.linkedin_url = linkedin_url.strip()
    consultant.current_location = current_location.strip()
    consultant.relocation_preference = relocation_preference.strip()
    consultant.onsite_preference = onsite_preference.strip()
    consultant.work_authorization = work_authorization.strip()
    consultant.visa_valid_until = visa_valid_until.strip()
    consultant.ead_valid_until = ead_valid_until.strip()
    consultant.years_experience = years_experience.strip()
    consultant.professional_experience = professional_experience.strip()
    consultant.domain_experience = domain_experience.strip()
    consultant.marketing_role_id = marketing_role_id
    consultant.target_industry_domain = target_industry_domain.strip()
    consultant.marketing_status = marketing_status.strip() or "profile_intake"
    consultant.primary_skills = primary_skills.strip()
    consultant.certifications = certifications.strip()
    consultant.education_summary = education_summary.strip()
    consultant.resume_summary = resume_summary.strip()
    consultant.base_resume_reference = base_resume_reference.strip()
    consultant.latest_project_title = latest_project_title.strip()
    consultant.latest_project_domain = latest_project_domain.strip()
    consultant.latest_project_summary = latest_project_summary.strip()
    consultant.resume_readiness_score = _bounded_form_score(resume_readiness_score)
    consultant.technical_readiness_score = _bounded_form_score(technical_readiness_score)
    consultant.interview_readiness_score = _bounded_form_score(interview_readiness_score)
    consultant.communication_score = _bounded_form_score(communication_score)
    consultant.profile_intake_complete = profile_intake_complete
    consultant.education_verified = education_verified
    consultant.certifications_verified = certifications_verified
    consultant.experience_verified = experience_verified
    consultant.base_resume_received = base_resume_received
    consultant.resume_tailoring_complete = resume_tailoring_complete
    consultant.latest_project_updated = latest_project_updated
    consultant.project_story_validated = project_story_validated
    consultant.basics_prep_complete = basics_prep_complete
    consultant.training_plan_assigned = training_plan_assigned
    consultant.glossary_review_complete = glossary_review_complete
    consultant.mock_interview_passed = mock_interview_passed
    consultant.marketing_brief_ready = marketing_brief_ready
    consultant.checklist_notes = checklist_notes.strip()
    consultant.availability = availability.strip()
    consultant.rate_expectation = rate_expectation.strip()
    consultant.staff_owner = staff_owner.strip()
    consultant.recruiter_owner = recruiter_owner.strip()
    consultant.profile_strengths = profile_strengths.strip()
    consultant.profile_gaps = profile_gaps.strip()
    consultant.marketing_notes = marketing_notes.strip()
    consultant.placement_company = placement_company.strip()
    consultant.placement_role = placement_role.strip()
    consultant.placement_start_date = _parse_date(placement_start_date)
    consultant.placement_notes = placement_notes.strip()
    consultant.maas_profile_id = maas_profile_id.strip()
    consultant.maas_sync_status = maas_sync_status.strip() or "not_synced"
    consultant.maas_last_synced_at = maas_last_synced_at.strip()
    consultant.maas_payload_notes = maas_payload_notes.strip()
    consultant.notes = notes.strip()
    consultant.active = active
    _sync_basics_to_training_assignment(db, consultant)
    gate = marketing_ready_context(consultant)
    activation_error = _consultant_activation_error(db, consultant)
    if activation_error:
        return RedirectResponse(f"/consultants/{consultant_id}/edit?{urlencode({'error': activation_error})}", status_code=303)
    if consultant.marketing_status in MARKETING_READY_STATUSES:
        if not gate["ready"]:
            return RedirectResponse(f"/consultants/{consultant_id}/edit?{urlencode({'error': 'Marketing Ready requires: ' + ', '.join(gate['missing_labels'])})}", status_code=303)
        if not user_can_mark_marketing_ready(user, consultant):
            return RedirectResponse(f"/consultants/{consultant_id}/edit?{urlencode({'error': 'Only the assigned staff owner or admin can mark a consultant Marketing Ready.'})}", status_code=303)
    db.add(consultant)
    try:
        if enable_consultant_access:
            _sync_consultant_access_user(db, consultant, consultant_access_password, previous_email=previous_email)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/consultants/{consultant_id}/edit?{urlencode({'error': str(exc)})}", status_code=303)
    except IntegrityError:
        db.rollback()
        return RedirectResponse(f"/consultants/{consultant_id}/edit?{urlencode({'error': 'Could not update consultant because a consultant profile, login email, or login username already exists. Search for the email/name and update the existing record.'})}", status_code=303)
    _flash(request, f"Updated consultant {consultant.name}.")
    return RedirectResponse(f"/consultants/{consultant.id}", status_code=303)


@router.post("/consultants/{consultant_id}/delete")
def delete_consultant(consultant_id: int, request: Request, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    consultant = db.get(ConsultantProfile, consultant_id)
    if consultant:
        if consultant.active:
            consultant.active = False
            db.add(consultant)
            _flash(request, f"Deactivated consultant {consultant.name}.")
        else:
            _flash(request, f"Deleted consultant {consultant.name}.")
            db.delete(consultant)
        db.commit()
    return RedirectResponse("/consultants", status_code=303)


def _consultant_readiness_items(consultant: ConsultantProfile) -> list[dict[str, Any]]:
    return [
        {"key": "profile_intake_complete", "label": "Profile intake completed", "done": consultant.profile_intake_complete},
        {"key": "education_verified", "label": "Education details verified", "done": consultant.education_verified},
        {"key": "certifications_verified", "label": "Certifications captured and verified", "done": consultant.certifications_verified},
        {"key": "experience_verified", "label": "Experience, domains, and project timeline verified", "done": consultant.experience_verified},
        {"key": "base_resume_received", "label": "Base resume received from consultant", "done": consultant.base_resume_received},
        {"key": "resume_tailoring_complete", "label": "Resume tailored for target role and ATS", "done": consultant.resume_tailoring_complete},
        {"key": "latest_project_updated", "label": "Latest project updated with Mintel/MAAS story", "done": consultant.latest_project_updated},
        {"key": "project_story_validated", "label": "Project stories validated for interview delivery", "done": consultant.project_story_validated},
        {"key": "basics_prep_complete", "label": "Basics Preparation completed", "done": getattr(consultant, "basics_prep_complete", False)},
        {"key": "training_plan_assigned", "label": "Role/domain training plan assigned", "done": consultant.training_plan_assigned},
        {"key": "glossary_review_complete", "label": "Role glossary and product vocabulary reviewed", "done": consultant.glossary_review_complete},
        {"key": "mock_interview_passed", "label": "Mock interview passed for marketing role", "done": consultant.mock_interview_passed},
        {"key": "marketing_brief_ready", "label": "Final evidence package ready for recruiter/staff handoff", "done": consultant.marketing_brief_ready},
    ]


def _consultant_positioning_context(db: Session, consultant: ConsultantProfile) -> dict[str, Any]:
    journey_report = _role_journey_report(db, marketing_role_id=consultant.marketing_role_id, owner="", stage="all", min_readiness=0)
    journey = next((row for row in journey_report["rows"] if row["consultant_id"] == consultant.id), None)
    if not journey:
        program = _consultant_training_program(db, consultant)
        gaps = _role_journey_gaps(consultant, program, [], [], _candidate_readiness_score(consultant))
        journey = {
            "target_role": consultant.marketing_role.name if consultant.marketing_role else "Role not set",
            "target_domain": consultant.target_industry_domain or "Domain not set",
            "stage_label": "Profile intake",
            "readiness_score": _candidate_readiness_score(consultant),
            "next_action": "Complete consultant profile and role readiness before active marketing.",
            "gaps": gaps,
            "gaps_label": ", ".join(gaps) or "No major blockers",
            "positioning": _role_positioning_brief(consultant, program, gaps),
            "training_program": program.title if program else "No role/domain program assigned",
            "training_program_id": program.id if program else None,
            "match_url": f"/reports/candidate-company-matches?consultant_id={consultant.id}&min_match_score=35",
        }
    available_years = _available_uscis_years(db)
    latest_year = max(available_years) if available_years else None
    matches = _candidate_company_matches_report(
        db,
        fiscal_year=latest_year,
        consultant_id=consultant.id,
        marketing_role_id=consultant.marketing_role_id,
        region_id=None,
        source="all",
        min_approvals=5,
        min_approval_rate=75,
        min_match_score=35,
    )["rows"][:8]
    role_name = consultant.marketing_role.name if consultant.marketing_role else "target role"
    skills = _split_training_items(consultant.primary_skills)
    resume_headline = _positioning_resume_headline(consultant, role_name, skills)
    return {
        "journey": journey,
        "matches": matches,
        "latest_year": latest_year,
        "evidence_package": _final_evidence_package_context(db, consultant.id),
        "resume_headline": resume_headline,
        "role_pitch": _positioning_role_pitch(consultant, role_name, skills),
        "resume_bullets": _positioning_resume_bullets(consultant, role_name, skills, matches),
        "company_talking_points": _positioning_company_talking_points(matches),
        "interview_story_flow": _positioning_interview_story_flow(consultant, role_name),
        "gap_plan": _positioning_gap_plan(journey.get("gaps", []), matches),
    }


def _consultant_lifecycle_hub_context(db: Session, consultant: ConsultantProfile) -> dict[str, Any]:
    journey = _ensure_consultant_role_journey(db, consultant)
    activities = db.scalars(select(ConsultantJourneyActivity).where(ConsultantJourneyActivity.journey_id == journey.id).order_by(ConsultantJourneyActivity.sequence)).all()
    program = journey.training_program or _consultant_training_program(db, consultant)
    evidence_package = _final_evidence_package_context(db, consultant.id, activities)
    model = _consultant_lifecycle_operating_model(consultant, journey=journey, program=program, activities=activities, evidence_package=evidence_package)
    model["links"] = {
        "onboarding": f"/consultants/{consultant.id}/onboarding",
        "journey": f"/consultants/{consultant.id}/journey",
        "positioning": f"/consultants/{consultant.id}/positioning",
        "training": f"/training-programs/{program.id}" if program else "/training-programs",
        "basics": "/training-basics",
        "matches": f"/reports/candidate-company-matches?consultant_id={consultant.id}&min_match_score=35",
        "submissions": f"/submissions?consultant_id={consultant.id}",
    }
    return model


def _consultant_lifecycle_operating_model(
    consultant: Any,
    *,
    journey: Optional[Any] = None,
    program: Optional[Any] = None,
    activities: Optional[list[Any]] = None,
    evidence_package: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    activities = activities or []
    evidence_package = evidence_package or {"completed": 0, "total": 8, "percent": 0}
    role_name = getattr(getattr(consultant, "marketing_role", None), "name", "") or "Target role not set"
    domain = getattr(consultant, "target_industry_domain", "") or getattr(consultant, "latest_project_domain", "") or "Domain not set"
    owner = (getattr(consultant, "staff_owner", "") or "").strip()
    recruiter = (getattr(consultant, "recruiter_owner", "") or "").strip()
    journey_stage = getattr(journey, "current_stage", "") or "profile_intake"
    journey_status = getattr(journey, "status", "") or getattr(consultant, "marketing_status", "profile_intake")
    completed_activities = len([activity for activity in activities if getattr(activity, "status", "") == ConsultantJourneyActivityStatus.COMPLETED.value])
    total_activities = len(activities)
    blocked_activities = [activity for activity in activities if getattr(activity, "status", "") == ConsultantJourneyActivityStatus.BLOCKED.value]
    next_activity = next(
        (
            activity
            for activity in activities
            if getattr(activity, "status", "") not in {ConsultantJourneyActivityStatus.COMPLETED.value, ConsultantJourneyActivityStatus.SKIPPED.value}
        ),
        None,
    )
    readiness_score = getattr(journey, "readiness_score", None)
    if readiness_score is None:
        readiness_score = getattr(consultant, "marketing_readiness_percent", 0)
    stage_labels = dict(_role_journey_stage_options())
    lifecycle = [
        {
            "key": "profile",
            "label": "Profile Intake",
            "owner": owner or "Staff owner required",
            "outcome": "Consultant identity, location, authorization, skills, experience, role, domain, availability, and rate are captured.",
            "evidence": "Consultant profile, authorization notes, base resume, role/domain selection",
            "done": bool(getattr(consultant, "profile_intake_complete", False) and owner),
        },
        {
            "key": "basics",
            "label": "Basics Preparation",
            "owner": owner or "Assigned staff",
            "outcome": "Consultant understands core cloud, DevOps, agile, command, evidence, and interview basics before role training.",
            "evidence": "Basics completion package, diagrams, command outputs, final staff check",
            "done": bool(getattr(consultant, "basics_prep_complete", False) or getattr(consultant, "training_plan_assigned", False)),
        },
        {
            "key": "role_training",
            "label": "Role / Domain Training",
            "owner": getattr(getattr(journey, "assigned_trainer", None), "email", "") or owner or "Trainer or staff owner",
            "outcome": f"Consultant studies the assigned {role_name} / {domain} program and builds 10-12 credible project use cases.",
            "evidence": "Assigned training program, completed use cases, role glossary, diagrams, project notes",
            "done": bool(getattr(consultant, "training_plan_assigned", False) and getattr(consultant, "project_story_validated", False)),
        },
        {
            "key": "resume",
            "label": "Resume And Project Positioning",
            "owner": owner or "Staff owner",
            "outcome": "Resume, project summary, ownership boundary, evidence, and interview story are aligned to the target role.",
            "evidence": "Resume version, project story validation, positioning brief, final evidence package",
            "done": bool(getattr(consultant, "resume_tailoring_complete", False) and getattr(consultant, "project_story_validated", False)),
        },
        {
            "key": "mock",
            "label": "Mock Interview Readiness",
            "owner": getattr(getattr(journey, "assigned_trainer", None), "email", "") or owner or "Mock interviewer",
            "outcome": "Consultant can explain project context, use cases, failures, architecture, evidence, and role boundaries under interview pressure.",
            "evidence": "Mock interview record, feedback, score, improvement notes",
            "done": bool(getattr(consultant, "mock_interview_passed", False)),
        },
        {
            "key": "marketing",
            "label": "Marketing Ready Approval",
            "owner": owner or "Staff owner required",
            "outcome": "Assigned owner approves readiness before submissions, preventing unowned or underprepared marketing.",
            "evidence": "Marketing-ready gate, final evidence package, recruiter handoff summary",
            "done": bool(marketing_ready_context(consultant)["ready"]),
        },
        {
            "key": "submission",
            "label": "Submissions And Interview Pipeline",
            "owner": recruiter or owner or "Recruiter and staff owner",
            "outcome": "Jobs are matched, resume is tailored, submissions are reviewed, feedback is tracked, and gaps recycle into training.",
            "evidence": "Job match, campaign, submission, interview feedback, next action",
            "done": bool(getattr(consultant, "submissions", []) or journey_stage in {"submission_pipeline", "interview_pipeline"}),
        },
        {
            "key": "offer",
            "label": "Offer And Joining Plan",
            "owner": recruiter or owner or "Recruiter and staff owner",
            "outcome": "Offer, start date, client expectations, compensation/rate notes, and joining risks are reviewed before placement is closed.",
            "evidence": "Offer notes, start date, recruiter/client confirmation, joining checklist",
            "done": journey_stage in {"placement", "post_placement"} or journey_status in {"placed", "post_placement", "completed"} or bool(getattr(consultant, "placement_company", "")),
        },
        {
            "key": "placement",
            "label": "Placement And Post-Placement Support",
            "owner": recruiter or owner or "Recruiter and staff owner",
            "outcome": "Placement details, first-week support, communication rhythm, escalation path, and feedback loop are tracked.",
            "evidence": "Placement company, role, start date, post-placement notes, support check-ins",
            "done": journey_status in {"completed", "post_placement"} or bool(getattr(consultant, "placement_company", "")),
        },
    ]
    current_index = next((index for index, item in enumerate(lifecycle) if not item["done"]), len(lifecycle) - 1)
    for index, item in enumerate(lifecycle):
        item["state"] = "completed" if item["done"] else "current" if index == current_index else "pending"
    staff_responsibilities = [
        {"stage": "Onboarding", "staff": owner or "Unassigned", "responsibility": "Own intake, profile completeness, authorization clarity, role/domain selection, and first training assignment."},
        {"stage": "Training", "staff": owner or "Unassigned", "responsibility": "Track basics, role/domain program, use-case completion, evidence quality, and weekly blockers."},
        {"stage": "Readiness", "staff": owner or "Unassigned", "responsibility": "Approve resume, project story, mock interview outcome, final evidence package, and marketing-ready gate."},
        {"stage": "Marketing", "staff": recruiter or owner or "Unassigned", "responsibility": "Select target jobs, tailor resume, create submissions, track interviews, and return feedback to the staff owner."},
        {"stage": "Offer", "staff": recruiter or owner or "Unassigned", "responsibility": "Track offer details, joining plan, start date, client expectation, and acceptance risks."},
        {"stage": "Placement", "staff": recruiter or owner or "Unassigned", "responsibility": "Record placement, support first-week onboarding, collect feedback, and close the lifecycle with placement notes."},
    ]
    training_path = [
        {"label": "2 weeks", "title": "Basics Preparation", "detail": "Cloud, DevOps, agile, commands, diagrams, evidence, and interview foundation."},
        {"label": "4 weeks", "title": "Role And Domain Training", "detail": f"{role_name} / {domain} use cases, architecture, project story, and evidence package."},
        {"label": "Weekly", "title": "Mock / Staff Review", "detail": "Mock interviews, resume review, use-case correction, and readiness scoring."},
        {"label": "Ongoing", "title": "Job Matching And Submission", "detail": "Active JD matching, company-specific resume tailoring, submissions, interviews, and feedback loop."},
        {"label": "Placement", "title": "Offer, Start, And Support", "detail": "Offer review, joining plan, first-week support, escalation guidance, and post-placement feedback."},
    ]
    return {
        "owner_label": owner or "Unassigned staff owner",
        "recruiter_label": recruiter or "Recruiter not assigned",
        "role_label": role_name,
        "domain_label": domain,
        "stage_label": stage_labels.get(journey_stage, journey_stage.replace("_", " ").title()),
        "status_label": journey_status.replace("_", " ").title(),
        "readiness_score": readiness_score,
        "completed_activities": completed_activities,
        "total_activities": total_activities,
        "blocked_count": len(blocked_activities),
        "next_action": getattr(journey, "next_action", "") or (getattr(next_activity, "title", "") if next_activity else "Keep profile, training, submissions, and interview feedback current."),
        "assigned_program": getattr(program, "title", "") or "No role/domain training program assigned",
        "assigned_program_domain": getattr(program, "industry_domain", "") or domain,
        "evidence_package": evidence_package,
        "unlock_plan": consultant_access_gate_plan(consultant),
        "lifecycle": lifecycle,
        "staff_responsibilities": staff_responsibilities,
        "training_path": training_path,
        "links": {},
    }


def _consultant_training_program(db: Session, consultant: ConsultantProfile) -> TrainingProgram | None:
    if not consultant.marketing_role_id:
        return None
    domain = (consultant.target_industry_domain or "").strip().lower()
    if domain:
        program = db.scalar(
            select(TrainingProgram).where(
                TrainingProgram.active.is_(True),
                TrainingProgram.marketing_role_id == consultant.marketing_role_id,
                func.lower(TrainingProgram.industry_domain) == domain,
            )
        )
        if program:
            return program
    return db.scalar(
        select(TrainingProgram)
        .where(TrainingProgram.active.is_(True), TrainingProgram.marketing_role_id == consultant.marketing_role_id)
        .order_by(TrainingProgram.display_order, TrainingProgram.title)
    )


def _consultant_activation_error(db: Session, consultant: ConsultantProfile) -> str:
    if not getattr(consultant, "active", False):
        return ""
    if not (consultant.staff_owner or "").strip():
        return "Active consultants must have an assigned staff owner."
    if not consultant.marketing_role_id:
        return "Active consultants must be locked to one marketing role before access is enabled."
    if not (consultant.target_industry_domain or "").strip():
        return "Active consultants must be locked to one target industry domain before access is enabled."
    if consultant.basics_prep_complete and not _consultant_training_program(db, consultant):
        return "Basics completion requires an active training program matching the consultant's marketing role and domain."
    return ""


def _sync_basics_to_training_assignment(db: Session, consultant: ConsultantProfile, completed_by: User | None = None) -> TrainingProgram | None:
    if not getattr(consultant, "basics_prep_complete", False):
        return None
    program = _consultant_training_program(db, consultant)
    if not program:
        return None
    consultant.training_plan_assigned = True
    journey = _ensure_consultant_role_journey(db, consultant)
    journey.training_program_id = program.id
    journey.current_stage = "training_plan"
    journey.next_action = f"Start {program.title or program.industry_domain} role/domain training."
    db.add(journey)
    activities = db.scalars(select(ConsultantJourneyActivity).where(ConsultantJourneyActivity.journey_id == journey.id, ConsultantJourneyActivity.key.in_(["complete_basics", "assign_training"]))).all()
    for activity in activities:
        activity.status = ConsultantJourneyActivityStatus.COMPLETED.value
        activity.completed_at = activity.completed_at or datetime.now(timezone.utc)
        activity.completed_by_id = getattr(completed_by, "id", None) or activity.completed_by_id
        if activity.key == "assign_training" and not activity.notes.strip():
            activity.notes = f"Assigned training program: {program.title or program.industry_domain}."
        db.add(activity)
    _refresh_consultant_role_journey(db, consultant, journey)
    db.add(consultant)
    return program


def _positioning_resume_headline(consultant: ConsultantProfile, role_name: str, skills: list[str]) -> str:
    parts = [role_name]
    if consultant.years_experience:
        parts.append(f"{consultant.years_experience} experience")
    if consultant.target_industry_domain:
        parts.append(consultant.target_industry_domain)
    if skills:
        parts.append(", ".join(skills[:4]))
    return " | ".join(parts)


def _positioning_role_pitch(consultant: ConsultantProfile, role_name: str, skills: list[str]) -> str:
    stack = ", ".join(skills[:5]) or "the target stack"
    domain = consultant.target_industry_domain or consultant.latest_project_domain or "enterprise technology"
    auth = consultant.work_authorization or "work authorization to be confirmed"
    return f"Position {consultant.name or consultant.email} as a {role_name} candidate for {domain} teams, emphasizing {stack}, production ownership, support evidence, and {auth}."


def _positioning_resume_bullets(consultant: ConsultantProfile, role_name: str, skills: list[str], matches: list[dict[str, Any]]) -> list[str]:
    stack = ", ".join(skills[:5]) or "role-specific tools"
    companies = ", ".join(row["company_name"] for row in matches[:3])
    project = consultant.latest_project_title or consultant.latest_project_domain or consultant.target_industry_domain or "enterprise platform work"
    bullets = [
        f"Delivered {role_name} work around {project}, with clear ownership boundaries, validation evidence, and support handoff.",
        f"Used {stack} to improve release readiness, troubleshooting, automation, monitoring, or platform operations.",
        "Translated production issues into evidence-based action: signal reviewed, owner identified, fix validated, and runbook or interview story updated.",
    ]
    if companies:
        bullets.append(f"Prepared company-specific positioning against hiring signals from {companies}.")
    if consultant.work_authorization:
        bullets.append(f"Kept work authorization positioning clear for OPT/H1B-friendly employer conversations: {consultant.work_authorization}.")
    return bullets


def _positioning_company_talking_points(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points = []
    for row in matches[:5]:
        points.append(
            {
                "company_name": row["company_name"],
                "role": row["best_roles_label"],
                "match_score": row["match_score"],
                "stack": row["skill_hits_label"],
                "talk_track": f"Lead with {row['consultant_role']} fit, then connect the resume story to {row['best_roles_label']} and the company's USCIS/job evidence.",
                "url": f"/pursuits/{row['pursuit_id']}?tab=job-postings" if row.get("pursuit_id") else f"/companies/{row['company_id']}/uscis",
            }
        )
    return points


def _positioning_interview_story_flow(consultant: ConsultantProfile, role_name: str) -> list[str]:
    project = consultant.latest_project_title or "the latest project"
    return [
        f"Open with the business or platform problem behind {project}.",
        f"State exactly what was owned as a {role_name}, and what stayed with product, application, data, security, or infrastructure teams.",
        "Name the tools, evidence, logs, metrics, tickets, dashboards, deployment records, or validation steps used.",
        "Explain one failure or risk, the troubleshooting path, and the final measurable outcome.",
        "Close by mapping that story to the target company's current posting and interview needs.",
    ]


def _positioning_gap_plan(gaps: list[str], matches: list[dict[str, Any]]) -> list[str]:
    plan = []
    for gap in gaps[:5]:
        if gap == "training plan missing":
            plan.append("Assign the role/domain training program and pick two project use cases to complete first.")
        elif gap == "project story not validated":
            plan.append("Validate one end-to-end project story with ownership boundary, incident example, and outcome.")
        elif gap == "resume tailoring incomplete":
            plan.append("Tailor resume headline, summary, skills, and latest project bullets for the target role.")
        elif gap == "mock interview not passed":
            plan.append("Run a role-specific mock interview using company posting language from the top matches.")
        elif gap == "no targeting campaign":
            plan.append("Create a targeting campaign from Candidate Company Matches.")
        elif gap == "no submissions yet":
            plan.append("Convert top campaign targets into reviewed submissions after resume tailoring.")
        else:
            plan.append(gap.replace("_", " ").capitalize())
    if not plan and matches:
        plan.append("Use the top company matches to tailor resume, create submissions, and prepare posting-specific interview stories.")
    if not plan:
        plan.append("Complete consultant intake so Mintel can generate a role-specific positioning plan.")
    return plan


_JOURNEY_LEGACY_FLAG_BY_ACTIVITY = {
    "profile_intake": "profile_intake_complete",
    "verify_education": "education_verified",
    "verify_certifications": "certifications_verified",
    "verify_experience": "experience_verified",
    "receive_base_resume": "base_resume_received",
    "complete_basics": "basics_prep_complete",
    "assign_training": "training_plan_assigned",
    "complete_glossary": "glossary_review_complete",
    "update_project": "latest_project_updated",
    "validate_project_story": "project_story_validated",
    "tailor_resume": "resume_tailoring_complete",
    "pass_mock": "mock_interview_passed",
    "approve_positioning": "marketing_brief_ready",
}


def _final_evidence_package_items() -> list[dict[str, str]]:
    return [
        {
            "key": "evidence_architecture_diagram",
            "title": "Architecture diagram",
            "description": "Attach a role-specific architecture diagram showing systems, services, data flow, integrations, ownership boundary, and where the consultant contributed.",
            "student_title": "Create architecture diagram",
            "student_description": "Show the system pieces, how they connect, and exactly what your role owned. Keep it interview-friendly.",
        },
        {
            "key": "evidence_workflow_diagram",
            "title": "Workflow diagram",
            "description": "Attach the delivery, deployment, incident, data, or support workflow diagram that explains the project step by step.",
            "student_title": "Create workflow diagram",
            "student_description": "Show the practical flow: request, build, deploy, monitor, incident, handoff, or data processing steps.",
        },
        {
            "key": "evidence_use_case_boundary",
            "title": "Use-case boundary sheet",
            "description": "Document what the consultant owned, what other teams owned, business context, systems touched, and claims to avoid.",
            "student_title": "Write use-case boundary sheet",
            "student_description": "I owned this part, supported this part, other teams owned these areas, and I kept the boundary clear.",
        },
        {
            "key": "evidence_tool_config_notes",
            "title": "Tool configuration notes",
            "description": "Capture configuration notes for relevant tools such as cloud services, CI/CD, observability, IaC, data, MLOps, security, or ticketing systems.",
            "student_title": "Add tool configuration notes",
            "student_description": "List the important tools, what each one was configured to do, and what output or evidence it produced.",
        },
        {
            "key": "evidence_screenshots_outputs",
            "title": "Screenshots or command outputs",
            "description": "Attach safe screenshots, terminal outputs, deployment records, dashboard snippets, logs, test results, or validation output without exposing secrets.",
            "student_title": "Attach screenshots or command outputs",
            "student_description": "Share proof that work was done: screenshots, command outputs, dashboards, logs, test results, or deployment records. Remove secrets.",
        },
        {
            "key": "evidence_runbook_incident",
            "title": "Runbook and incident simulation",
            "description": "Create a runbook plus one incident simulation with symptom, signals, triage path, escalation, recovery, validation, and prevention.",
            "student_title": "Prepare runbook and incident simulation",
            "student_description": "Write one realistic incident story: symptom, evidence, diagnosis, fix, validation, and prevention.",
        },
        {
            "key": "evidence_interview_story_bank",
            "title": "Interview story bank",
            "description": "Prepare short and long interview stories for architecture, troubleshooting, delivery, role ownership, tool usage, team handoff, and business impact.",
            "student_title": "Build interview story bank",
            "student_description": "Prepare stories you can tell in 60 seconds and 5 minutes for project, issue, tool, architecture, and teamwork questions.",
        },
        {
            "key": "evidence_resume_project_summary",
            "title": "Resume bullets and project summary",
            "description": "Finalize resume bullets, project summary, metrics, role keywords, and recruiter-facing summary from the approved evidence package.",
            "student_title": "Finalize resume bullets and project summary",
            "student_description": "Turn the evidence package into resume bullets and a project summary that match your target Mintel role.",
        },
    ]


def _journey_activity_blueprint() -> list[dict[str, str | int]]:
    base = [
        {"sequence": 10, "key": "assign_role", "stage": "role_intake", "title": "Assign Mintel role", "description": "Pick the single best Mintel marketing role for this consultant and record why."},
        {"sequence": 20, "key": "profile_intake", "stage": "profile_intake", "title": "Complete profile intake", "description": "Capture contact, location, availability, rate, education, authorization, and experience context."},
        {"sequence": 30, "key": "verify_work_auth", "stage": "profile_intake", "title": "Verify OPT/H1B status", "description": "Confirm work authorization, EAD/visa dates, sponsorship constraints, and location flexibility."},
        {"sequence": 40, "key": "verify_education", "stage": "profile_intake", "title": "Verify education", "description": "Confirm degree, school, graduation date, and documents needed for marketing or client review."},
        {"sequence": 50, "key": "verify_certifications", "stage": "profile_intake", "title": "Verify certifications", "description": "Capture certifications and decide which ones should be emphasized for the target role."},
        {"sequence": 60, "key": "verify_experience", "stage": "profile_intake", "title": "Verify experience timeline", "description": "Confirm employment/project timeline, domains, responsibilities, and any explainable gaps."},
        {"sequence": 70, "key": "receive_base_resume", "stage": "positioning", "title": "Receive base resume", "description": "Attach or reference the base resume before tailoring starts."},
        {"sequence": 75, "key": "complete_basics", "stage": "training_plan", "title": "Complete Basics Preparation", "description": "Complete the basics foundation before the role/domain training program is assigned."},
        {"sequence": 80, "key": "assign_training", "stage": "training_plan", "title": "Assign role/domain training", "description": "Assign a training program that matches the target role and domain direction after Basics is completed."},
        {"sequence": 90, "key": "identify_skill_gaps", "stage": "training_plan", "title": "Identify skill gaps", "description": "Convert missing role skills into training tasks, labs, and interview checkpoints."},
        {"sequence": 100, "key": "complete_glossary", "stage": "training_plan", "title": "Complete role glossary", "description": "Review role vocabulary, product terms, and ownership boundaries."},
        {"sequence": 110, "key": "complete_labs", "stage": "training_plan", "title": "Complete role labs/projects", "description": "Complete role-specific labs or project work that can be explained in interviews."},
        {"sequence": 120, "key": "update_project", "stage": "project_story", "title": "Update latest project", "description": "Create or refresh the latest project with role-specific deliverables and evidence."},
        {"sequence": 130, "key": "validate_project_story", "stage": "project_story", "title": "Validate project story", "description": "Validate the story with problem, ownership, tools, evidence, issue, and result."},
        {"sequence": 140, "key": "tailor_resume", "stage": "positioning", "title": "Tailor resume for role", "description": "Update headline, summary, skills, project bullets, and ATS keywords for the role."},
        {"sequence": 150, "key": "approve_positioning", "stage": "positioning", "title": "Approve positioning brief", "description": "Approve the staff/recruiter handoff: pitch, gaps, resume direction, and interview story flow."},
        {"sequence": 160, "key": "pass_mock", "stage": "interview_readiness", "title": "Pass role mock interview", "description": "Run and pass a role-specific mock interview before heavy marketing."},
    ]
    evidence_items = [
        {
            "sequence": 170 + index,
            "key": item["key"],
            "stage": "final_evidence",
            "title": item["title"],
            "description": item["description"],
        }
        for index, item in enumerate(_final_evidence_package_items())
    ]
    market_items = [
        {"sequence": 190, "key": "match_companies", "stage": "company_matching", "title": "Match target companies", "description": "Use Candidate Company Matches to identify companies worth targeting first."},
        {"sequence": 200, "key": "create_campaign", "stage": "campaign_active", "title": "Create targeting campaign", "description": "Create a working campaign with companies, next actions, and submission strategy."},
        {"sequence": 210, "key": "company_resume_tailor", "stage": "campaign_active", "title": "Tailor for top companies", "description": "Adjust resume and pitch for the top company/job signals in the campaign."},
        {"sequence": 220, "key": "create_submissions", "stage": "submission_pipeline", "title": "Create reviewed submissions", "description": "Submit only after official job evidence, resume fit, and role pitch are aligned."},
        {"sequence": 230, "key": "track_feedback", "stage": "interview_pipeline", "title": "Track interview/submission feedback", "description": "Update gaps, resume, training, and company notes from every outcome."},
        {"sequence": 240, "key": "offer_review", "stage": "offer", "title": "Review offer and joining plan", "description": "Capture offer details, start date, onboarding needs, client expectations, and any remaining risk."},
        {"sequence": 250, "key": "confirm_placement", "stage": "placement", "title": "Confirm placement", "description": "Record placement company, role, start date, recruiter/client handoff, and final placement notes."},
        {"sequence": 260, "key": "post_placement_support", "stage": "post_placement", "title": "Post-placement support", "description": "Track first-week support, first sprint expectations, communication rhythm, escalation path, and feedback loop."},
    ]
    return [*base, *evidence_items, *market_items]


def _ensure_consultant_role_journey(db: Session, consultant: ConsultantProfile) -> ConsultantRoleJourney:
    journey = db.scalar(
        select(ConsultantRoleJourney)
        .where(ConsultantRoleJourney.consultant_id == consultant.id, ConsultantRoleJourney.status != ConsultantJourneyStatus.ARCHIVED.value)
        .order_by(ConsultantRoleJourney.updated_at.desc())
    )
    if not journey:
        journey = ConsultantRoleJourney(
            consultant_id=consultant.id,
            marketing_role_id=consultant.marketing_role_id,
            target_domain=consultant.target_industry_domain or consultant.latest_project_domain or "",
            status=ConsultantJourneyStatus.ACTIVE.value,
        )
        db.add(journey)
        db.flush()
    _ensure_journey_activities(db, consultant, journey)
    _refresh_consultant_role_journey(db, consultant, journey)
    return journey


def _ensure_journey_activities(db: Session, consultant: ConsultantProfile, journey: ConsultantRoleJourney) -> None:
    existing = {activity.key: activity for activity in db.scalars(select(ConsultantJourneyActivity).where(ConsultantJourneyActivity.journey_id == journey.id)).all()}
    for item in _journey_activity_blueprint():
        key = str(item["key"])
        if key in existing:
            _sync_activity_completion_from_consultant(consultant, existing[key])
            continue
        status = _initial_activity_status(consultant, key)
        activity = ConsultantJourneyActivity(
            journey_id=journey.id,
            sequence=int(item["sequence"]),
            key=key,
            stage=str(item["stage"]),
            title=str(item["title"]),
            description=str(item["description"]),
            status=status,
            completed_at=datetime.now(timezone.utc) if status == ConsultantJourneyActivityStatus.COMPLETED.value else None,
        )
        db.add(activity)


def _sync_activity_completion_from_consultant(consultant: ConsultantProfile, activity: ConsultantJourneyActivity) -> None:
    if activity.status == ConsultantJourneyActivityStatus.COMPLETED.value:
        return
    if _initial_activity_status(consultant, activity.key) == ConsultantJourneyActivityStatus.COMPLETED.value:
        activity.status = ConsultantJourneyActivityStatus.COMPLETED.value
        activity.completed_at = activity.completed_at or datetime.now(timezone.utc)


def _initial_activity_status(consultant: ConsultantProfile, key: str) -> str:
    if key == "assign_role" and consultant.marketing_role_id:
        return ConsultantJourneyActivityStatus.COMPLETED.value
    if key == "verify_work_auth" and (consultant.work_authorization or "").strip():
        return ConsultantJourneyActivityStatus.COMPLETED.value
    if key == "offer_review" and consultant.marketing_status in {"offer", "placed", "post_placement"}:
        return ConsultantJourneyActivityStatus.COMPLETED.value
    if key == "confirm_placement" and (consultant.placement_company or consultant.marketing_status in {"placed", "post_placement"}):
        return ConsultantJourneyActivityStatus.COMPLETED.value
    if key == "post_placement_support" and consultant.marketing_status == "post_placement":
        return ConsultantJourneyActivityStatus.COMPLETED.value
    flag = _JOURNEY_LEGACY_FLAG_BY_ACTIVITY.get(key)
    if flag and bool(getattr(consultant, flag, False)):
        return ConsultantJourneyActivityStatus.COMPLETED.value
    return ConsultantJourneyActivityStatus.TODO.value


def _refresh_consultant_role_journey(db: Session, consultant: ConsultantProfile, journey: ConsultantRoleJourney, preserve_manual: bool = False) -> None:
    activities = db.scalars(select(ConsultantJourneyActivity).where(ConsultantJourneyActivity.journey_id == journey.id).order_by(ConsultantJourneyActivity.sequence)).all()
    incomplete = [activity for activity in activities if activity.status not in {ConsultantJourneyActivityStatus.COMPLETED.value, ConsultantJourneyActivityStatus.SKIPPED.value}]
    completed_count = len([activity for activity in activities if activity.status == ConsultantJourneyActivityStatus.COMPLETED.value])
    total_count = len(activities)
    journey.marketing_role_id = consultant.marketing_role_id
    if not journey.training_program_id:
        program = _consultant_training_program(db, consultant)
        journey.training_program_id = program.id if program else None
    journey.target_domain = consultant.target_industry_domain or consultant.latest_project_domain or journey.target_domain or ""
    journey.readiness_score = round(completed_count / total_count * 100) if total_count else 0
    if incomplete:
        journey.current_stage = incomplete[0].stage
        derived_next_action = incomplete[0].title
    else:
        journey.current_stage = "interview_pipeline"
        derived_next_action = "Keep tracking interview/submission feedback and recycle gaps into training."
        if journey.status == ConsultantJourneyStatus.ACTIVE.value:
            journey.status = ConsultantJourneyStatus.COMPLETED.value
    blockers = [activity.title for activity in incomplete[:4] if activity.status == ConsultantJourneyActivityStatus.BLOCKED.value]
    if not blockers:
        blockers = [activity.title for activity in incomplete[:4]]
    journey.blocker_summary = ", ".join(blockers)
    if not preserve_manual or not journey.next_action.strip():
        journey.next_action = derived_next_action
    if not journey.positioning_summary.strip():
        journey.positioning_summary = _role_positioning_brief(consultant, _consultant_training_program(db, consultant), blockers).get("pitch", "")
    db.add(journey)


def _consultant_journey_context(db: Session, consultant: ConsultantProfile, journey: ConsultantRoleJourney) -> dict[str, Any]:
    activities = db.scalars(select(ConsultantJourneyActivity).where(ConsultantJourneyActivity.journey_id == journey.id).order_by(ConsultantJourneyActivity.sequence)).all()
    completed = len([activity for activity in activities if activity.status == ConsultantJourneyActivityStatus.COMPLETED.value])
    blocked = len([activity for activity in activities if activity.status == ConsultantJourneyActivityStatus.BLOCKED.value])
    grouped: list[dict[str, Any]] = []
    for stage_value, stage_label in _role_journey_stage_options():
        if stage_value == "all":
            continue
        stage_items = [activity for activity in activities if activity.stage == stage_value]
        if stage_items:
            grouped.append({"stage": stage_value, "label": stage_label, "activities": stage_items})
    return {
        "activities": activities,
        "grouped_activities": grouped,
        "completed_activities": completed,
        "blocked_activities": blocked,
        "total_activities": len(activities),
        "activity_status_options": _journey_activity_status_options(),
        "journey_status_options": _journey_status_options(),
        "stage_options": [item for item in _role_journey_stage_options() if item[0] != "all"],
        "evidence_package": _final_evidence_package_context(db, consultant.id, activities),
        "positioning_url": f"/consultants/{consultant.id}/positioning",
        "matches_url": f"/reports/candidate-company-matches?consultant_id={consultant.id}&min_match_score=35",
    }


def _consultant_onboarding_context(db: Session, consultant: ConsultantProfile, journey: ConsultantRoleJourney) -> dict[str, Any]:
    activities = db.scalars(select(ConsultantJourneyActivity).where(ConsultantJourneyActivity.journey_id == journey.id).order_by(ConsultantJourneyActivity.sequence)).all()
    visible_activities = [_student_activity_view(activity) for activity in activities]
    active_items = [
        item
        for item in visible_activities
        if item["status"] not in {ConsultantJourneyActivityStatus.COMPLETED.value, ConsultantJourneyActivityStatus.SKIPPED.value}
    ]
    blockers = [item for item in visible_activities if item["status"] == ConsultantJourneyActivityStatus.BLOCKED.value]
    completed_count = len([item for item in visible_activities if item["status"] == ConsultantJourneyActivityStatus.COMPLETED.value])
    program = _consultant_training_program(db, consultant)
    matches = _candidate_company_matches_report(
        db,
        fiscal_year=max(_available_uscis_years(db) or [0]) or None,
        consultant_id=consultant.id,
        marketing_role_id=consultant.marketing_role_id,
        region_id=None,
        source="all",
        min_approvals=5,
        min_approval_rate=75,
        min_match_score=35,
    )["rows"][:5]
    stage_label = dict(_role_journey_stage_options()).get(journey.current_stage, journey.current_stage.replace("_", " ").title())
    role_name = consultant.marketing_role.name if consultant.marketing_role else "your Mintel role"
    return {
        "student_activities": visible_activities,
        "next_tasks": active_items[:3],
        "focus_task": active_items[0] if active_items else None,
        "student_blockers": blockers[:4],
        "completed_count": completed_count,
        "activity_count": len(visible_activities),
        "stage_label": stage_label,
        "training_program": program,
        "company_matches": matches,
        "welcome_message": _student_welcome_message(consultant, journey, active_items),
        "weekly_focus": _student_weekly_focus(journey.current_stage, active_items),
        "quick_wins": _student_quick_wins(visible_activities, consultant),
        "prep_cards": _student_prep_cards(consultant, journey, program, matches),
        "confidence_message": _student_confidence_message(journey, completed_count, len(visible_activities)),
        "role_fit_message": _student_role_fit_message(consultant, role_name, program),
        "market_readiness_message": _student_market_readiness_message(journey, active_items),
        "timeline_steps": _student_timeline_steps(journey.current_stage),
        "evidence_package": _final_evidence_package_context(db, consultant.id, activities),
        "staff_guidance": journey.next_action or (active_items[0]["student_title"] if active_items else "Keep your profile and interview stories current."),
        "journey_url": f"/consultants/{consultant.id}/journey",
        "positioning_url": f"/consultants/{consultant.id}/positioning",
    }


def _consultant_onboarding_questionnaire_context(consultant: ConsultantProfile | None = None) -> dict[str, Any]:
    role_name = consultant.marketing_role.name if consultant and consultant.marketing_role else ""
    value_by_key = {
        "legal_name": getattr(consultant, "name", "") or "",
        "preferred_name": getattr(consultant, "preferred_name", "") or "",
        "email": getattr(consultant, "email", "") or "",
        "phone": getattr(consultant, "phone", "") or "",
        "linkedin_url": getattr(consultant, "linkedin_url", "") or "",
        "current_location": getattr(consultant, "current_location", "") or "",
        "work_authorization": getattr(consultant, "work_authorization", "") or "",
        "visa_valid_until": getattr(consultant, "visa_valid_until", "") or "",
        "ead_valid_until": getattr(consultant, "ead_valid_until", "") or "",
        "relocation_preference": getattr(consultant, "relocation_preference", "") or "",
        "onsite_preference": getattr(consultant, "onsite_preference", "") or "",
        "availability": getattr(consultant, "availability", "") or "",
        "rate_expectation": getattr(consultant, "rate_expectation", "") or "",
        "years_experience": getattr(consultant, "years_experience", "") or "",
        "education_summary": getattr(consultant, "education_summary", "") or "",
        "certifications": getattr(consultant, "certifications", "") or "",
        "professional_experience": getattr(consultant, "professional_experience", "") or "",
        "domain_experience": getattr(consultant, "domain_experience", "") or "",
        "primary_skills": getattr(consultant, "primary_skills", "") or "",
        "marketing_role": role_name,
        "target_industry_domain": getattr(consultant, "target_industry_domain", "") or "",
        "latest_project_title": getattr(consultant, "latest_project_title", "") or "",
        "latest_project_domain": getattr(consultant, "latest_project_domain", "") or "",
        "latest_project_summary": getattr(consultant, "latest_project_summary", "") or "",
        "resume_summary": getattr(consultant, "resume_summary", "") or "",
        "profile_strengths": getattr(consultant, "profile_strengths", "") or "",
        "profile_gaps": getattr(consultant, "profile_gaps", "") or "",
        "base_resume_reference": getattr(consultant, "base_resume_reference", "") or "",
        "staff_owner": getattr(consultant, "staff_owner", "") or "",
        "recruiter_owner": getattr(consultant, "recruiter_owner", "") or "",
        "maas_profile_id": getattr(consultant, "maas_profile_id", "") or "",
    }
    sections = [
        {
            "title": "1. Identity And Contact",
            "purpose": "Confirms who the consultant is and how staff should reach them during onboarding and marketing.",
            "questions": [
                ("legal_name", "Legal name", "consultant.legal_name", "Short text"),
                ("preferred_name", "Preferred name", "consultant.preferred_name", "Short text"),
                ("email", "Primary email", "consultant.email", "Email"),
                ("phone", "Phone number", "consultant.phone", "Phone"),
                ("linkedin_url", "LinkedIn URL", "consultant.linkedin_url", "URL"),
                ("current_location", "Current city and state", "consultant.current_location", "Short text"),
            ],
        },
        {
            "title": "2. Work Authorization And Availability",
            "purpose": "Prevents wrong submissions by capturing authorization, timing, location preference, and rate expectations early.",
            "questions": [
                ("work_authorization", "Work authorization status", "consultant.work_authorization", "OPT, STEM OPT, H1B, GC, USC, other"),
                ("visa_valid_until", "Visa valid until or status notes", "consultant.visa_valid_until", "Date or notes"),
                ("ead_valid_until", "EAD valid until, if applicable", "consultant.ead_valid_until", "Date or notes"),
                ("relocation_preference", "Relocation preference", "consultant.relocation_preference", "States, cities, or remote only"),
                ("onsite_preference", "Work setup preference", "consultant.onsite_preference", "Remote, hybrid, onsite"),
                ("availability", "Availability to start", "consultant.availability", "Immediate, two weeks, date"),
                ("rate_expectation", "Rate or salary expectation", "consultant.rate_expectation", "Range and flexibility"),
            ],
        },
        {
            "title": "3. Education, Certifications, And Background",
            "purpose": "Captures degree timeline, certifications, and the learning baseline staff needs for profile and screening alignment.",
            "questions": [
                ("years_experience", "Total professional experience", "consultant.years_experience", "Years and months"),
                ("", "Undergraduate degree, university, start date, and end date", "future_mtas.education.undergrad", "Degree, university, MM/YYYY to MM/YYYY"),
                ("", "Master's degree, university, start date, and end date", "future_mtas.education.masters", "Degree, university, MM/YYYY to MM/YYYY"),
                ("education_summary", "Education summary", "consultant.education_summary", "Degree, university, graduation year, notes"),
                ("certifications", "Certifications completed or in progress", "consultant.certifications", "Cloud, data, security, agile, domain"),
                ("professional_experience", "Current professional experience", "consultant.professional_experience", "Company, title, duration, major work"),
                ("", "Previous experience before current role", "future_mtas.experience.previous", "Company, title, duration, responsibilities"),
                ("domain_experience", "Domain exposure", "consultant.domain_experience", "Healthcare, banking, insurance, logistics, retail, telecom"),
                ("", "What did you learn in the last 6 months?", "future_mtas.learning.last_6_months", "Tools, projects, certifications, courses, hands-on labs"),
            ],
        },
        {
            "title": "4. Marketing Role And Domain Lock",
            "purpose": "Locks the consultant into the correct training lane so Basics unlocks only the selected role/domain training program.",
            "questions": [
                ("marketing_role", "Target marketing role", "consultant.marketing_role_id", "Cloud Platform Engineer, SRE/AIOps, DevOps, Data Engineer, etc."),
                ("target_industry_domain", "Target industry domain", "consultant.target_industry_domain", "Healthcare, banking, insurance, logistics, retail, telecom"),
                ("primary_skills", "Primary skills to market", "consultant.primary_skills", "Tools, languages, platforms, frameworks"),
                ("profile_strengths", "Strongest selling points", "consultant.profile_strengths", "What staff should emphasize"),
                ("profile_gaps", "Known gaps or risk areas", "consultant.profile_gaps", "What training and mock interviews should improve"),
            ],
        },
        {
            "title": "5. Project Story Intake",
            "purpose": "Builds one believable project narrative that can support resume bullets, interview answers, and role/domain training.",
            "questions": [
                ("latest_project_title", "Latest project title", "consultant.latest_project_title", "Role-aligned project name"),
                ("latest_project_domain", "Latest project domain", "consultant.latest_project_domain", "Business domain and line of business"),
                ("latest_project_summary", "Project summary", "consultant.latest_project_summary", "Systems, users, scale, responsibilities, outcomes"),
                ("", "Product or platform systems involved", "future_mtas.project.systems", "Applications, workflows, integrations, cloud services"),
                ("", "Use cases the consultant can explain", "future_mtas.project.usecases", "8 to 12 implemented or supported use cases"),
                ("", "Evidence available", "future_mtas.project.evidence", "Architecture, workflows, runbooks, logs, dashboards, release notes"),
            ],
        },
        {
            "title": "6. Resume, Recent Learning, And Documents",
            "purpose": "Collects consultant-provided resume material and supporting documents before staff starts Mintel review.",
            "questions": [
                ("base_resume_reference", "Base resume reference or upload note", "consultant.base_resume_reference", "File name, Drive path, or MTAS source"),
                ("resume_summary", "Current resume summary", "consultant.resume_summary", "Current summary or desired direction"),
                ("", "Documents available", "future_mtas.documents.available", "Resume, certificates, portfolio links, GitHub, LinkedIn, status proof"),
                ("", "Portfolio, GitHub, or project links", "future_mtas.documents.portfolio_links", "URLs and short description"),
                ("", "Additional learning proof", "future_mtas.learning.proof", "Course, lab, certification, GitHub, notes"),
            ],
        },
    ]
    return {
        "consultant": consultant,
        "questionnaire_sections": sections,
        "value_by_key": value_by_key,
        "generated_on": date.today(),
    }


def _public_intake_education_summary(
    undergrad_degree: str,
    undergrad_university: str,
    undergrad_start_date: str,
    undergrad_end_date: str,
    masters_degree: str,
    masters_university: str,
    masters_start_date: str,
    masters_end_date: str,
) -> str:
    return _join_note_sections(
        ("Undergraduate", " | ".join(part for part in [undergrad_degree.strip(), undergrad_university.strip(), f"{undergrad_start_date.strip()} to {undergrad_end_date.strip()}".strip()] if part and part != "to")),
        ("Master's", " | ".join(part for part in [masters_degree.strip(), masters_university.strip(), f"{masters_start_date.strip()} to {masters_end_date.strip()}".strip()] if part and part != "to")),
    )


def _join_note_sections(*sections: tuple[str, str]) -> str:
    parts = []
    for title, value in sections:
        cleaned = (value or "").strip()
        if cleaned:
            parts.append(f"{title}: {cleaned}")
    return "\n\n".join(parts)


def _final_evidence_package_context(db: Session, consultant_id: int, activities: Optional[list[ConsultantJourneyActivity]] = None) -> dict[str, Any]:
    if activities is None:
        journey = db.scalar(
            select(ConsultantRoleJourney)
            .where(ConsultantRoleJourney.consultant_id == consultant_id, ConsultantRoleJourney.status != ConsultantJourneyStatus.ARCHIVED.value)
            .order_by(ConsultantRoleJourney.updated_at.desc())
        )
        activities = db.scalars(select(ConsultantJourneyActivity).where(ConsultantJourneyActivity.journey_id == journey.id).order_by(ConsultantJourneyActivity.sequence)).all() if journey else []
    activity_by_key = {activity.key: activity for activity in activities}
    rows = []
    completed = 0
    blocked = 0
    for item in _final_evidence_package_items():
        activity = activity_by_key.get(item["key"])
        status = activity.status if activity else ConsultantJourneyActivityStatus.TODO.value
        if status == ConsultantJourneyActivityStatus.COMPLETED.value:
            completed += 1
        if status == ConsultantJourneyActivityStatus.BLOCKED.value:
            blocked += 1
        rows.append(
            {
                **item,
                "activity": activity,
                "activity_id": activity.id if activity else None,
                "status": status,
                "status_label": status.replace("_", " ").title(),
                "evidence_url": activity.evidence_url if activity else "",
                "notes": activity.notes if activity else "",
                "due_date": activity.due_date if activity else None,
                "complete": status == ConsultantJourneyActivityStatus.COMPLETED.value,
                "blocked": status == ConsultantJourneyActivityStatus.BLOCKED.value,
            }
        )
    total = len(rows)
    return {
        "items": rows,
        "completed": completed,
        "blocked": blocked,
        "total": total,
        "percent": round(completed / total * 100) if total else 0,
        "ready": bool(total and completed == total),
    }


def _student_welcome_message(consultant: ConsultantProfile, journey: ConsultantRoleJourney, active_items: list[dict[str, Any]]) -> str:
    name = consultant.preferred_name or consultant.name or "there"
    if journey.readiness_score >= 85:
        return f"{name}, you are close to market-ready. The work now is sharpening your story and responding quickly when staff asks for company-specific material."
    if journey.readiness_score >= 55:
        return f"{name}, you have momentum. Stay focused on the next few items so staff can confidently market you for the right role."
    if active_items:
        return f"{name}, start here. Mintel will guide you one step at a time so your resume, project story, and interview answers all point to the same role."
    return f"{name}, your journey is being set up. Once staff assigns your first tasks, this page will show exactly what to do next."


def _student_weekly_focus(current_stage: str, active_items: list[dict[str, Any]]) -> dict[str, str]:
    focus_by_stage = {
        "role_intake": ("Choose the right lane", "Confirm the target role and make sure it matches your background and goals."),
        "profile_intake": ("Make your profile usable", "Clean information helps staff avoid wrong companies, wrong locations, and wrong work authorization conversations."),
        "training_plan": ("Build role confidence", "Focus on the tools and vocabulary you will need to explain in interviews."),
        "project_story": ("Create one strong story", "Your project story should prove what you owned, what changed, and how you handled issues."),
        "positioning": ("Make the resume match the role", "Resume, pitch, and project bullets should all tell the same role-specific story."),
        "interview_readiness": ("Practice out loud", "Mock interview practice turns knowledge into clear answers under pressure."),
        "final_evidence": ("Build your evidence package", "Create proof that your project story is real, explainable, and ready for resume, submissions, and interviews."),
        "company_matching": ("Understand target companies", "Know why staff is choosing these companies and what postings they are matching you against."),
        "campaign_active": ("Be responsive", "Company targeting moves faster when resume updates and interview prep happen quickly."),
        "submission_pipeline": ("Prepare for callbacks", "Every submission should have a matching project story and expected interview questions."),
        "interview_pipeline": ("Use feedback fast", "Turn each result into better answers, better resume wording, and sharper targeting."),
    }
    title, body = focus_by_stage.get(current_stage, focus_by_stage["profile_intake"])
    if active_items:
        body = f"{body} First action: {active_items[0]['student_title']}."
    return {"title": title, "body": body}


def _student_quick_wins(activities: list[dict[str, Any]], consultant: ConsultantProfile) -> list[str]:
    wins = []
    for activity in activities:
        if activity["status"] == ConsultantJourneyActivityStatus.COMPLETED.value:
            wins.append(activity["student_title"])
    if consultant.primary_skills.strip():
        wins.append("Skills captured")
    if consultant.work_authorization.strip():
        wins.append("Work authorization captured")
    if consultant.latest_project_title.strip() or consultant.latest_project_summary.strip():
        wins.append("Project story started")
    seen = []
    for item in wins:
        if item not in seen:
            seen.append(item)
    return seen[:6]


def _student_prep_cards(consultant: ConsultantProfile, journey: ConsultantRoleJourney, program: TrainingProgram | None, matches: list[dict[str, Any]]) -> list[dict[str, str]]:
    role_name = consultant.marketing_role.name if consultant.marketing_role else "your target role"
    skills = ", ".join(_split_training_items(consultant.primary_skills)[:4]) or "your core tools"
    company_hint = matches[0]["company_name"] if matches else "target companies"
    training_hint = program.title or program.industry_domain if program else "your assigned training"
    return [
        {
            "title": "Role Story",
            "body": f"Be ready to explain why {role_name} fits you, what you own in that role, and what stays outside your responsibility.",
        },
        {
            "title": "Skill Proof",
            "body": f"Prepare examples using {skills}. Do not just list tools; explain where you used them and what improved.",
        },
        {
            "title": "Training",
            "body": f"Use {training_hint} to build the project and vocabulary staff expects you to explain.",
        },
        {
            "title": "Company Match",
            "body": f"When staff targets {company_hint}, connect your resume and project story to the exact job evidence.",
        },
        {
            "title": "Evidence Package",
            "body": "Keep diagrams, screenshots, runbooks, story bank, and resume bullets aligned so every claim has proof.",
        },
    ]


def _student_confidence_message(journey: ConsultantRoleJourney, completed_count: int, total_count: int) -> str:
    if not total_count:
        return "Your checklist will appear as soon as staff starts your journey."
    if journey.readiness_score >= 85:
        return "Strong progress. You are entering the zone where quick response and interview prep matter most."
    if completed_count:
        return f"You have completed {completed_count} important step{'s' if completed_count != 1 else ''}. Keep the chain moving."
    return "No pressure to do everything at once. Finish the first task well, then move to the next."


def _student_activity_view(activity: ConsultantJourneyActivity) -> dict[str, Any]:
    evidence_labels = {item["key"]: (item["student_title"], item["student_description"]) for item in _final_evidence_package_items()}
    labels = {
        "assign_role": ("Understand your target role", "Review the role Mintel is positioning you for and ask questions if it does not match your background."),
        "profile_intake": ("Complete your profile", "Make sure your contact, location, education, authorization, experience, and availability are accurate."),
        "verify_work_auth": ("Confirm work authorization", "Share accurate OPT, STEM OPT, H1B, CPT, EAD, or visa details so staff can target the right companies."),
        "verify_education": ("Confirm education details", "Provide correct degree, university, dates, and any documents staff may need."),
        "verify_certifications": ("Share certifications", "Add certifications or planned certifications that support your target role."),
        "verify_experience": ("Validate your experience timeline", "Help staff understand your real project history, responsibilities, and explainable gaps."),
        "receive_base_resume": ("Provide your base resume", "Submit the latest resume so Mintel can tailor it for your target role."),
        "complete_basics": ("Complete Basics Preparation", "Finish the foundation topics before Mintel opens the role/domain training program."),
        "assign_training": ("Start assigned training", "Follow the role/domain training plan selected for you."),
        "identify_skill_gaps": ("Review skill gaps", "Understand which skills must be strengthened before marketing starts."),
        "complete_glossary": ("Learn role vocabulary", "Complete the glossary so you can explain tools, systems, and ownership clearly."),
        "complete_labs": ("Complete role projects", "Finish labs or project work that can become interview stories."),
        "update_project": ("Update project story", "Prepare one strong project with problem, tools, ownership, and outcome."),
        "validate_project_story": ("Practice project explanation", "Validate that you can explain your project clearly in an interview."),
        "tailor_resume": ("Review tailored resume", "Make sure the resume headline, skills, and project bullets match your target role."),
        "approve_positioning": ("Review marketing pitch", "Review how staff will position you to recruiters and companies."),
        "pass_mock": ("Pass mock interview", "Complete a role-specific mock interview before active marketing."),
        **evidence_labels,
        "match_companies": ("Review target companies", "Understand the companies Mintel is targeting and why they fit your profile."),
        "create_campaign": ("Start company targeting", "Staff creates a campaign to work matching companies for you."),
        "company_resume_tailor": ("Tailor for top companies", "Adjust resume and talk track for top company/job signals."),
        "create_submissions": ("Move into submissions", "Staff submits you only when role, resume, company, and job fit are aligned."),
        "track_feedback": ("Use feedback to improve", "Interview and submission feedback will update your training, resume, and next attempts."),
        "offer_review": ("Review offer and joining plan", "Confirm offer details, start date, onboarding needs, and expectations before joining."),
        "confirm_placement": ("Confirm placement", "Record the placement company, role, start date, and handoff notes."),
        "post_placement_support": ("Use post-placement support", "Use first-week support to understand expectations, communication, escalation, and feedback."),
    }
    student_title, student_description = labels.get(activity.key, (activity.title, activity.description))
    return {
        "id": activity.id,
        "key": activity.key,
        "sequence": activity.sequence,
        "stage": activity.stage,
        "student_title": student_title,
        "student_description": student_description,
        "staff_title": activity.title,
        "status": activity.status,
        "status_label": activity.status.replace("_", " ").title(),
        "due_date": activity.due_date,
        "evidence_url": activity.evidence_url,
        "notes": activity.notes,
        "can_submit_evidence": activity.status not in {ConsultantJourneyActivityStatus.COMPLETED.value, ConsultantJourneyActivityStatus.SKIPPED.value},
    }


def _student_role_fit_message(consultant: ConsultantProfile, role_name: str, program: TrainingProgram | None) -> str:
    domain = consultant.target_industry_domain or consultant.latest_project_domain or (program.industry_domain if program else "")
    skills = ", ".join(_split_training_items(consultant.primary_skills)[:4])
    message = f"Mintel is preparing you for {role_name}"
    if domain:
        message += f" in {domain}"
    if skills:
        message += f", using your current strengths in {skills}"
    return message + "."


def _student_market_readiness_message(journey: ConsultantRoleJourney, active_items: list[dict[str, Any]]) -> str:
    if journey.readiness_score >= 85:
        return "You are close to active marketing. Keep your resume, project story, and interview answers sharp."
    if journey.readiness_score >= 60:
        return "You are progressing. Finish the remaining role, resume, and mock interview tasks before broad marketing."
    if active_items:
        return f"Focus on the next task first: {active_items[0]['student_title']}."
    return "Start with profile intake so Mintel can build your role plan."


def _student_timeline_steps(current_stage: str) -> list[dict[str, str]]:
    order = [
        ("profile_intake", "Intake"),
        ("training_plan", "Training"),
        ("project_story", "Project Story"),
        ("positioning", "Resume & Pitch"),
        ("interview_readiness", "Mock"),
        ("final_evidence", "Evidence"),
        ("company_matching", "Companies"),
        ("submission_pipeline", "Submissions"),
        ("interview_pipeline", "Interviews"),
        ("offer", "Offer"),
        ("placement", "Placement"),
        ("post_placement", "Post-Placement"),
    ]
    index_by_stage = {stage: idx for idx, (stage, _) in enumerate(order)}
    current_index = index_by_stage.get(current_stage, 0)
    if current_stage in {"role_intake"}:
        current_index = 0
    if current_stage in {"campaign_active"}:
        current_index = index_by_stage["company_matching"]
    return [
        {
            "stage": stage,
            "label": label,
            "state": "done" if idx < current_index else "current" if idx == current_index else "upcoming",
        }
        for idx, (stage, label) in enumerate(order)
    ]


def _sync_consultant_flag_from_activity(consultant: ConsultantProfile, activity: ConsultantJourneyActivity) -> None:
    completed = activity.status == ConsultantJourneyActivityStatus.COMPLETED.value
    if activity.key == "assign_role":
        return
    if activity.key == "verify_work_auth":
        return
    flag = _JOURNEY_LEGACY_FLAG_BY_ACTIVITY.get(activity.key)
    if flag:
        setattr(consultant, flag, completed)


def _journey_activity_status_options() -> list[tuple[str, str]]:
    return [
        (ConsultantJourneyActivityStatus.TODO.value, "To do"),
        (ConsultantJourneyActivityStatus.IN_PROGRESS.value, "In progress"),
        (ConsultantJourneyActivityStatus.BLOCKED.value, "Blocked"),
        (ConsultantJourneyActivityStatus.COMPLETED.value, "Completed"),
        (ConsultantJourneyActivityStatus.SKIPPED.value, "Skipped"),
    ]


def _journey_activity_status_values() -> set[str]:
    return {value for value, _ in _journey_activity_status_options()}


def _journey_status_options() -> list[tuple[str, str]]:
    return [
        (ConsultantJourneyStatus.ACTIVE.value, "Active"),
        (ConsultantJourneyStatus.PAUSED.value, "Paused"),
        (ConsultantJourneyStatus.COMPLETED.value, "Completed"),
        (ConsultantJourneyStatus.PLACED.value, "Placed"),
        (ConsultantJourneyStatus.POST_PLACEMENT.value, "Post-placement support"),
        (ConsultantJourneyStatus.ARCHIVED.value, "Archived"),
    ]


def _journey_status_values() -> set[str]:
    return {value for value, _ in _journey_status_options()}


def _consultant_marketing_status_options() -> list[tuple[str, str]]:
    return [
        ("profile_intake", "Profile intake"),
        ("training", "Training"),
        ("mock_interviews", "Mock interviews"),
        ("marketing_ready", "Marketing ready"),
        ("actively_marketing", "Actively marketing"),
        ("offer", "Offer / final round"),
        ("placed", "Placed"),
        ("post_placement", "Post-placement support"),
        ("on_hold", "On hold"),
    ]


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    q: str = "",
    company_id: str = "",
    status: str = "active",
    sort: str = "updated",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    error: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    selected_company_id = _optional_query_int(company_id)
    query = select(JobOpportunity).join(Company).join(CompanyPursuit)
    visible_clause = _pursuit_visibility_clause(user)
    if visible_clause is not None:
        query = query.where(visible_clause)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(func.lower(JobOpportunity.title).like(pattern) | func.lower(Company.name).like(pattern) | func.lower(JobOpportunity.location).like(pattern))
    if selected_company_id:
        query = query.where(JobOpportunity.company_id == selected_company_id)
    if status == "active":
        query = query.where(JobOpportunity.active.is_(True))
    elif status == "inactive":
        query = query.where(JobOpportunity.active.is_(False))
    elif status == "pending":
        query = query.where(JobOpportunity.approval_status == "pending")
    sort_map = {
        "title": JobOpportunity.title.asc(),
        "company": Company.name.asc(),
        "location": JobOpportunity.location.asc(),
        "status": JobOpportunity.active.desc(),
        "updated": JobOpportunity.updated_at.desc(),
    }
    query = query.order_by(sort_map.get(sort, JobOpportunity.updated_at.desc()), JobOpportunity.id.desc())
    total_rows = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = _pagination_context(total_rows, page, per_page)
    jobs = db.scalars(query.limit(per_page).offset(pagination["offset"])).all()
    params = {"q": q, "company_id": selected_company_id or "", "status": status, "sort": sort, "per_page": per_page}
    return templates.TemplateResponse(
        "web/jobs.html",
        {
            "request": request,
            "user": user,
            "jobs": jobs,
            "editable_job_ids": {job.id for job in jobs if _can_edit_job(user, job)},
            "q": q,
            "company_id": selected_company_id,
            "status": status,
            "sort": sort,
            "promoted_companies": _promoted_companies_for_user(db, user),
            "status_options": [("active", "Active"), ("pending", "Pending Approval"), ("inactive", "Inactive"), ("all", "All")],
            "can_create_jobs": has_permission(user, Permission.MANAGE_PURSUIT_WORKSPACE),
            "sort_urls": _sort_urls("/jobs", params, ["title", "company", "location", "status", "updated"]),
            "page_params": urlencode(params),
            "error": error,
            **pagination,
        },
    )


@router.get("/jobs/new", response_class=HTMLResponse)
def new_job_form(request: Request, error: str = "", user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)), db: Session = Depends(get_db)):
    return _job_form_response(request, user, db, None, error)


@router.post("/jobs")
def create_job(
    request: Request,
    company_id: Optional[int] = Form(None),
    company_name: str = Form(""),
    title: str = Form(""),
    requirement_key: str = Form(""),
    certifications_required: str = Form(""),
    marketing_roles: Optional[list[int]] = Form(None),
    additional_cloud_specializations: Optional[list[str]] = Form(None),
    location: str = Form(""),
    job_type: str = Form(""),
    experience_level: str = Form(""),
    source: str = Form(JobSource.OTHER.value),
    url: str = Form(""),
    posted_on: str = Form(""),
    ats_platform: str = Form(""),
    description: str = Form(""),
    decision_payload: str = Form(""),
    sponsorship_notes: str = Form(""),
    is_active: Optional[bool] = Form(None),
    active: Optional[bool] = Form(None),
    job_alerts_created: bool = Form(False),
    next: str = Form(""),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    return _create_staff_manual_job(
        request,
        db,
        user,
        company_id=company_id,
        company_name=company_name,
        title=title,
        requirement_key=requirement_key,
        certifications_required=certifications_required,
        marketing_roles=marketing_roles,
        additional_cloud_specializations=additional_cloud_specializations,
        location=location,
        job_type=job_type,
        experience_level=experience_level,
        source=source,
        url=url,
        posted_on=posted_on,
        ats_platform=ats_platform,
        description=description,
        decision_payload=decision_payload,
        sponsorship_notes=sponsorship_notes,
        is_active=is_active if is_active is not None else bool(active),
        job_alerts_created=job_alerts_created,
        next_url=next,
        error_target="/jobs/new",
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    job = db.get(JobOpportunity, job_id)
    if not job:
        return RedirectResponse("/jobs", status_code=303)
    if not job.company or not job.company.pursuit or not _can_view_pursuit(user, job.company.pursuit):
        raise PermissionDenied("This job belongs to another region group.")
    return templates.TemplateResponse(
        "web/job_detail.html",
        {
            "request": request,
            "user": user,
            "job": job,
            "job_description_html": _sanitize_rich_text(job.description or ""),
            "job_sponsorship_notes_html": _sanitize_rich_text(job.sponsorship_notes or ""),
            "can_edit_job": _can_edit_job(user, job),
        },
    )


@router.get("/jobs/{job_id}/edit", response_class=HTMLResponse)
def edit_job_form(job_id: int, request: Request, error: str = "", user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)), db: Session = Depends(get_db)):
    job = db.get(JobOpportunity, job_id)
    if not job:
        return RedirectResponse("/jobs", status_code=303)
    if not _can_edit_job(user, job):
        raise PermissionDenied("Only managers or the staff user who created this job can edit it.")
    return _job_form_response(request, user, db, job, error)


@router.post("/jobs/{job_id}")
def update_job(
    request: Request,
    job_id: int,
    company_id: Optional[int] = Form(None),
    company_name: str = Form(""),
    title: str = Form(""),
    requirement_key: str = Form(""),
    certifications_required: str = Form(""),
    marketing_roles: Optional[list[int]] = Form(None),
    additional_cloud_specializations: Optional[list[str]] = Form(None),
    location: str = Form(""),
    job_type: str = Form(""),
    experience_level: str = Form(""),
    source: str = Form(JobSource.OTHER.value),
    url: str = Form(""),
    posted_on: str = Form(""),
    ats_platform: str = Form(""),
    description: str = Form(""),
    decision_payload: str = Form(""),
    sponsorship_notes: str = Form(""),
    is_active: Optional[bool] = Form(None),
    active: Optional[bool] = Form(None),
    job_alerts_created: bool = Form(False),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    job = db.get(JobOpportunity, job_id)
    if not job:
        return RedirectResponse("/jobs", status_code=303)
    if not _can_edit_job(user, job):
        raise PermissionDenied("Only managers or the staff user who created this job can edit it.")
    company = _job_company_from_form(db, company_id, company_name)
    if not company or not _is_promoted_company(db, company.id) or not _can_view_pursuit(user, company.pursuit):
        return RedirectResponse(f"/jobs/{job_id}/edit?error=Jobs+can+only+be+assigned+to+promoted+companies", status_code=303)
    validation_error = _job_validation_error(db, company.id, title, requirement_key, location, url, description, marketing_roles or [], job_id=job.id)
    if validation_error:
        return RedirectResponse(f"/jobs/{job_id}/edit?error={validation_error}", status_code=303)
    _apply_job_fields(
        job,
        company=company,
        title=title,
        requirement_key=requirement_key,
        certifications_required=certifications_required,
        marketing_roles=marketing_roles,
        additional_cloud_specializations=additional_cloud_specializations,
        location=location,
        job_type=job_type,
        experience_level=experience_level,
        source=source,
        url=url,
        posted_on=posted_on,
        ats_platform=ats_platform,
        description=description,
        decision_payload=decision_payload,
        sponsorship_notes=sponsorship_notes,
        active=is_active if is_active is not None else bool(active),
        job_alerts_created=job_alerts_created,
    )
    db.add(job)
    db.commit()
    _flash(request, f"Updated job {job.title}.")
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@router.post("/jobs/{job_id}/delete")
def delete_job(job_id: int, request: Request, user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)), db: Session = Depends(get_db)):
    job = db.get(JobOpportunity, job_id)
    if job:
        if not _can_edit_job(user, job):
            raise PermissionDenied("Only managers or the staff user who created this job can edit it.")
        if job.active:
            job.active = False
            db.add(job)
            _flash(request, f"Deactivated job {job.title}.")
        else:
            _flash(request, f"Deleted job {job.title}.")
            db.delete(job)
        db.commit()
    return RedirectResponse("/jobs", status_code=303)


@router.get("/resume-versions", response_class=HTMLResponse)
def resume_versions_page(
    request: Request,
    consultant_id: Optional[int] = Query(None),
    status: str = "active",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    error: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = select(ResumeVersion).join(ConsultantProfile)
    if consultant_id:
        query = query.where(ResumeVersion.consultant_id == consultant_id)
    if status == "active":
        query = query.where(ResumeVersion.active.is_(True))
    elif status == "inactive":
        query = query.where(ResumeVersion.active.is_(False))
    query = query.order_by(ResumeVersion.updated_at.desc(), ResumeVersion.id.desc())
    total_rows = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = _pagination_context(total_rows, page, per_page)
    rows = db.scalars(query.limit(per_page).offset(pagination["offset"])).all()
    params = {"consultant_id": consultant_id or "", "status": status, "per_page": per_page}
    return templates.TemplateResponse(
        "web/resume_versions.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "consultants": _active_consultants(db),
            "marketing_roles": _active_marketing_roles(db),
            "promoted_jobs": _promoted_jobs(db),
            "domains": INDUSTRY_DOMAINS,
            "consultant_id": consultant_id,
            "status": status,
            "status_options": [("active", "Active"), ("inactive", "Inactive"), ("all", "All")],
            "page_params": urlencode(params),
            "error": error,
            **pagination,
        },
    )


@router.post("/resume-versions")
def create_resume_version(
    request: Request,
    consultant_id: int = Form(...),
    version_name: str = Form(""),
    base_resume_name: str = Form(""),
    target_role_id: Optional[int] = Form(None),
    target_domain: str = Form(""),
    target_job_id: Optional[int] = Form(None),
    latest_project_update: str = Form(""),
    supporting_project_improvements: str = Form(""),
    ats_score: int = Form(0),
    tailoring_notes: str = Form(""),
    file_reference: str = Form(""),
    active: bool = Form(False),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant:
        return RedirectResponse("/resume-versions?error=Select+a+valid+consultant", status_code=303)
    if target_job_id and not _is_promoted_job(db, target_job_id):
        return RedirectResponse("/resume-versions?error=Target+job+must+belong+to+a+promoted+company", status_code=303)
    row = ResumeVersion(
        consultant_id=consultant_id,
        version_name=version_name.strip() or f"{consultant.name} tailored resume",
        base_resume_name=base_resume_name.strip(),
        target_role_id=target_role_id,
        target_domain=target_domain.strip(),
        target_job_id=target_job_id,
        latest_project_update=latest_project_update.strip(),
        supporting_project_improvements=supporting_project_improvements.strip(),
        ats_score=max(0, min(100, ats_score)),
        tailoring_notes=tailoring_notes.strip(),
        file_reference=file_reference.strip(),
        active=active,
    )
    db.add(row)
    db.commit()
    _flash(request, f"Created resume version for {consultant.name}.")
    return RedirectResponse("/resume-versions", status_code=303)


@router.get("/submissions", response_class=HTMLResponse)
def submissions_page(
    request: Request,
    consultant_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    status: str = "all",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    error: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = select(ConsultantSubmission).join(ConsultantProfile).join(JobOpportunity).join(Company).join(CompanyPursuit)
    consultant_profile = _consultant_profile_for_user(db, user) if _is_consultant_user(user) else None
    if _is_consultant_user(user):
        consultant_id = consultant_profile.id if consultant_profile else -1
        query = query.where(ConsultantSubmission.consultant_id == consultant_id)
    elif consultant_id:
        query = query.where(ConsultantSubmission.consultant_id == consultant_id)
    if company_id:
        query = query.where(JobOpportunity.company_id == company_id)
    if status != "all":
        query = query.where(ConsultantSubmission.status == status)
    query = query.order_by(ConsultantSubmission.updated_at.desc(), ConsultantSubmission.id.desc())
    total_rows = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = _pagination_context(total_rows, page, per_page)
    rows = db.scalars(query.limit(per_page).offset(pagination["offset"])).all()
    params = {"consultant_id": consultant_id or "", "company_id": company_id or "", "status": status, "per_page": per_page}
    return templates.TemplateResponse(
        "web/submissions.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "consultants": [consultant_profile] if consultant_profile else _active_consultants(db),
            "promoted_companies": _promoted_companies_for_user(db, user),
            "promoted_jobs": _promoted_jobs(db),
            "resume_versions": _active_resume_versions(db),
            "status_options": _submission_status_options(include_all=True),
            "consultant_id": consultant_id,
            "company_id": company_id,
            "status": status,
            "page_params": urlencode(params),
            "error": error,
            **pagination,
        },
    )


@router.post("/submissions")
def create_submission(
    request: Request,
    consultant_id: int = Form(...),
    job_id: int = Form(...),
    resume_version_id: Optional[int] = Form(None),
    submitted_on: str = Form(""),
    status: str = Form(SubmissionStatus.DRAFT.value),
    vendor_contact: str = Form(""),
    bill_rate: str = Form(""),
    submission_notes: str = Form(""),
    next_step: str = Form(""),
    admin_override_reason: str = Form(""),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant:
        return RedirectResponse("/submissions?error=Select+a+valid+consultant", status_code=303)
    if not _is_promoted_job(db, job_id):
        return RedirectResponse("/submissions?error=Submissions+can+only+use+jobs+from+promoted+companies", status_code=303)
    if resume_version_id and not _resume_version_belongs_to_consultant(db, resume_version_id, consultant_id):
        return RedirectResponse("/submissions?error=Resume+version+must+belong+to+the+selected+consultant", status_code=303)
    normalized_status = status if status in _submission_status_values() else SubmissionStatus.DRAFT.value
    eligibility = submission_eligibility_context(consultant, status=normalized_status, admin_override_reason=admin_override_reason, user=user)
    if not eligibility["allowed"]:
        return RedirectResponse(f"/submissions?{urlencode({'error': eligibility['reason']})}", status_code=303)
    notes = submission_notes.strip()
    if eligibility["override_allowed"]:
        notes = "\n\n".join(part for part in [notes, eligibility["reason"]] if part)
    row = ConsultantSubmission(
        consultant_id=consultant_id,
        job_id=job_id,
        resume_version_id=resume_version_id,
        submitted_on=_parse_date(submitted_on),
        status=normalized_status,
        vendor_contact=vendor_contact.strip(),
        bill_rate=bill_rate.strip(),
        submission_notes=notes,
        next_step=next_step.strip(),
    )
    db.add(row)
    db.commit()
    _flash(request, "Created submission.")
    return RedirectResponse("/submissions", status_code=303)


@router.get("/targeting-campaigns", response_class=HTMLResponse)
def targeting_campaigns_page(
    request: Request,
    consultant_id: Optional[int] = Query(None),
    status: str = "all",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = select(TargetingCampaign).join(ConsultantProfile)
    consultant_profile = _consultant_profile_for_user(db, user) if _is_consultant_user(user) else None
    if _is_consultant_user(user):
        consultant_id = consultant_profile.id if consultant_profile else -1
        query = query.where(TargetingCampaign.consultant_id == consultant_id)
    elif consultant_id:
        query = query.where(TargetingCampaign.consultant_id == consultant_id)
    if status != "all":
        query = query.where(TargetingCampaign.status == status)
    query = query.order_by(TargetingCampaign.updated_at.desc(), TargetingCampaign.id.desc())
    rows = db.scalars(query.limit(500)).all()
    campaign_ids = [row.id for row in rows]
    target_counts = _campaign_target_counts(db, campaign_ids)
    return templates.TemplateResponse(
        "web/targeting_campaigns.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "target_counts": target_counts,
            "consultants": [consultant_profile] if consultant_profile else _active_consultants(db),
            "status_options": _campaign_status_options(include_all=True),
            "consultant_id": consultant_id,
            "status": status,
            "total_rows": len(rows),
        },
    )


@router.post("/targeting-campaigns/from-matches")
def create_targeting_campaign_from_matches(
    request: Request,
    consultant_id: int = Form(...),
    fiscal_year: str = Form(""),
    region_id: str = Form(""),
    source: str = Form("all"),
    min_approvals: int = Form(5),
    min_approval_rate: int = Form(75),
    min_match_score: int = Form(35),
    goal_count: int = Form(25),
    due_date: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant:
        return RedirectResponse("/reports/candidate-company-matches", status_code=303)
    available_years = _available_uscis_years(db)
    selected_year = _optional_query_int(fiscal_year) or (max(available_years) if available_years else None)
    report = _candidate_company_matches_report(
        db,
        fiscal_year=selected_year,
        consultant_id=consultant_id,
        marketing_role_id=None,
        region_id=_optional_query_int(region_id),
        source=source,
        min_approvals=min_approvals,
        min_approval_rate=min_approval_rate,
        min_match_score=min_match_score,
    )
    selected_rows = report["rows"][: max(1, min(goal_count, 100))]
    owner_id = user.id if getattr(user, "id", None) else None
    campaign = TargetingCampaign(
        consultant_id=consultant_id,
        name=f"{consultant.name or consultant.email} targeting campaign",
        target_role=consultant.marketing_role.name if consultant.marketing_role else "",
        status=TargetingCampaignStatus.ACTIVE.value,
        owner_id=owner_id,
        due_date=_parse_date(due_date),
        goal_count=max(1, min(goal_count, 100)),
        min_match_score=max(0, min(min_match_score, 100)),
        notes=notes.strip() or f"Created from candidate-company matches for USCIS year {selected_year or 'latest'}.",
    )
    db.add(campaign)
    db.flush()
    for row in selected_rows:
        db.add(
            TargetingCampaignTarget(
                campaign_id=campaign.id,
                company_id=row["company_id"],
                pursuit_id=row.get("pursuit_id"),
                status=TargetingCampaignTargetStatus.QUEUED.value,
                match_score=row["match_score"],
                company_score=row["watch_score"],
                role_fit=row["best_roles_label"],
                skill_overlap=row["skill_hits_label"],
                gaps=row["candidate_gaps_label"],
                next_action=row["next_action"],
            )
        )
    db.commit()
    _flash(request, f"Created targeting campaign with {len(selected_rows)} company targets.")
    return RedirectResponse(f"/targeting-campaigns/{campaign.id}", status_code=303)


@router.get("/targeting-campaigns/{campaign_id}", response_class=HTMLResponse)
def targeting_campaign_detail(campaign_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    campaign = db.get(TargetingCampaign, campaign_id)
    if not campaign:
        return RedirectResponse("/targeting-campaigns", status_code=303)
    if _is_consultant_user(user):
        consultant_profile = _consultant_profile_for_user(db, user)
        if not consultant_profile or campaign.consultant_id != consultant_profile.id:
            raise PermissionDenied("This campaign is locked to the assigned consultant.")
    targets = db.scalars(select(TargetingCampaignTarget).where(TargetingCampaignTarget.campaign_id == campaign_id).order_by(TargetingCampaignTarget.match_score.desc(), TargetingCampaignTarget.id.asc())).all()
    return templates.TemplateResponse(
        "web/targeting_campaign_detail.html",
        {
            "request": request,
            "user": user,
            "campaign": campaign,
            "targets": targets,
            "target_status_options": _campaign_target_status_options(),
            "resume_versions": _active_resume_versions_for_consultant(db, campaign.consultant_id),
            "jobs_by_company": _campaign_jobs_by_company(db, targets),
            "summary": _campaign_summary(targets),
        },
    )


@router.get("/targeting-campaigns/{campaign_id}/edit", response_class=HTMLResponse)
def edit_targeting_campaign(campaign_id: int, request: Request, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    campaign = db.get(TargetingCampaign, campaign_id)
    if not campaign:
        return RedirectResponse("/targeting-campaigns", status_code=303)
    return templates.TemplateResponse(
        "web/targeting_campaign_form.html",
        {
            "request": request,
            "user": user,
            "campaign": campaign,
            "status_options": _campaign_status_options(),
            "owner_options": _assignable_staff_options(db),
        },
    )


@router.post("/targeting-campaigns/{campaign_id}")
def update_targeting_campaign(
    campaign_id: int,
    request: Request,
    name: str = Form(""),
    status: str = Form(TargetingCampaignStatus.ACTIVE.value),
    owner_id: Optional[int] = Form(None),
    due_date: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    campaign = db.get(TargetingCampaign, campaign_id)
    if not campaign:
        return RedirectResponse("/targeting-campaigns", status_code=303)
    campaign.name = name.strip() or campaign.name
    campaign.status = status if status in _campaign_status_values() else campaign.status
    campaign.owner_id = owner_id
    campaign.due_date = _parse_date(due_date)
    campaign.notes = notes.strip()
    db.add(campaign)
    db.commit()
    _flash(request, "Updated targeting campaign.")
    return RedirectResponse(f"/targeting-campaigns/{campaign_id}", status_code=303)


@router.post("/targeting-campaigns/{campaign_id}/status")
def update_targeting_campaign_status(
    campaign_id: int,
    request: Request,
    status: str = Form(TargetingCampaignStatus.ACTIVE.value),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    campaign = db.get(TargetingCampaign, campaign_id)
    if campaign and status in _campaign_status_values():
        campaign.status = status
        db.add(campaign)
        db.commit()
        _flash(request, f"Updated campaign status to {status.replace('_', ' ')}.")
    return RedirectResponse("/targeting-campaigns", status_code=303)


@router.post("/targeting-campaigns/{campaign_id}/targets/{target_id}")
def update_targeting_campaign_target(
    campaign_id: int,
    target_id: int,
    request: Request,
    status: str = Form(TargetingCampaignTargetStatus.QUEUED.value),
    next_action: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    target = db.get(TargetingCampaignTarget, target_id)
    if not target or target.campaign_id != campaign_id:
        return RedirectResponse(f"/targeting-campaigns/{campaign_id}", status_code=303)
    target.status = status if status in _campaign_target_status_values() else target.status
    target.next_action = next_action.strip()
    target.notes = notes.strip()
    db.add(target)
    db.commit()
    _flash(request, f"Updated target for {target.company.name}.")
    return RedirectResponse(f"/targeting-campaigns/{campaign_id}", status_code=303)


@router.post("/targeting-campaigns/{campaign_id}/targets/{target_id}/submission")
def create_submission_from_campaign_target(
    campaign_id: int,
    target_id: int,
    request: Request,
    job_id: int = Form(...),
    resume_version_id: Optional[int] = Form(None),
    submitted_on: str = Form(""),
    status: str = Form(SubmissionStatus.DRAFT.value),
    vendor_contact: str = Form(""),
    bill_rate: str = Form(""),
    submission_notes: str = Form(""),
    next_step: str = Form(""),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    campaign = db.get(TargetingCampaign, campaign_id)
    target = db.get(TargetingCampaignTarget, target_id)
    if not campaign or not target or target.campaign_id != campaign_id:
        return RedirectResponse(f"/targeting-campaigns/{campaign_id}", status_code=303)
    job = db.get(JobOpportunity, job_id)
    if not job or job.company_id != target.company_id or not _is_promoted_job(db, job_id):
        _flash(request, "Select a promoted-company job for this campaign target.", "error")
        return RedirectResponse(f"/targeting-campaigns/{campaign_id}", status_code=303)
    if resume_version_id and not _resume_version_belongs_to_consultant(db, resume_version_id, campaign.consultant_id):
        _flash(request, "Resume version must belong to the campaign candidate.", "error")
        return RedirectResponse(f"/targeting-campaigns/{campaign_id}", status_code=303)
    normalized_status = status if status in _submission_status_values() else SubmissionStatus.DRAFT.value
    eligibility = submission_eligibility_context(campaign.consultant, status=normalized_status, user=user)
    if not eligibility["allowed"]:
        _flash(request, eligibility["reason"], "error")
        return RedirectResponse(f"/targeting-campaigns/{campaign_id}", status_code=303)
    submission = ConsultantSubmission(
        consultant_id=campaign.consultant_id,
        job_id=job_id,
        resume_version_id=resume_version_id,
        submitted_on=_parse_date(submitted_on),
        status=normalized_status,
        vendor_contact=vendor_contact.strip(),
        bill_rate=bill_rate.strip(),
        submission_notes=submission_notes.strip() or f"Created from targeting campaign: {campaign.name}",
        next_step=next_step.strip() or target.next_action,
    )
    db.add(submission)
    db.flush()
    target.job_id = job_id
    target.submission_id = submission.id
    target.status = TargetingCampaignTargetStatus.SUBMITTED.value if submission.status != SubmissionStatus.DRAFT.value else TargetingCampaignTargetStatus.READY_TO_SUBMIT.value
    target.next_action = next_step.strip() or "Follow up on submitted resume and update campaign status."
    db.add(target)
    db.commit()
    _flash(request, f"Created submission for {campaign.consultant.name or campaign.consultant.email} at {target.company.name}.")
    return RedirectResponse(f"/targeting-campaigns/{campaign_id}", status_code=303)


@router.get("/mock-interviews", response_class=HTMLResponse)
def mock_interviews_page(
    request: Request,
    consultant_id: Optional[int] = Query(None),
    marketing_role_id: Optional[int] = Query(None),
    status: str = "all",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    error: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    marketing_roles = _visible_marketing_roles_for_user(db, user)
    query = select(MockInterview).join(ConsultantProfile)
    consultant_profile = _consultant_profile_for_user(db, user) if _is_consultant_user(user) else None
    if _is_consultant_user(user):
        query = query.where(MockInterview.consultant_id == (consultant_profile.id if consultant_profile else -1))
    if visible_role_ids is not None:
        query = query.where(MockInterview.marketing_role_id.in_(visible_role_ids or {-1}))
    if consultant_id:
        query = query.where(MockInterview.consultant_id == consultant_id)
    if marketing_role_id:
        if visible_role_ids is not None and marketing_role_id not in visible_role_ids:
            query = query.where(MockInterview.marketing_role_id == -1)
        else:
            query = query.where(MockInterview.marketing_role_id == marketing_role_id)
    if status != "all":
        query = query.where(MockInterview.status == status)
    query = query.order_by(MockInterview.scheduled_on.desc().nullslast(), MockInterview.scheduled_time.desc(), MockInterview.updated_at.desc())
    total_rows = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = _pagination_context(total_rows, page, per_page)
    rows = db.scalars(query.limit(per_page).offset(pagination["offset"])).all()
    params = {"consultant_id": consultant_id or "", "marketing_role_id": marketing_role_id or "", "status": status, "per_page": per_page}
    return templates.TemplateResponse(
        "web/mock_interviews.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "consultants": [consultant_profile] if consultant_profile else _active_consultants(db, visible_role_ids),
            "submissions": _recent_submissions(db, visible_role_ids),
            "marketing_roles": marketing_roles,
            "assigned_staff_options": _mock_interview_staff_options(db, visible_role_ids),
            "training_programs": _visible_training_programs(db, visible_role_ids),
            "status_options": _mock_status_options(include_all=True),
            "timezone_options": _timezone_options(),
            "round_type_options": _mock_round_type_options(),
            "consultant_id": consultant_id,
            "marketing_role_id": marketing_role_id,
            "status": status,
            "can_manage_mock_interviews": _can_manage_mock_interviews(user),
            "page_params": urlencode(params),
            "error": error,
            **pagination,
        },
    )


@router.get("/mock-interviews/export.csv")
def mock_interviews_export(
    consultant_id: Optional[int] = Query(None),
    marketing_role_id: Optional[int] = Query(None),
    status: str = "all",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    query = _mock_interviews_query_for_user(user, visible_role_ids).join(ConsultantProfile)
    if consultant_id:
        query = query.where(MockInterview.consultant_id == consultant_id)
    if marketing_role_id:
        query = query.where(MockInterview.marketing_role_id == marketing_role_id)
    if status != "all":
        query = query.where(MockInterview.status == status)
    rows = db.scalars(query.order_by(MockInterview.scheduled_on.desc().nullslast(), MockInterview.scheduled_time.desc())).all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["S No", "Consultant", "Email", "Marketing Role", "Domain", "Scheduled On", "Time", "Timezone", "Duration", "Assigned Staff", "Status", "Meeting Link", "Action Items", "Status History"])
    event_map = _mock_status_event_map(db, [row.id for row in rows])
    for index, row in enumerate(rows, start=1):
        writer.writerow(
            [
                index,
                row.consultant.name if row.consultant else "",
                row.consultant.email if row.consultant else "",
                row.role_snapshot or (row.marketing_role.name if row.marketing_role else ""),
                row.domain_snapshot or (row.training_program.industry_domain if row.training_program else ""),
                row.scheduled_on or "",
                row.scheduled_time or "",
                row.timezone or "",
                row.duration_minutes or "",
                row.assigned_staff.name if row.assigned_staff else row.interviewer_name,
                str(row.status).replace("_", " ").title(),
                row.meeting_link or "",
                row.action_items or "",
                " | ".join(event_map.get(row.id, [])),
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=mock_interviews.csv"},
    )


@router.get("/mock-interviews/calendar", response_class=HTMLResponse)
def mock_interviews_calendar(
    request: Request,
    marketing_role_id: Optional[int] = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    query = _mock_interviews_query_for_user(user, visible_role_ids).where(MockInterview.scheduled_on.is_not(None))
    if marketing_role_id:
        query = query.where(MockInterview.marketing_role_id == marketing_role_id)
    rows = db.scalars(query.order_by(MockInterview.scheduled_on.asc(), MockInterview.scheduled_time.asc()).limit(300)).all()
    grouped: dict[date, list[MockInterview]] = {}
    for row in rows:
        if row.scheduled_on:
            grouped.setdefault(row.scheduled_on, []).append(row)
    calendar_days = [{"day": day, "rows": grouped[day]} for day in sorted(grouped)]
    return templates.TemplateResponse(
        "web/mock_interview_calendar.html",
        {
            "request": request,
            "user": user,
            "calendar_days": calendar_days,
            "marketing_roles": _visible_marketing_roles_for_user(db, user),
            "marketing_role_id": marketing_role_id,
        },
    )


@router.get("/mock-interviews/trainer-dashboard", response_class=HTMLResponse)
def mock_interview_trainer_dashboard(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    base = _mock_interviews_query_for_user(user, visible_role_ids)
    today = date.today()
    rows = db.scalars(base.order_by(MockInterview.scheduled_on.asc().nullslast(), MockInterview.scheduled_time.asc()).limit(500)).all()
    today_rows = [row for row in rows if row.scheduled_on == today and row.status not in {MockInterviewStatus.CANCELLED.value, MockInterviewStatus.COMPLETED.value}]
    upcoming_rows = [row for row in rows if row.scheduled_on and row.scheduled_on >= today and row.status in {MockInterviewStatus.PLANNED.value, MockInterviewStatus.PENDING_ACK.value}]
    pending_ack_rows = [row for row in rows if row.consultant_ack_status == "pending"]
    requested_rows = [row for row in rows if row.status in {MockInterviewStatus.RESCHEDULE_REQUESTED.value, MockInterviewStatus.CANCELLATION_REQUESTED.value}]
    feedback_rows = [row for row in rows if row.status == MockInterviewStatus.WAITING_FEEDBACK.value or (row.status == MockInterviewStatus.COMPLETED.value and not (row.strengths or row.gaps or row.action_items))]
    return templates.TemplateResponse(
        "web/mock_interview_trainer_dashboard.html",
        {
            "request": request,
            "user": user,
            "today_rows": today_rows,
            "upcoming_rows": upcoming_rows[:20],
            "pending_ack_rows": pending_ack_rows[:20],
            "requested_rows": requested_rows[:20],
            "feedback_rows": feedback_rows[:20],
            "summary_tiles": [
                {"label": "Today", "value": len(today_rows), "href": "#today"},
                {"label": "Upcoming", "value": len(upcoming_rows), "href": "#upcoming"},
                {"label": "Pending Ack", "value": len(pending_ack_rows), "href": "#pending-ack"},
                {"label": "Requests", "value": len(requested_rows), "href": "#requests"},
                {"label": "Feedback Due", "value": len(feedback_rows), "href": "#feedback"},
            ],
        },
    )


@router.get("/mock-interviews/new", response_class=HTMLResponse)
def new_mock_interview_form(
    request: Request,
    marketing_role_id: Optional[int] = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not _can_manage_mock_interviews(user):
        raise PermissionDenied("Mock interview scheduling access is required.")
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    staff_options = _mock_interview_staff_options(db, visible_role_ids)
    selected_role_ids = {marketing_role_id} if marketing_role_id else visible_role_ids
    return templates.TemplateResponse(
        "web/mock_interview_form.html",
        {
            "request": request,
            "user": user,
            "row": None,
            "form_title": "Add Mock Interview",
            "form_action": "/mock-interviews",
            "submit_label": "Save Mock Interview",
            "consultants": _active_consultants(db, selected_role_ids),
            "marketing_roles": _visible_marketing_roles_for_user(db, user),
            "assigned_staff_options": staff_options,
            "training_programs": _visible_training_programs(db, selected_role_ids),
            "submissions": _recent_submissions(db, selected_role_ids),
            "timezone_options": _timezone_options(),
            "round_type_options": _mock_round_type_options(),
            "status_options": _mock_status_options(),
            "availability_by_staff": _availability_summary_by_staff(db, staff_options),
            "marketing_role_id": marketing_role_id,
        },
    )


@router.get("/mock-interviews/book", response_class=HTMLResponse)
def mock_interview_book_page(marketing_role_id: Optional[int] = Query(None)):
    suffix = f"?marketing_role_id={marketing_role_id}" if marketing_role_id else ""
    return RedirectResponse(f"/mock-interviews/new{suffix}", status_code=303)


@router.post("/mock-interviews")
def create_mock_interview(
    request: Request,
    consultant_id: int = Form(...),
    marketing_role_id: Optional[int] = Form(None),
    assigned_staff_id: Optional[int] = Form(None),
    submission_id: Optional[int] = Form(None),
    training_program_id: Optional[int] = Form(None),
    scheduled_on: str = Form(""),
    scheduled_time: str = Form(""),
    timezone: str = Form("America/Chicago"),
    duration_minutes: int = Form(60),
    meeting_link: str = Form(""),
    round_type: str = Form("mock"),
    interviewer_name: str = Form(""),
    status: str = Form(MockInterviewStatus.PLANNED.value),
    score: int = Form(0),
    strengths: str = Form(""),
    gaps: str = Form(""),
    action_items: str = Form(""),
    question_coverage: str = Form(""),
    questions_asked: str = Form(""),
    prep_pack_snapshot: str = Form(""),
    require_acknowledgement: bool = Form(False),
    override_conflict: bool = Form(False),
    conflict_override_reason: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not _can_manage_mock_interviews(user):
        raise PermissionDenied("Mock interview scheduling access is required.")
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant:
        return RedirectResponse("/mock-interviews?error=Select+a+valid+consultant", status_code=303)
    if submission_id and not _submission_belongs_to_consultant(db, submission_id, consultant_id):
        return RedirectResponse("/mock-interviews?error=Submission+must+belong+to+the+selected+consultant", status_code=303)
    program = db.get(TrainingProgram, training_program_id) if training_program_id else None
    selected_role_id = marketing_role_id or (program.marketing_role_id if program else None) or consultant.marketing_role_id
    marketing_role = db.get(MarketingRole, selected_role_id) if selected_role_id else None
    if not marketing_role or not marketing_role.active:
        return RedirectResponse("/mock-interviews?error=Select+a+valid+marketing+role", status_code=303)
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    if visible_role_ids is not None and marketing_role.id not in visible_role_ids:
        return RedirectResponse("/mock-interviews?error=You+can+only+schedule+mock+interviews+for+your+assigned+marketing+roles", status_code=303)
    if consultant.marketing_role_id and consultant.marketing_role_id != marketing_role.id:
        return RedirectResponse("/mock-interviews?error=Consultant+marketing+role+must+match+the+mock+interview+marketing+role", status_code=303)
    if program and program.marketing_role_id != marketing_role.id:
        return RedirectResponse("/mock-interviews?error=Training+program+must+match+the+selected+marketing+role", status_code=303)
    assigned_staff = _assignable_staff_member(db, assigned_staff_id) if assigned_staff_id else None
    if assigned_staff_id and not assigned_staff:
        return RedirectResponse("/mock-interviews?error=Select+a+valid+assigned+staff+member", status_code=303)
    if assigned_staff and not _staff_assigned_to_marketing_role(assigned_staff, marketing_role.id):
        return RedirectResponse("/mock-interviews?error=Assigned+staff+must+be+assigned+to+the+selected+marketing+role", status_code=303)
    conflicts = _mock_interview_conflicts(db, consultant_id, assigned_staff.id if assigned_staff else None, scheduled_on, scheduled_time, duration_minutes)
    if conflicts and not override_conflict:
        return RedirectResponse("/mock-interviews?error=Scheduling+conflict+found.+Use+override+with+a+reason+from+Book+Mock.", status_code=303)
    row = MockInterview(
        consultant_id=consultant_id,
        submission_id=submission_id,
        training_program_id=training_program_id,
        marketing_role_id=marketing_role.id,
        assigned_staff_id=assigned_staff.id if assigned_staff else None,
        scheduled_on=_parse_date(scheduled_on),
        scheduled_time=scheduled_time.strip(),
        timezone=timezone if timezone in _timezone_values() else "America/Chicago",
        duration_minutes=max(15, min(240, duration_minutes)),
        meeting_link=meeting_link.strip(),
        round_type=round_type.strip() if round_type in _mock_round_type_values() else "mock",
        interviewer_name=interviewer_name.strip(),
        role_snapshot=marketing_role.name,
        domain_snapshot=program.industry_domain if program else "",
        score=max(0, min(100, score)),
        strengths=strengths.strip(),
        gaps=gaps.strip(),
        action_items=action_items.strip(),
        question_coverage=question_coverage.strip(),
        questions_asked=questions_asked.strip(),
        prep_pack_snapshot=prep_pack_snapshot.strip(),
        consultant_ack_status="pending" if require_acknowledgement else "not_required",
        status=MockInterviewStatus.PENDING_ACK.value if require_acknowledgement else (status if status in _mock_status_values() else MockInterviewStatus.PLANNED.value),
        conflict_overridden=bool(conflicts and override_conflict),
        conflict_override_reason=conflict_override_reason.strip(),
    )
    db.add(row)
    db.flush()
    _record_mock_event(db, row, user, "book", "", row.status, "Created mock interview.")
    db.commit()
    _flash(request, "Created mock interview.")
    return RedirectResponse("/mock-interviews", status_code=303)


@router.get("/mock-interviews/trainer-availability", response_class=HTMLResponse)
def trainer_availability_page(
    request: Request,
    staff_id: Optional[int] = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not _can_manage_mock_interviews(user):
        raise PermissionDenied("Mock interview scheduling access is required.")
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    staff_options = _mock_interview_staff_options(db, visible_role_ids)
    selected_staff_id = staff_id or (staff_options[0].id if staff_options else None)
    weekly = []
    adhoc = []
    if selected_staff_id:
        weekly = db.scalars(select(TrainerWeeklyAvailability).where(TrainerWeeklyAvailability.staff_id == selected_staff_id).order_by(TrainerWeeklyAvailability.weekday, TrainerWeeklyAvailability.start_time)).all()
        adhoc = db.scalars(select(TrainerAdhocAvailability).where(TrainerAdhocAvailability.staff_id == selected_staff_id).order_by(TrainerAdhocAvailability.start_at.desc())).all()
    return templates.TemplateResponse(
        "web/mock_interview_trainer_availability.html",
        {
            "request": request,
            "user": user,
            "staff_options": staff_options,
            "selected_staff_id": selected_staff_id,
            "weekly": weekly,
            "adhoc": adhoc,
            "timezone_options": _timezone_options(),
            "weekdays": _weekday_options(),
        },
    )


@router.post("/mock-interviews/trainer-availability")
def save_trainer_availability(
    request: Request,
    staff_id: int = Form(...),
    kind: str = Form("weekly"),
    weekday: int = Form(0),
    start_time: str = Form(""),
    end_time: str = Form(""),
    start_at: str = Form(""),
    end_at: str = Form(""),
    timezone_name: str = Form("America/Chicago"),
    notes: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not _can_manage_mock_interviews(user):
        raise PermissionDenied("Mock interview scheduling access is required.")
    staff = _assignable_staff_member(db, staff_id)
    if not staff:
        return RedirectResponse("/mock-interviews/trainer-availability?error=Select+a+valid+staff+member", status_code=303)
    if kind == "adhoc":
        row = TrainerAdhocAvailability(
            staff_id=staff_id,
            start_at=_parse_local_datetime(start_at) or datetime.now(timezone.utc),
            end_at=_parse_local_datetime(end_at) or datetime.now(timezone.utc),
            timezone=timezone_name,
            notes=notes.strip(),
            active=True,
        )
    else:
        row = TrainerWeeklyAvailability(
            staff_id=staff_id,
            weekday=max(0, min(6, weekday)),
            start_time=_parse_time_value(start_time) or datetime_time(9, 0),
            end_time=_parse_time_value(end_time) or datetime_time(17, 0),
            timezone=timezone_name,
            notes=notes.strip(),
            active=True,
        )
    db.add(row)
    db.commit()
    _flash(request, "Saved interviewer availability.")
    return RedirectResponse(f"/mock-interviews/trainer-availability?staff_id={staff_id}", status_code=303)


@router.post("/mock-interviews/trainer-availability/{availability_id}/toggle")
def toggle_trainer_availability(
    availability_id: int,
    kind: str = Form("weekly"),
    staff_id: int = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not _can_manage_mock_interviews(user):
        raise PermissionDenied("Mock interview scheduling access is required.")
    model = TrainerAdhocAvailability if kind == "adhoc" else TrainerWeeklyAvailability
    row = db.get(model, availability_id)
    if row:
        row.active = not row.active
        db.add(row)
        db.commit()
    return RedirectResponse(f"/mock-interviews/trainer-availability?staff_id={staff_id}", status_code=303)


@router.get("/mock-interviews/consultant-availability", response_class=HTMLResponse)
def consultant_availability_page(
    request: Request,
    consultant_id: Optional[int] = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    consultant_profile = _consultant_profile_for_user(db, user) if _is_consultant_user(user) else None
    consultants = [consultant_profile] if consultant_profile else _active_consultants(db, visible_role_ids)
    selected_consultant_id = consultant_id or (consultants[0].id if consultants else None)
    if consultant_profile:
        selected_consultant_id = consultant_profile.id
    blocks = []
    if selected_consultant_id:
        blocks = db.scalars(select(ConsultantAvailabilityBlock).where(ConsultantAvailabilityBlock.consultant_id == selected_consultant_id).order_by(ConsultantAvailabilityBlock.start_at.desc())).all()
    return templates.TemplateResponse(
        "web/mock_interview_consultant_availability.html",
        {
            "request": request,
            "user": user,
            "consultants": consultants,
            "selected_consultant_id": selected_consultant_id,
            "blocks": blocks,
            "timezone_options": _timezone_options(),
            "can_manage_mock_interviews": _can_manage_mock_interviews(user),
        },
    )


@router.post("/mock-interviews/consultant-availability")
def save_consultant_availability(
    request: Request,
    consultant_id: int = Form(...),
    start_at: str = Form(""),
    end_at: str = Form(""),
    timezone_name: str = Form("America/Chicago"),
    reason: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    consultant = db.get(ConsultantProfile, consultant_id)
    if not consultant or (visible_role_ids is not None and consultant.marketing_role_id not in visible_role_ids):
        raise PermissionDenied("Consultant is outside your assigned marketing roles.")
    if not _can_manage_mock_interviews(user):
        raise PermissionDenied("Mock interview scheduling access is required.")
    row = ConsultantAvailabilityBlock(
        consultant_id=consultant_id,
        start_at=_parse_local_datetime(start_at) or datetime.now(timezone.utc),
        end_at=_parse_local_datetime(end_at) or datetime.now(timezone.utc),
        timezone=timezone_name,
        reason=reason.strip(),
        notes=notes.strip(),
        active=True,
        created_by_id=user.id,
    )
    db.add(row)
    db.commit()
    _flash(request, "Saved consultant availability block.")
    return RedirectResponse(f"/mock-interviews/consultant-availability?consultant_id={consultant_id}", status_code=303)


@router.post("/mock-interviews/consultant-availability/{block_id}/toggle")
def toggle_consultant_availability(
    block_id: int,
    consultant_id: int = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not _can_manage_mock_interviews(user):
        raise PermissionDenied("Mock interview scheduling access is required.")
    row = db.get(ConsultantAvailabilityBlock, block_id)
    if row:
        row.active = not row.active
        db.add(row)
        db.commit()
    return RedirectResponse(f"/mock-interviews/consultant-availability?consultant_id={consultant_id}", status_code=303)


@router.get("/mock-interviews/{mock_id}", response_class=HTMLResponse)
def mock_interview_detail(
    mock_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _mock_interview_for_user(db, user, mock_id)
    return templates.TemplateResponse(
        "web/mock_interview_detail.html",
        {
            "request": request,
            "user": user,
            "row": row,
            "events": db.scalars(select(MockInterviewStatusEvent).where(MockInterviewStatusEvent.mock_interview_id == row.id).order_by(MockInterviewStatusEvent.created_at.desc())).all(),
            "status_options": _mock_status_options(),
            "timezone_options": _timezone_options(),
            "round_type_options": _mock_round_type_options(),
            "assigned_staff_options": _mock_interview_staff_options(db, _visible_mock_marketing_role_ids(user)),
            "can_manage_mock_interviews": _can_manage_mock_interviews(user),
            "conflicts": _mock_interview_conflicts(db, row.consultant_id, row.assigned_staff_id, str(row.scheduled_on or ""), row.scheduled_time or "", row.duration_minutes or 60, exclude_mock_id=row.id),
        },
    )


@router.get("/mock-interviews/{mock_id}/edit", response_class=HTMLResponse)
def edit_mock_interview_form(
    mock_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not _can_manage_mock_interviews(user):
        raise PermissionDenied("Mock interview scheduling access is required.")
    row = _mock_interview_for_user(db, user, mock_id)
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    staff_options = _mock_interview_staff_options(db, visible_role_ids)
    return templates.TemplateResponse(
        "web/mock_interview_form.html",
        {
            "request": request,
            "user": user,
            "row": row,
            "form_title": f"Edit Mock Interview #{row.id}",
            "form_action": f"/mock-interviews/{row.id}/update",
            "submit_label": "Update Mock Interview",
            "consultants": _active_consultants(db, visible_role_ids),
            "marketing_roles": _visible_marketing_roles_for_user(db, user),
            "assigned_staff_options": staff_options,
            "training_programs": _visible_training_programs(db, visible_role_ids),
            "submissions": _recent_submissions(db, visible_role_ids),
            "timezone_options": _timezone_options(),
            "round_type_options": _mock_round_type_options(),
            "status_options": _mock_status_options(),
            "availability_by_staff": _availability_summary_by_staff(db, staff_options),
            "marketing_role_id": row.marketing_role_id,
            "conflicts": _mock_interview_conflicts(db, row.consultant_id, row.assigned_staff_id, str(row.scheduled_on or ""), row.scheduled_time or "", row.duration_minutes or 60, exclude_mock_id=row.id),
        },
    )


@router.post("/mock-interviews/{mock_id}/acknowledge")
def acknowledge_mock_interview(
    mock_id: int,
    request: Request,
    note: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _mock_interview_for_user(db, user, mock_id)
    from_status = row.status
    row.consultant_ack_status = "acknowledged"
    row.consultant_acknowledged_at = datetime.now(timezone.utc)
    row.consultant_acknowledged_by_id = user.id
    row.consultant_ack_note = note.strip()
    if row.status == MockInterviewStatus.PENDING_ACK.value:
        row.status = MockInterviewStatus.PLANNED.value
    _record_mock_event(db, row, user, "acknowledge", from_status, row.status, note.strip() or "Consultant acknowledged session.")
    db.add(row)
    db.commit()
    _flash(request, "Mock interview acknowledged.")
    return RedirectResponse(f"/mock-interviews/{mock_id}", status_code=303)


@router.post("/mock-interviews/{mock_id}/request")
def request_mock_interview_change(
    mock_id: int,
    request: Request,
    request_type: str = Form("reschedule"),
    note: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _mock_interview_for_user(db, user, mock_id)
    from_status = row.status
    row.request_note = note.strip()
    row.status = MockInterviewStatus.CANCELLATION_REQUESTED.value if request_type == "cancel" else MockInterviewStatus.RESCHEDULE_REQUESTED.value
    _record_mock_event(db, row, user, request_type, from_status, row.status, note.strip())
    db.add(row)
    db.commit()
    _flash(request, "Request saved.")
    return RedirectResponse(f"/mock-interviews/{mock_id}", status_code=303)


@router.post("/mock-interviews/{mock_id}/update")
def update_mock_interview(
    mock_id: int,
    request: Request,
    assigned_staff_id: Optional[int] = Form(None),
    scheduled_on: str = Form(""),
    scheduled_time: str = Form(""),
    timezone: str = Form("America/Chicago"),
    duration_minutes: int = Form(60),
    meeting_link: str = Form(""),
    round_type: str = Form("mock"),
    interviewer_name: str = Form(""),
    status: str = Form(MockInterviewStatus.PLANNED.value),
    score: int = Form(0),
    strengths: str = Form(""),
    gaps: str = Form(""),
    action_items: str = Form(""),
    question_coverage: str = Form(""),
    questions_asked: str = Form(""),
    prep_pack_snapshot: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not _can_manage_mock_interviews(user):
        raise PermissionDenied("Mock interview scheduling access is required.")
    row = db.get(MockInterview, mock_id)
    if not row:
        return RedirectResponse("/mock-interviews?error=Mock+interview+not+found", status_code=303)
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    if visible_role_ids is not None and row.marketing_role_id not in visible_role_ids:
        raise PermissionDenied("Mock interview is outside your assigned marketing roles.")
    assigned_staff = _assignable_staff_member(db, assigned_staff_id) if assigned_staff_id else None
    if assigned_staff_id and not assigned_staff:
        return RedirectResponse("/mock-interviews?error=Select+a+valid+assigned+staff+member", status_code=303)
    if assigned_staff and row.marketing_role_id and not _staff_assigned_to_marketing_role(assigned_staff, row.marketing_role_id):
        return RedirectResponse("/mock-interviews?error=Assigned+staff+must+be+assigned+to+the+mock+interview+marketing+role", status_code=303)

    from_status = row.status
    row.assigned_staff_id = assigned_staff.id if assigned_staff else None
    row.scheduled_on = _parse_date(scheduled_on)
    row.scheduled_time = scheduled_time.strip()
    row.timezone = timezone if timezone in _timezone_values() else "America/Chicago"
    row.duration_minutes = max(15, min(240, duration_minutes))
    row.meeting_link = meeting_link.strip()
    row.round_type = round_type.strip() if round_type in _mock_round_type_values() else "mock"
    row.interviewer_name = interviewer_name.strip()
    row.status = status if status in _mock_status_values() else MockInterviewStatus.PLANNED.value
    row.score = max(0, min(100, score))
    row.strengths = strengths.strip()
    row.gaps = gaps.strip()
    row.action_items = action_items.strip()
    row.question_coverage = question_coverage.strip()
    row.questions_asked = questions_asked.strip()
    row.prep_pack_snapshot = prep_pack_snapshot.strip()
    if from_status != row.status:
        _record_mock_event(db, row, user, "status_update", from_status, row.status, "Updated mock interview status.")
    else:
        _record_mock_event(db, row, user, "update", from_status, row.status, "Updated mock interview.")
    db.add(row)
    db.commit()
    _flash(request, "Updated mock interview.")
    return RedirectResponse("/mock-interviews", status_code=303)


@router.get("/marketing-roles/new", response_class=HTMLResponse)
def new_marketing_role_form(request: Request, error: str = "", user: User = Depends(require_manager), db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "web/marketing_role_form.html",
        {
            "request": request,
            "user": user,
            "role": None,
            "owner_options": _marketing_role_owner_options(db),
            "error": error,
        },
    )


@router.get("/marketing-roles/glossary", response_class=HTMLResponse)
def marketing_role_glossary(
    request: Request,
    q: str = "",
    category: str = "all",
    marketing_role: str = "all",
    sort: str = "term",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rows = list(MARKETING_ROLE_GLOSSARY)
    if q:
        value = q.strip().lower()
        rows = [row for row in rows if value in row["term"].lower() or value in row["meaning"].lower() or value in row["roles"].lower()]
    if category != "all":
        rows = [row for row in rows if row["category"] == category]
    if marketing_role != "all":
        rows = [row for row in rows if row["roles"] == marketing_role]
    sort_key = sort if sort in {"term", "category", "roles"} else "term"
    rows = sorted(rows, key=lambda row: row[sort_key].lower())
    total_rows = len(rows)
    pagination = _pagination_context(total_rows, page, per_page)
    page_rows = rows[pagination["offset"] : pagination["offset"] + per_page]
    params = {"q": q, "category": category, "marketing_role": marketing_role, "sort": sort, "per_page": per_page}
    roles_by_name = {role.name: role for role in db.scalars(select(MarketingRole)).all()}
    return templates.TemplateResponse(
        "web/marketing_role_glossary.html",
        {
            "request": request,
            "user": user,
            "terms": page_rows,
            "q": q,
            "category": category,
            "marketing_role": marketing_role,
            "sort": sort,
            "category_options": [("all", "All categories"), *[(name, name) for name in glossary_categories()]],
            "marketing_role_options": [("all", "All marketing roles"), *[(name, name) for name in glossary_roles()]],
            "sort_urls": _sort_urls("/marketing-roles/glossary", params, ["term", "category", "roles"]),
            "page_params": urlencode(params),
            "role_count": len(glossary_roles()),
            "glossary_total_count": len(MARKETING_ROLE_GLOSSARY),
            "roles_by_name": roles_by_name,
            "can_manage": has_permission(user, Permission.MANAGE_OPERATIONS),
            **pagination,
        },
    )


@router.get("/marketing-roles/glossary/{item_id}", response_class=HTMLResponse)
def marketing_role_glossary_detail(item_id: int, request: Request, user: User = Depends(require_user)):
    item = glossary_item(item_id)
    if not item:
        return RedirectResponse("/marketing-roles/glossary", status_code=303)
    return templates.TemplateResponse(
        "web/marketing_role_glossary_detail.html",
        {
            "request": request,
            "user": user,
            "item": item,
            "word_count": star_word_count(item),
        },
    )


@router.post("/marketing-roles")
def create_marketing_role(
    name: str = Form(...),
    code: str = Form(""),
    owner_id: Optional[int] = Form(None),
    description: str = Form(""),
    covers: str = Form(""),
    common_tools: str = Form(""),
    aliases: str = Form(""),
    keywords: str = Form(""),
    active: bool = Form(False),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    role = MarketingRole(
        name=name.strip(),
        code=_marketing_role_code(code, name),
        owner_id=_marketing_role_owner_id(db, owner_id),
        description=description.strip(),
        covers=covers.strip(),
        common_tools=common_tools.strip(),
        aliases=aliases.strip(),
        keywords=keywords.strip(),
        active=active,
    )
    db.add(role)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/marketing-roles/new?error=Role+code+already+exists", status_code=303)
    return RedirectResponse("/marketing-roles", status_code=303)


@router.get("/marketing-roles/{role_id}", response_class=HTMLResponse)
def marketing_role_detail(role_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    role = db.get(MarketingRole, role_id)
    if not role:
        return RedirectResponse("/marketing-roles", status_code=303)
    requirement_count = db.scalar(select(func.count(PursuitRequirement.id)).where(PursuitRequirement.marketing_role_id == role_id)) or 0
    training_program = db.scalar(select(TrainingProgram).where(TrainingProgram.marketing_role_id == role_id))
    return templates.TemplateResponse(
        "web/marketing_role_detail.html",
        {
            "request": request,
            "user": user,
            "role": role,
            "requirement_count": requirement_count,
            "training_program": training_program,
            "can_manage": has_permission(user, Permission.MANAGE_OPERATIONS),
        },
    )


@router.get("/marketing-roles/{role_id}/edit", response_class=HTMLResponse)
def edit_marketing_role_form(role_id: int, request: Request, error: str = "", user: User = Depends(require_manager), db: Session = Depends(get_db)):
    role = db.get(MarketingRole, role_id)
    if not role:
        return RedirectResponse("/marketing-roles", status_code=303)
    return templates.TemplateResponse(
        "web/marketing_role_form.html",
        {
            "request": request,
            "user": user,
            "role": role,
            "owner_options": _marketing_role_owner_options(db),
            "error": error,
        },
    )


@router.post("/marketing-roles/{role_id}")
def update_marketing_role(
    role_id: int,
    name: str = Form(...),
    code: str = Form(""),
    owner_id: Optional[int] = Form(None),
    description: str = Form(""),
    covers: str = Form(""),
    common_tools: str = Form(""),
    aliases: str = Form(""),
    keywords: str = Form(""),
    active: bool = Form(False),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    role = db.get(MarketingRole, role_id)
    if not role:
        return RedirectResponse("/marketing-roles", status_code=303)
    role.name = name.strip()
    role.code = _marketing_role_code(code, name)
    role.owner_id = _marketing_role_owner_id(db, owner_id)
    role.description = description.strip()
    role.covers = covers.strip()
    role.common_tools = common_tools.strip()
    role.aliases = aliases.strip()
    role.keywords = keywords.strip()
    role.active = active
    db.add(role)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(f"/marketing-roles/{role_id}/edit?error=Role+code+already+exists", status_code=303)
    return RedirectResponse("/marketing-roles", status_code=303)


@router.post("/marketing-roles/{role_id}/delete")
def delete_marketing_role(role_id: int, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    role = db.get(MarketingRole, role_id)
    if not role:
        return RedirectResponse("/marketing-roles", status_code=303)
    requirement_count = db.scalar(select(func.count(PursuitRequirement.id)).where(PursuitRequirement.marketing_role_id == role_id)) or 0
    if requirement_count:
        role.active = False
        db.add(role)
    else:
        db.delete(role)
    db.commit()
    return RedirectResponse("/marketing-roles", status_code=303)


@router.get("/training-programs", response_class=HTMLResponse)
def training_programs_page(
    request: Request,
    marketing_role_id: str = "",
    industry_domain: str = "all",
    q: str = "",
    status: str = "active",
    sort: str = "role",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    selected_marketing_role_id = _optional_query_int(marketing_role_id)
    consultant_role_id, consultant_domain = _consultant_training_scope(db, user)
    query = select(TrainingProgram).join(MarketingRole)
    if _is_consultant_user(user):
        query = query.where(
            TrainingProgram.active.is_(True),
            TrainingProgram.marketing_role_id == (consultant_role_id or -1),
            TrainingProgram.industry_domain == consultant_domain,
        )
        selected_marketing_role_id = consultant_role_id if consultant_role_id and consultant_role_id > 0 else None
        industry_domain = consultant_domain or "__no_domain__"
    if selected_marketing_role_id:
        query = query.where(TrainingProgram.marketing_role_id == selected_marketing_role_id)
    if industry_domain != "all":
        query = query.where(TrainingProgram.industry_domain == industry_domain)
    if status == "active":
        query = query.where(TrainingProgram.active.is_(True))
    elif status == "inactive":
        query = query.where(TrainingProgram.active.is_(False))
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(
            func.lower(TrainingProgram.title).like(pattern)
            | func.lower(TrainingProgram.short_description).like(pattern)
            | func.lower(TrainingProgram.enterprise_context).like(pattern)
            | func.lower(TrainingProgram.cloud_architecture_json).like(pattern)
            | func.lower(TrainingProgram.tools_and_technologies_json).like(pattern)
            | func.lower(TrainingProgram.key_deliverables_json).like(pattern)
        )
    sort_map = {
        "role": MarketingRole.name.asc(),
        "domain": TrainingProgram.industry_domain.asc(),
        "owner": User.name.asc(),
        "duration": TrainingProgram.duration_weeks.asc(),
        "updated": TrainingProgram.updated_at.desc(),
    }
    if sort == "owner":
        query = query.outerjoin(User, MarketingRole.owner_id == User.id)
    query = query.order_by(sort_map.get(sort, MarketingRole.name.asc()), TrainingProgram.id.asc())
    total_rows = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = _pagination_context(total_rows, page, per_page)
    programs = db.scalars(query.limit(per_page).offset(pagination["offset"])).all()
    jd_counts = dict(
        db.execute(
            select(TrainingJobDescription.program_id, func.count(TrainingJobDescription.id))
            .where(TrainingJobDescription.program_id.in_([program.id for program in programs] or [0]))
            .group_by(TrainingJobDescription.program_id)
        ).all()
    )
    params = {"marketing_role_id": selected_marketing_role_id or "", "industry_domain": industry_domain, "q": q, "status": status, "sort": sort, "per_page": per_page}
    return templates.TemplateResponse(
        "web/training_programs.html",
        {
            "request": request,
            "user": user,
            "programs": programs,
            "jd_counts": jd_counts,
            "marketing_roles": _visible_training_marketing_roles(db, user),
            "industry_domains": [consultant_domain] if _is_consultant_user(user) and consultant_domain else INDUSTRY_DOMAINS,
            "marketing_role_id": selected_marketing_role_id,
            "industry_domain": industry_domain,
            "q": q,
            "status": status,
            "status_options": [("active", "Active"), ("inactive", "Inactive"), ("all", "All")],
            "sort": sort,
            "sort_urls": _sort_urls("/training-programs", params, ["role", "domain", "owner", "duration", "updated"]),
            "page_params": urlencode(params),
            "can_manage": has_permission(user, Permission.MANAGE_OPERATIONS),
            "is_consultant_access": _is_consultant_user(user),
            **pagination,
        },
    )


@router.get("/training-basics", response_class=HTMLResponse)
def training_basics_page(request: Request, section: str = "overview", user: User = Depends(require_user), db: Session = Depends(get_db)):
    tabs = [
        {"key": "overview", "label": "Overview"},
        {"key": "schedule", "label": "12-Day Schedule"},
        {"key": "commands", "label": "Command Maps"},
        {"key": "modules", "label": "Modules"},
        {"key": "interview", "label": "Interview Questions"},
    ]
    selected_section = section if section in {item["key"] for item in tabs} else "overview"
    basics_modules = _training_basics_preparation_modules()
    consultant_profile = _consultant_profile_for_user(db, user) if _is_consultant_user(user) else None
    assigned_training_program = _consultant_training_program(db, consultant_profile) if consultant_profile and consultant_profile.training_plan_assigned else None
    return templates.TemplateResponse(
        "web/training_basics.html",
        {
            "request": request,
            "user": user,
            "selected_section": selected_section,
            "section_tabs": tabs,
            "basics_modules": basics_modules,
            "command_map_modules": [module for module in basics_modules if module.get("command_groups")],
            "course_overview": _training_basics_course_overview(),
            "devops_visual_reference": _training_basics_devops_visual_reference(),
            "cicd_security_pipeline_reference": _training_cicd_security_pipeline_reference(),
            "fourteen_day_plan": _training_basics_14_day_plan(),
            "five_six_year_questions": _training_basics_five_six_year_interview_questions(),
            "consultant_profile": consultant_profile,
            "assigned_training_program": assigned_training_program,
        },
    )


@router.post("/training-basics/complete")
def complete_training_basics(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    consultant = _consultant_profile_for_user(db, user)
    if not consultant:
        raise PermissionDenied("Basics completion is available only for consultant profiles.")
    consultant.basics_prep_complete = True
    program = _sync_basics_to_training_assignment(db, consultant, user)
    db.commit()
    if program:
        _flash(request, f"Basics completed. Assigned role/domain training: {program.title or program.industry_domain}.")
        return RedirectResponse(f"/training-programs/{program.id}", status_code=303)
    _flash(request, "Basics completed. Assign a marketing role and domain to connect the role/domain training program.", "warn")
    return RedirectResponse("/training-basics", status_code=303)


@router.get("/training-basics/export.pdf")
def training_basics_pdf_export(user: User = Depends(require_user)):
    title = "Mintel Basics Preparation Command Workbook"
    pdf_bytes = _simple_text_pdf(title, _training_basics_pdf_blocks(), program=None)
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="mintel-basics-preparation-command-workbook.pdf"'},
    )


@router.get("/training-basics/topics/{topic_number}", response_class=HTMLResponse)
def training_basics_topic_page(topic_number: int, request: Request, user: User = Depends(require_user)):
    modules = _training_basics_preparation_modules()
    if topic_number < 1 or topic_number > len(modules):
        return RedirectResponse("/training-basics", status_code=303)
    module = modules[topic_number - 1]
    plan = _training_basics_14_day_plan()
    day = plan[topic_number - 1] if topic_number <= len(plan) else None
    previous_topic = topic_number - 1 if topic_number > 1 else None
    next_topic = topic_number + 1 if topic_number < len(modules) else None
    return templates.TemplateResponse(
        "web/training_basics_topic.html",
        {
            "request": request,
            "user": user,
            "module": module,
            "day": day,
            "topic_number": topic_number,
            "topic_total": len(modules),
            "previous_topic": previous_topic,
            "next_topic": next_topic,
            "topic_sections": _training_basics_topic_sections(module, topic_number),
        },
    )


@router.get("/training-basics/topics/{topic_number}/sections/{section_key}", response_class=HTMLResponse)
def training_basics_topic_section_page(topic_number: int, section_key: str, request: Request, user: User = Depends(require_user)):
    modules = _training_basics_preparation_modules()
    if topic_number < 1 or topic_number > len(modules):
        return RedirectResponse("/training-basics", status_code=303)
    module = modules[topic_number - 1]
    sections = _training_basics_topic_sections(module, topic_number)
    section = next((item for item in sections if item["key"] == section_key), None)
    if not section:
        return RedirectResponse(f"/training-basics/topics/{topic_number}", status_code=303)
    return templates.TemplateResponse(
        "web/training_basics_topic_section.html",
        {
            "request": request,
            "user": user,
            "module": module,
            "section": section,
            "topic_number": topic_number,
            "topic_total": len(modules),
            "topic_sections": sections,
        },
    )


@router.get("/training-programs/{program_id}", response_class=HTMLResponse)
def training_program_detail(
    program_id: int,
    request: Request,
    section: str = "start",
    glossary_q: str = "",
    glossary_category: str = "all",
    glossary_sort: str = "term",
    glossary_page: int = Query(1, ge=1),
    glossary_per_page: int = Query(25, ge=10, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    program = db.get(TrainingProgram, program_id)
    if not program:
        return RedirectResponse("/training-programs", status_code=303)
    if not _can_access_training_program(program, db, user):
        raise PermissionDenied("This training program is outside the consultant's assigned marketing role and domain.")
    sections = _training_sections()
    section = section if section in {item["key"] for item in sections} else "overview"
    glossary_rows = list((program.cloud_architecture or {}).get("productGlossary", []))
    if glossary_q:
        value = glossary_q.strip().lower()
        glossary_rows = [
            row
            for row in glossary_rows
            if value in str(row.get("term", "")).lower()
            or value in str(row.get("category", "")).lower()
            or value in str(row.get("productMeaning", "")).lower()
            or value in str(row.get("consultantTalkTrack", "")).lower()
            or value in str(row.get("boundary", "")).lower()
        ]
    if glossary_category != "all":
        glossary_rows = [row for row in glossary_rows if row.get("category") == glossary_category]
    glossary_sort_key = glossary_sort if glossary_sort in {"term", "category"} else "term"
    glossary_rows = sorted(glossary_rows, key=lambda row: str(row.get(glossary_sort_key, "")).lower())
    glossary_total_rows = len(glossary_rows)
    glossary_pagination = _pagination_context(glossary_total_rows, glossary_page, glossary_per_page)
    glossary_page_rows = glossary_rows[glossary_pagination["offset"] : glossary_pagination["offset"] + glossary_per_page]
    glossary_params = {
        "section": "glossary",
        "glossary_q": glossary_q,
        "glossary_category": glossary_category,
        "glossary_sort": glossary_sort,
        "glossary_per_page": glossary_per_page,
    }
    glossary_categories = sorted({str(row.get("category", "Core Term")) for row in (program.cloud_architecture or {}).get("productGlossary", []) if row.get("category")})
    return templates.TemplateResponse(
        "web/training_program_detail.html",
        {
            "request": request,
            "user": user,
            "program": program,
            "section": section,
            "sections": sections,
            "job_descriptions": sorted(program.job_descriptions, key=lambda item: item.sequence),
            "role_terms": _training_role_terms(program.marketing_role.name),
            "beginner_cards": _training_beginner_cards(program),
            "beginner_story_steps": _training_beginner_story_steps(program),
            "beginner_quiz": _training_beginner_quiz(program),
            "architecture_diagrams": _training_architecture_diagram_cards(program),
            "provider_diagrams": _training_provider_diagram_cards(program),
            "document_diagrams": _training_document_diagram_workbook(program),
            "microsoft_healthcare_references": _microsoft_healthcare_customer_story_references(program),
            "provider_usecase_sources": _training_provider_usecase_sources(program),
            "concept_cards": _training_concept_cards(program),
            "concept_coverage_map": _training_concept_coverage_map(program),
            "usecase_cards": _training_usecase_cards(program),
            "interview_banks": _training_interview_banks(program),
            "weekly_plan": _training_weekly_plan(program),
            "lab_cards": _training_lab_cards(program),
            "resume_bullets": _training_resume_bullets(program),
            "readiness_rows": _training_readiness_rows(),
            "onboarding_assessment": _training_onboarding_assessment(program),
            "cicd_security_pipeline_reference": _training_cicd_security_pipeline_reference() if program.marketing_role.name == "DevOps Engineer" else None,
            "product_system_links": product_system_link_map(program.application_landscape),
            "product_system_slugs": product_system_slug_lookup(program.application_landscape),
            "product_system_cards": product_system_cards(program.application_landscape),
            "glossary_terms": glossary_page_rows,
            "glossary_q": glossary_q,
            "glossary_category": glossary_category,
            "glossary_sort": glossary_sort,
            "glossary_category_options": [("all", "All categories"), *[(name, name) for name in glossary_categories]],
            "glossary_sort_urls": _named_sort_urls(f"/training-programs/{program.id}", glossary_params, "glossary_sort", ["term", "category"]),
            "glossary_page_params": urlencode(glossary_params),
            "glossary_total_rows": glossary_total_rows,
            "glossary_row_start": glossary_pagination["row_start"],
            "glossary_row_end": glossary_pagination["row_end"],
            "glossary_pages": glossary_pagination["pages"],
            "glossary_current_page": glossary_pagination["page"],
            "glossary_per_page": glossary_per_page,
            "can_manage": has_permission(user, Permission.MANAGE_OPERATIONS),
            "diagram_export_options": _training_diagram_export_options(program),
        },
    )


@router.get("/training-programs/{program_id}/export.pdf")
def training_program_pdf_export(
    program_id: int,
    include_diagrams: bool = Query(True),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    program = db.get(TrainingProgram, program_id)
    if not program:
        return RedirectResponse("/training-programs", status_code=303)
    if not _can_access_training_program(program, db, user):
        raise PermissionDenied("This training program is outside the consultant's assigned marketing role and domain.")
    title = f"{program.industry_domain} - {program.marketing_role.name} Training Program"
    filename = _pdf_filename(title)
    pdf_bytes = _simple_text_pdf(title, _training_program_pdf_blocks(program, include_diagrams=include_diagrams), program=program)
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/training-programs/{program_id}/systems/{system_slug}", response_class=HTMLResponse)
def training_program_product_system_detail(program_id: int, system_slug: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    program = db.get(TrainingProgram, program_id)
    if not program:
        return RedirectResponse("/training-programs", status_code=303)
    if not _can_access_training_program(program, db, user):
        raise PermissionDenied("This training program is outside the consultant's assigned marketing role and domain.")
    system = product_system_detail(system_slug, program.marketing_role.name, program.industry_domain)
    if not system or system["name"] not in program.application_landscape:
        return RedirectResponse(f"/training-programs/{program.id}?section=architecture", status_code=303)
    return templates.TemplateResponse(
        "web/training_program_system.html",
        {
            "request": request,
            "user": user,
            "program": program,
            "system": system,
            "product_system_links": product_system_link_map(program.application_landscape),
            "product_system_cards": product_system_cards(program.application_landscape),
            "can_manage": has_permission(user, Permission.MANAGE_OPERATIONS),
        },
    )


@router.get("/training-programs/{program_id}/usecases/{usecase_index}", response_class=HTMLResponse)
def training_program_usecase_detail(program_id: int, usecase_index: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    program = db.get(TrainingProgram, program_id)
    if not program:
        return RedirectResponse("/training-programs", status_code=303)
    if not _can_access_training_program(program, db, user):
        raise PermissionDenied("This training program is outside the consultant's assigned marketing role and domain.")
    use_cases = program.cloud_architecture.get("deliveredUseCases", [])
    if usecase_index < 1 or usecase_index > len(use_cases):
        return RedirectResponse(f"/training-programs/{program.id}?section=usecases", status_code=303)
    usecase = use_cases[usecase_index - 1]
    return templates.TemplateResponse(
        "web/training_program_usecase.html",
        {
            "request": request,
            "user": user,
            "program": program,
            "usecase": usecase,
            "usecase_index": usecase_index,
            "can_manage": has_permission(user, Permission.MANAGE_OPERATIONS),
        },
    )


@router.get("/training-programs/{program_id}/edit", response_class=HTMLResponse)
def edit_training_program_form(program_id: int, request: Request, error: str = "", user: User = Depends(require_manager), db: Session = Depends(get_db)):
    program = db.get(TrainingProgram, program_id)
    if not program:
        return RedirectResponse("/training-programs", status_code=303)
    return templates.TemplateResponse(
        "web/training_program_form.html",
        {
            "request": request,
            "user": user,
            "program": program,
            "job_descriptions": sorted(program.job_descriptions, key=lambda item: item.sequence),
            "error": error,
        },
    )


@router.post("/training-programs/{program_id}")
def update_training_program(
    program_id: int,
    title: str = Form(...),
    duration_weeks: int = Form(6),
    target_audience: str = Form(""),
    outcome: str = Form(""),
    vocabulary_plan: str = Form(""),
    concepts_plan: str = Form(""),
    usecases_plan: str = Form(""),
    interview_plan: str = Form(""),
    resume_plan: str = Form(""),
    labs_plan: str = Form(""),
    readiness_checklist: str = Form(""),
    missing_areas: str = Form(""),
    active: bool = Form(False),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    program = db.get(TrainingProgram, program_id)
    if not program:
        return RedirectResponse("/training-programs", status_code=303)
    program.title = title.strip()
    program.duration_weeks = max(1, min(duration_weeks, 52))
    program.target_audience = target_audience.strip()
    program.outcome = outcome.strip()
    program.vocabulary_plan = vocabulary_plan.strip()
    program.concepts_plan = concepts_plan.strip()
    program.usecases_plan = usecases_plan.strip()
    program.interview_plan = interview_plan.strip()
    program.resume_plan = resume_plan.strip()
    program.labs_plan = labs_plan.strip()
    program.readiness_checklist = readiness_checklist.strip()
    program.missing_areas = missing_areas.strip()
    program.active = active
    db.add(program)
    db.commit()
    return RedirectResponse(f"/training-programs/{program.id}", status_code=303)


@router.get("/staff", response_class=HTMLResponse)
def staff_page(
    request: Request,
    q: str = "",
    role: str = "all",
    status: str = "active",
    sort: str = "name",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    error: str = "",
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(User)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(
            func.lower(User.name).like(pattern)
            | func.lower(User.email).like(pattern)
            | func.lower(User.username).like(pattern)
            | func.lower(User.first_name).like(pattern)
            | func.lower(User.last_name).like(pattern)
        )
    if role != "all":
        query = query.where(User.role == role)
    if status == "active":
        query = query.where(User.active.is_(True))
    elif status == "inactive":
        query = query.where(User.active.is_(False))
    sort_map = {
        "name": User.name.asc(),
        "email": User.email.asc(),
        "username": User.username.asc(),
        "role": User.role.asc(),
        "status": User.active.desc(),
        "updated": User.updated_at.desc(),
    }
    query = query.order_by(sort_map.get(sort, User.name.asc()), User.email.asc())
    total_rows = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = _pagination_context(total_rows, page, per_page)
    staff = db.scalars(query.limit(per_page).offset(pagination["offset"])).all()
    params = {"q": q, "role": role, "status": status, "sort": sort, "per_page": per_page}
    return templates.TemplateResponse(
        "web/staff.html",
        {
            "request": request,
            "user": user,
            "staff": staff,
            "q": q,
            "role": role,
            "status": status,
            "sort": sort,
            "role_options": _staff_role_options(include_all=True),
            "status_options": [("active", "Active"), ("inactive", "Inactive"), ("all", "All")],
            "sort_urls": _sort_urls("/staff", params, ["name", "email", "username", "role", "status", "updated"]),
            "page_params": urlencode(params),
            "error": error,
            **pagination,
        },
    )


@router.get("/staff/access", response_class=HTMLResponse)
def staff_access_matrix(request: Request, user: User = Depends(require_admin)):
    permissions = list(Permission)
    role_options = _staff_role_options()
    role_permissions = {role: ROLE_PERMISSIONS.get(role, set()) for role, _label in role_options}
    return templates.TemplateResponse(
        "web/staff_access.html",
        {
            "request": request,
            "user": user,
            "permissions": permissions,
            "role_options": role_options,
            "role_permissions": role_permissions,
        },
    )


@router.get("/staff/new", response_class=HTMLResponse)
def new_staff_form(request: Request, error: str = "", user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "web/staff_form.html",
        {
            "request": request,
            "user": user,
            "staff_member": None,
            "role_options": _staff_role_options(),
            "timezone_options": _timezone_options(),
            "regions": db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all(),
            "marketing_roles": db.scalars(select(MarketingRole).where(MarketingRole.active.is_(True)).order_by(MarketingRole.name)).all(),
            "assigned_region_ids": set(),
            "assigned_role_ids": set(),
            "error": error,
        },
    )


@router.post("/staff")
def create_staff(
    first_name: str = Form(""),
    last_name: str = Form(""),
    username: str = Form(...),
    email: str = Form(...),
    role: str = Form(UserRole.REGIONAL_STAFF.value),
    password: str = Form(...),
    password_confirm: str = Form(...),
    timezone: str = Form("America/Chicago"),
    region_ids: Optional[list[int]] = Form(None),
    marketing_role_ids: Optional[list[int]] = Form(None),
    active: bool = Form(False),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    normalized_email = _normalize_email(email)
    normalized_username = _normalize_username(username)
    if not normalized_email or not normalized_username or not password.strip():
        return RedirectResponse("/staff/new?error=Username,+email,+and+password+are+required", status_code=303)
    if password.strip() != password_confirm.strip():
        return RedirectResponse("/staff/new?error=Password+confirmation+does+not+match", status_code=303)
    if role not in _staff_role_values():
        role = UserRole.REGIONAL_STAFF.value
    staff_member = User(
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        name=_staff_display_name(first_name, last_name, normalized_email),
        username=normalized_username,
        email=normalized_email,
        role=role,
        timezone=timezone if timezone in _timezone_values() else "America/Chicago",
        password_hash=hash_password(password.strip()),
        active=active,
    )
    db.add(staff_member)
    try:
        db.flush()
        _save_staff_assignments(db, staff_member.id, region_ids, marketing_role_ids)
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/staff/new?error=Staff+email+or+username+already+exists", status_code=303)
    return RedirectResponse(f"/staff/{staff_member.id}", status_code=303)


@router.get("/staff/{staff_id}", response_class=HTMLResponse)
def staff_detail(staff_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    staff_member = db.get(User, staff_id)
    if not staff_member:
        return RedirectResponse("/staff", status_code=303)
    return templates.TemplateResponse(
        "web/staff_detail.html",
        {
            "request": request,
            "user": user,
            "staff_member": staff_member,
            "assigned_regions": _active_region_assignments(staff_member),
            "assigned_marketing_roles": _active_marketing_role_assignments(staff_member),
            "assigned_region_groups": _active_region_group_memberships(staff_member),
        },
    )


@router.get("/staff/{staff_id}/edit", response_class=HTMLResponse)
def edit_staff_form(staff_id: int, request: Request, error: str = "", user: User = Depends(require_admin), db: Session = Depends(get_db)):
    staff_member = db.get(User, staff_id)
    if not staff_member:
        return RedirectResponse("/staff", status_code=303)
    assigned_region_ids = {assignment.region_id for assignment in staff_member.region_assignments if assignment.active}
    assigned_role_ids = {assignment.marketing_role_id for assignment in staff_member.marketing_role_assignments if assignment.active}
    return templates.TemplateResponse(
        "web/staff_form.html",
        {
            "request": request,
            "user": user,
            "staff_member": staff_member,
            "role_options": _staff_role_options(),
            "timezone_options": _timezone_options(),
            "regions": db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all(),
            "marketing_roles": db.scalars(select(MarketingRole).where(MarketingRole.active.is_(True)).order_by(MarketingRole.name)).all(),
            "assigned_region_ids": assigned_region_ids,
            "assigned_role_ids": assigned_role_ids,
            "error": error,
        },
    )


@router.post("/staff/{staff_id}")
def update_staff(
    staff_id: int,
    first_name: str = Form(""),
    last_name: str = Form(""),
    username: str = Form(...),
    email: str = Form(...),
    role: str = Form(UserRole.REGIONAL_STAFF.value),
    password: str = Form(""),
    password_confirm: str = Form(""),
    timezone: str = Form("America/Chicago"),
    region_ids: Optional[list[int]] = Form(None),
    marketing_role_ids: Optional[list[int]] = Form(None),
    active: bool = Form(False),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    staff_member = db.get(User, staff_id)
    if not staff_member:
        return RedirectResponse("/staff", status_code=303)
    normalized_email = _normalize_email(email)
    normalized_username = _normalize_username(username)
    if not normalized_email or not normalized_username:
        return RedirectResponse(f"/staff/{staff_id}/edit?error=Username+and+email+are+required", status_code=303)
    if password.strip() or password_confirm.strip():
        if password.strip() != password_confirm.strip():
            return RedirectResponse(f"/staff/{staff_id}/edit?error=Password+confirmation+does+not+match", status_code=303)
    if role not in _staff_role_values():
        role = UserRole.REGIONAL_STAFF.value
    staff_member.first_name = first_name.strip()
    staff_member.last_name = last_name.strip()
    staff_member.name = _staff_display_name(first_name, last_name, normalized_email)
    staff_member.username = normalized_username
    staff_member.email = normalized_email
    staff_member.role = role
    staff_member.timezone = timezone if timezone in _timezone_values() else "America/Chicago"
    staff_member.active = True if staff_member.id == user.id else active
    if password.strip():
        staff_member.password_hash = hash_password(password.strip())
    db.add(staff_member)
    try:
        _save_staff_assignments(db, staff_member.id, region_ids, marketing_role_ids)
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(f"/staff/{staff_id}/edit?error=Staff+email+or+username+already+exists", status_code=303)
    return RedirectResponse(f"/staff/{staff_id}", status_code=303)


@router.post("/staff/{staff_id}/delete")
def delete_staff(staff_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    staff_member = db.get(User, staff_id)
    if not staff_member:
        return RedirectResponse("/staff", status_code=303)
    if staff_member.id == user.id:
        return RedirectResponse("/staff?error=You+cannot+deactivate+or+delete+your+own+account", status_code=303)
    if staff_member.active:
        staff_member.active = False
        db.add(staff_member)
    else:
        db.delete(staff_member)
    db.commit()
    return RedirectResponse("/staff", status_code=303)


@router.get("/staff-assignments", response_class=HTMLResponse)
def staff_assignments_page(
    request: Request,
    sort: str = "name",
    status: str = "active",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(User).where(User.role.in_(_assignable_staff_roles()))
    if status == "active":
        query = query.where(User.active.is_(True))
    elif status == "inactive":
        query = query.where(User.active.is_(False))
    sort_map = {
        "name": User.name.asc(),
        "email": User.email.asc(),
        "role": User.role.asc(),
        "status": User.active.desc(),
        "updated": User.updated_at.desc(),
    }
    query = query.order_by(sort_map.get(sort, User.name.asc()), User.email.asc())
    total_rows = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = _pagination_context(total_rows, page, per_page)
    staff = db.scalars(query.limit(per_page).offset(pagination["offset"])).all()
    params = {"sort": sort, "status": status, "per_page": per_page}
    return templates.TemplateResponse(
        "web/staff_assignments.html",
        {
            "request": request,
            "user": user,
            "staff": staff,
            "sort": sort,
            "status": status,
            "status_options": [("active", "Active"), ("inactive", "Inactive"), ("all", "All")],
            "sort_urls": _sort_urls("/staff-assignments", params, ["name", "email", "role", "status", "updated"]),
            "page_params": urlencode(params),
            **pagination,
        },
    )


@router.get("/staff-assignments/{staff_id}", response_class=HTMLResponse)
def staff_assignment_detail(staff_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    staff_member = _assignable_staff_member(db, staff_id)
    if not staff_member:
        return RedirectResponse("/staff-assignments", status_code=303)
    return templates.TemplateResponse(
        "web/staff_assignment_detail.html",
        {
            "request": request,
            "user": user,
            "staff_member": staff_member,
            "assigned_regions": _active_region_assignments(staff_member),
            "assigned_marketing_roles": _active_marketing_role_assignments(staff_member),
            "assigned_region_groups": _active_region_group_memberships(staff_member),
        },
    )


@router.get("/staff-assignments/{staff_id}/edit", response_class=HTMLResponse)
def edit_staff_assignment_form(staff_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    staff_member = _assignable_staff_member(db, staff_id)
    if not staff_member:
        return RedirectResponse("/staff-assignments", status_code=303)
    assigned_region_ids = {assignment.region_id for assignment in staff_member.region_assignments if assignment.active}
    assigned_role_ids = {assignment.marketing_role_id for assignment in staff_member.marketing_role_assignments if assignment.active}
    return templates.TemplateResponse(
        "web/staff_assignment_form.html",
        {
            "request": request,
            "user": user,
            "staff_member": staff_member,
            "regions": db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all(),
            "marketing_roles": db.scalars(select(MarketingRole).where(MarketingRole.active.is_(True)).order_by(MarketingRole.name)).all(),
            "assigned_region_ids": assigned_region_ids,
            "assigned_role_ids": assigned_role_ids,
        },
    )


@router.post("/staff-assignments/{staff_id}")
def update_staff_assignment(
    staff_id: int,
    region_ids: Optional[list[int]] = Form(None),
    marketing_role_ids: Optional[list[int]] = Form(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    staff_member = _assignable_staff_member(db, staff_id)
    if not staff_member:
        return RedirectResponse("/staff-assignments", status_code=303)
    selected_region_ids = set(region_ids or [])
    selected_role_ids = set(marketing_role_ids or [])
    _save_staff_assignments(db, staff_id, list(selected_region_ids), list(selected_role_ids))
    db.commit()
    return RedirectResponse(f"/staff-assignments/{staff_id}", status_code=303)


@router.post("/pursuits/{pursuit_id}/assign")
def assign_pursuit(
    request: Request,
    pursuit_id: int,
    region_id: Optional[int] = Form(None),
    owner_user_id: Optional[int] = Form(None),
    assigned_staff_name: str = Form(""),
    assigned_staff_email: str = Form(""),
    status: str = Form(PursuitStatus.ASSIGNED.value),
    priority: int = Form(0),
    next_action: str = Form(""),
    next_follow_up_date: Optional[date] = Form(None),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_view_pursuit(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        pursuit.region_id = region_id
        owner = _pursuit_owner_from_form(db, owner_user_id, region_id)
        if owner_user_id and not owner:
            _flash(request, "Selected owner is not active or is not assigned to that region.", "error")
            return RedirectResponse("/pursuits", status_code=303)
        if owner:
            _assign_pursuit_owner(pursuit, owner)
        else:
            pursuit.assigned_staff_name = assigned_staff_name.strip()
            pursuit.assigned_staff_email = assigned_staff_email.strip()
        pursuit.status = status
        pursuit.priority = priority
        pursuit.next_action = next_action
        pursuit.next_follow_up_date = next_follow_up_date
        activity(db, pursuit.id, user.email, "assignment_updated", "Updated assignment, status, priority, or next action")
        db.add(pursuit)
        db.commit()
        _flash(request, f"Updated assignment for {pursuit.company.name}.")
    return RedirectResponse("/pursuits", status_code=303)


@router.post("/pursuits/{pursuit_id}/quick-status")
def quick_update_pursuit_status(
    pursuit_id: int,
    status: str = Form(...),
    next: str = Form("/pursuits"),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_view_pursuit(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        allowed = {item["value"] for item in _pursuit_status_options()}
        if status in allowed:
            pursuit.status = status
            activity(db, pursuit.id, user.email, "status_updated", f"Updated status to {status}")
            db.add(pursuit)
            db.commit()
    return RedirectResponse(next if next.startswith("/") else "/pursuits", status_code=303)


@router.get("/pursuits/{pursuit_id}", response_class=HTMLResponse)
def pursuit_detail(
    pursuit_id: int,
    request: Request,
    tab: str = "profile",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if not pursuit:
        return RedirectResponse("/pursuits", status_code=303)
    if not _can_view_pursuit(user, pursuit):
        raise PermissionDenied("This company belongs to another region group.")
    company_context = _company_uscis_context(db, pursuit.company)
    research_prompt = pursuit.research_prompt or build_company_research_prompt(pursuit.company, company_context)
    can_edit_pursuit = _can_edit_pursuit_workspace(user, pursuit)
    structured = structured_context(db, pursuit.id)
    company_jobs = db.scalars(select(JobOpportunity).where(JobOpportunity.company_id == pursuit.company_id).order_by(JobOpportunity.active.desc(), JobOpportunity.updated_at.desc())).all()
    decision_readiness = decision_readiness_context(pursuit, company_context, structured, company_jobs)
    pursue_gate = company_pursue_context(pursuit, decision_readiness, company_jobs)
    tech_stack_context = consolidated_tech_stack_context(structured)
    job_posting_context = job_posting_review_context(structured)
    checklist = _pursuit_research_checklist(pursuit, structured, company_jobs, decision_readiness)
    return templates.TemplateResponse(
        "web/pursuit_detail.html",
        {
            "request": request,
            "user": user,
            "pursuit": pursuit,
            "company": pursuit.company,
            "regions": db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all(),
            "owner_options": _staff_options_for_region(db, pursuit.region_id),
            "statuses": _pursuit_status_options(),
            "company_context": company_context,
            "structured": structured,
            "marketing_roles": db.scalars(select(MarketingRole).where(MarketingRole.active.is_(True)).order_by(MarketingRole.name)).all(),
            "research_prompt": research_prompt,
            "active_tab": tab,
            "tabs": _pursuit_tabs(),
            "can_edit_pursuit": can_edit_pursuit,
            "can_assign_pursuit": has_permission(user, Permission.ASSIGN_PURSUITS),
            "can_run_research": can_edit_pursuit,
            "research_checklist": checklist,
            "research_completion": _checklist_completion(checklist),
            "pinned_notes": [note for note in structured["notes"] if note.pinned],
            "company_jobs": company_jobs,
            "decision_readiness": decision_readiness,
            "pursue_gate": pursue_gate,
            "tech_stack_context": tech_stack_context,
            "job_posting_context": job_posting_context,
            "ats_type_options": _company_ats_type_options(),
            "company_signal_options": _company_signal_options(),
            "opt_risk_options": _company_opt_risk_options(),
            "company_owner_options": _assignable_staff_options(db),
        },
    )


@router.post("/pursuits/{pursuit_id}/company-profile")
def save_pursuit_company_profile(
    request: Request,
    pursuit_id: int,
    website: str = Form(""),
    linkedin_url: str = Form(""),
    careers_url: str = Form(""),
    ats_api_url: str = Form(""),
    ats_type: str = Form(""),
    ats_platform: str = Form(""),
    location: str = Form(""),
    industry: str = Form(""),
    headquarters_city: str = Form(""),
    headquarters_state: str = Form(""),
    managed_by_id: Optional[int] = Form(None),
    application_time_minutes: int = Form(0),
    requires_account_creation: bool = Form(False),
    requires_email_verification: bool = Form(False),
    accepts_cover_letter: bool = Form(False),
    onsite_interview_required: bool = Form(False),
    opt_status: str = Form("unknown"),
    stem_opt_status: str = Form("unknown"),
    sponsorship_status: str = Form("unknown"),
    opt_risk: str = Form("low"),
    opt_recent_hires: int = Form(0),
    h1b_filings_recent: int = Form(0),
    opt_last_verified: str = Form(""),
    opt_notes: str = Form(""),
    tech_stack: str = Form(""),
    background_process: str = Form(""),
    submission_guidance: str = Form(""),
    company_notes: str = Form(""),
    source_url: str = Form(""),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        company = pursuit.company
        company.website = website.strip()
        company.linkedin_url = linkedin_url.strip()
        company.careers_url = careers_url.strip()
        company.ats_api_url = ats_api_url.strip()
        company.ats_type = ats_type if ats_type in _company_ats_type_values() else ""
        company.ats_platform = ats_platform.strip()
        company.location = location.strip()
        company.industry = industry.strip()
        company.headquarters_city = headquarters_city.strip()
        company.headquarters_state = headquarters_state.strip()
        company.managed_by_id = _marketing_role_owner_id(db, managed_by_id)
        company.application_time_minutes = max(0, application_time_minutes)
        company.requires_account_creation = requires_account_creation
        company.requires_email_verification = requires_email_verification
        company.accepts_cover_letter = accepts_cover_letter
        company.onsite_interview_required = onsite_interview_required
        company.opt_status = opt_status if opt_status in _company_signal_values() else "unknown"
        company.stem_opt_status = stem_opt_status if stem_opt_status in _company_signal_values() else "unknown"
        company.sponsorship_status = sponsorship_status if sponsorship_status in _company_signal_values() else "unknown"
        company.opt_risk = opt_risk if opt_risk in _company_opt_risk_values() else "low"
        company.opt_recent_hires = max(0, opt_recent_hires)
        company.h1b_filings_recent = max(0, h1b_filings_recent)
        company.opt_last_verified = _parse_date(opt_last_verified)
        company.opt_notes = opt_notes.strip()
        company.tech_stack = tech_stack.strip()
        company.background_process = background_process.strip()
        company.submission_guidance = submission_guidance.strip()
        company.notes = company_notes.strip()
        db.add(company)
        if source_url.strip():
            db.add(PursuitEvidence(pursuit_id=pursuit_id, kind="company_profile", label="Company profile source", url=source_url.strip(), confidence="medium"))
        activity(db, pursuit_id, user.email, "company_profile_saved", "Updated company profile fields")
        db.commit()
        _flash(request, "Saved company profile.")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=profile", status_code=303)


@router.post("/pursuits/{pursuit_id}/research")
def save_pursuit_research(
    request: Request,
    pursuit_id: int,
    status: str = Form(PursuitStatus.PROMOTED.value),
    priority: int = Form(0),
    region_id: Optional[int] = Form(None),
    owner_user_id: Optional[int] = Form(None),
    assigned_staff_name: str = Form(""),
    assigned_staff_email: str = Form(""),
    next_action: str = Form(""),
    next_follow_up_date: Optional[date] = Form(None),
    closing_probability: int = Form(0),
    decision: str = Form(""),
    pursuit_reason: str = Form(""),
    research_summary: str = Form(""),
    recent_requirements: str = Form(""),
    technology_stack: str = Form(""),
    submission_intelligence: str = Form(""),
    company_contacts: str = Form(""),
    prime_vendors: str = Form(""),
    c2c_managers: str = Form(""),
    research_prompt: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        if has_permission(user, Permission.ASSIGN_PURSUITS):
            pursuit.status = status
            pursuit.priority = priority
            pursuit.region_id = region_id
            owner = _pursuit_owner_from_form(db, owner_user_id, region_id)
            if owner_user_id and not owner:
                _flash(request, "Selected owner is not active or is not assigned to that region.", "error")
                return RedirectResponse(f"/pursuits/{pursuit_id}", status_code=303)
            if owner:
                _assign_pursuit_owner(pursuit, owner)
            else:
                pursuit.assigned_staff_name = assigned_staff_name.strip()
                pursuit.assigned_staff_email = assigned_staff_email.strip()
            pursuit.next_action = next_action
            pursuit.next_follow_up_date = next_follow_up_date
            pursuit.closing_probability = max(0, min(closing_probability, 100))
            if decision == "pursue":
                structured = structured_context(db, pursuit.id)
                company_jobs = db.scalars(select(JobOpportunity).where(JobOpportunity.company_id == pursuit.company_id).order_by(JobOpportunity.active.desc(), JobOpportunity.updated_at.desc())).all()
                decision_readiness = decision_readiness_context(pursuit, _company_uscis_context(db, pursuit.company), structured, company_jobs)
                pursue_gate = company_pursue_context(pursuit, decision_readiness, company_jobs)
                if not pursue_gate["ready"]:
                    _flash(request, "Pursue Now requires: " + ", ".join(pursue_gate["missing_labels"]), "error")
                    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=workflow", status_code=303)
            pursuit.decision = decision
            pursuit.pursuit_reason = pursuit_reason
        pursuit.research_summary = research_summary
        pursuit.recent_requirements = recent_requirements
        pursuit.technology_stack = technology_stack
        pursuit.submission_intelligence = submission_intelligence
        pursuit.company_contacts = company_contacts
        pursuit.prime_vendors = prime_vendors
        pursuit.c2c_managers = c2c_managers
        pursuit.research_prompt = research_prompt
        pursuit.notes = notes
        activity(db, pursuit.id, user.email, "workspace_saved", "Saved pursuit workspace fields")
        db.add(pursuit)
        db.commit()
        _flash(request, "Saved pursuit workspace.")
    return RedirectResponse(f"/pursuits/{pursuit_id}", status_code=303)


@router.post("/pursuits/{pursuit_id}/requirements")
def add_pursuit_requirement(
    request: Request,
    pursuit_id: int,
    title: str = Form(""),
    location: str = Form(""),
    posted_or_seen_date: str = Form(""),
    employment_type: str = Form(""),
    technologies: str = Form(""),
    work_auth_language: str = Form(""),
    marketing_role_id: Optional[int] = Form(None),
    source_url: str = Form(""),
    confidence: str = Form(""),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        role = db.get(MarketingRole, marketing_role_id) if marketing_role_id else classify_marketing_role(db, f"{title} {technologies} {work_auth_language}")
        db.add(PursuitRequirement(pursuit_id=pursuit_id, marketing_role_id=role.id if role else None, title=title, location=location, posted_or_seen_date=posted_or_seen_date, employment_type=employment_type, technologies=technologies, work_auth_language=work_auth_language, source_url=source_url, confidence=confidence))
        if source_url:
            db.add(PursuitEvidence(pursuit_id=pursuit_id, kind="requirement", label=title, url=source_url, confidence=confidence))
        activity(db, pursuit_id, user.email, "requirement_added", title or "Added requirement")
        db.commit()
        _flash(request, "Added requirement.")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=job-postings", status_code=303)


@router.post("/pursuits/{pursuit_id}/requirements/{requirement_id}/create-job")
def create_job_from_pursuit_requirement(
    request: Request,
    pursuit_id: int,
    requirement_id: int,
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    requirement = db.get(PursuitRequirement, requirement_id)
    if pursuit and requirement and requirement.pursuit_id == pursuit_id:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        duplicate = db.scalar(
            select(JobOpportunity).where(
                JobOpportunity.company_id == pursuit.company_id,
                func.lower(JobOpportunity.title) == requirement.title.strip().lower(),
                JobOpportunity.url == requirement.source_url.strip(),
            )
        )
        if duplicate:
            _flash(request, f"Job already exists for {requirement.title}.", "warn")
            return RedirectResponse(f"/jobs/{duplicate.id}", status_code=303)
        job = JobOpportunity(
            company_id=pursuit.company_id,
            title=requirement.title.strip() or "Open requirement",
            requirement_key=f"PURSUIT-{pursuit_id}-REQ-{requirement.id}",
            marketing_role_ids=str(requirement.marketing_role_id or ""),
            location=requirement.location.strip(),
            source=JobSource.STAFF_MANUAL.value,
            source_type=JobSource.STAFF_MANUAL.value,
            url=requirement.source_url.strip(),
            description=" ".join(part for part in [requirement.title, requirement.technologies, requirement.employment_type, requirement.work_auth_language, pursuit.research_summary] if part).strip(),
            sponsorship_notes=requirement.work_auth_language.strip(),
            approval_status="pending",
            created_by=user.email,
            active=True,
        )
        db.add(job)
        activity(db, pursuit_id, user.email, "job_created", f"Created job from requirement: {job.title}")
        db.commit()
        _flash(request, f"Created job {job.title}.")
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)
    _flash(request, "Requirement was not found.", "error")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=requirements", status_code=303)


@router.post("/pursuits/{pursuit_id}/technologies")
def add_pursuit_technology(
    request: Request,
    pursuit_id: int,
    category: str = Form(""),
    name: str = Form(""),
    evidence: str = Form(""),
    confidence: str = Form(""),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        db.add(PursuitTechnology(pursuit_id=pursuit_id, category=category, name=name, evidence=evidence, confidence=confidence))
        activity(db, pursuit_id, user.email, "technology_added", name or "Added technology")
        db.commit()
        _flash(request, "Added technology.")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=technologies", status_code=303)


@router.post("/pursuits/{pursuit_id}/contacts")
def add_pursuit_contact(
    request: Request,
    pursuit_id: int,
    name: str = Form(""),
    title: str = Form(""),
    department: str = Form(""),
    location: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    linkedin_url: str = Form(""),
    source_url: str = Form(""),
    confidence: str = Form(""),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        db.add(PursuitContact(pursuit_id=pursuit_id, name=name, title=title, department=department, location=location, email=email, phone=phone, linkedin_url=linkedin_url, source_url=source_url, confidence=confidence))
        evidence_url = source_url or linkedin_url
        if evidence_url:
            db.add(PursuitEvidence(pursuit_id=pursuit_id, kind="contact", label=name, url=evidence_url, confidence=confidence))
        activity(db, pursuit_id, user.email, "contact_added", name or "Added contact")
        db.commit()
        _flash(request, "Added contact.")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=contacts", status_code=303)


@router.post("/pursuits/{pursuit_id}/vendors")
def add_pursuit_vendor(
    request: Request,
    pursuit_id: int,
    vendor_name: str = Form(""),
    relationship_evidence: str = Form(""),
    technology_or_role_focus: str = Form(""),
    source_url: str = Form(""),
    confidence: str = Form(""),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        db.add(PursuitPrimeVendor(pursuit_id=pursuit_id, vendor_name=vendor_name, relationship_evidence=relationship_evidence, technology_or_role_focus=technology_or_role_focus, source_url=source_url, confidence=confidence))
        if source_url:
            db.add(PursuitEvidence(pursuit_id=pursuit_id, kind="prime_vendor", label=vendor_name, url=source_url, confidence=confidence))
        activity(db, pursuit_id, user.email, "vendor_added", vendor_name or "Added prime vendor")
        db.commit()
        _flash(request, "Added prime vendor.")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=vendors", status_code=303)


@router.post("/pursuits/{pursuit_id}/managers")
def add_pursuit_manager(
    request: Request,
    pursuit_id: int,
    name: str = Form(""),
    company_or_vendor: str = Form(""),
    title: str = Form(""),
    role_focus: str = Form(""),
    linkedin_url: str = Form(""),
    source_url: str = Form(""),
    confidence: str = Form(""),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        db.add(PursuitC2CManager(pursuit_id=pursuit_id, name=name, company_or_vendor=company_or_vendor, title=title, role_focus=role_focus, linkedin_url=linkedin_url, source_url=source_url, confidence=confidence))
        evidence_url = source_url or linkedin_url
        if evidence_url:
            db.add(PursuitEvidence(pursuit_id=pursuit_id, kind="c2c_manager", label=name, url=evidence_url, confidence=confidence))
        activity(db, pursuit_id, user.email, "manager_added", name or "Added C2C manager")
        db.commit()
        _flash(request, "Added C2C manager.")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=managers", status_code=303)


@router.post("/pursuits/{pursuit_id}/evidence")
def add_pursuit_evidence(
    request: Request,
    pursuit_id: int,
    kind: str = Form(""),
    label: str = Form(""),
    url: str = Form(""),
    evidence_notes: str = Form(""),
    confidence: str = Form(""),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        db.add(PursuitEvidence(pursuit_id=pursuit_id, kind=kind, label=label, url=url, notes=evidence_notes, confidence=confidence))
        activity(db, pursuit_id, user.email, "evidence_added", label or url or "Added evidence")
        db.commit()
        _flash(request, "Added evidence.")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=notes", status_code=303)


@router.post("/pursuits/{pursuit_id}/activities")
def add_pursuit_activity(
    request: Request,
    pursuit_id: int,
    activity_type: str = Form("note"),
    summary: str = Form(""),
    due_at: str = Form(""),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        activity(db, pursuit_id, user.email, activity_type, summary, due_at or None)
        db.commit()
        _flash(request, "Added activity.")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=notes", status_code=303)


@router.post("/pursuits/{pursuit_id}/notes")
def add_pursuit_note(
    request: Request,
    pursuit_id: int,
    category: str = Form("owner_note"),
    body: str = Form(""),
    pinned: bool = Form(False),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        if body.strip():
            db.add(PursuitNote(pursuit_id=pursuit_id, author=user.email, category=category[:60], body=body.strip(), pinned=pinned))
            activity(db, pursuit_id, user.email, "note_added", body.strip()[:160])
            db.commit()
            _flash(request, "Added owner note.")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=notes", status_code=303)


@router.post("/pursuits/{pursuit_id}/notes/{note_id}/archive")
def archive_pursuit_note(
    request: Request,
    pursuit_id: int,
    note_id: int,
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    note = db.get(PursuitNote, note_id)
    if pursuit and note and note.pursuit_id == pursuit_id:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        note.active = False
        db.add(note)
        activity(db, pursuit_id, user.email, "note_archived", note.body[:160])
        db.commit()
        _flash(request, "Archived owner note.")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=notes", status_code=303)


@router.post("/pursuits/{pursuit_id}/notes/{note_id}/update")
def update_pursuit_note(
    request: Request,
    pursuit_id: int,
    note_id: int,
    category: str = Form("owner_note"),
    body: str = Form(""),
    pinned: bool = Form(False),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    note = db.get(PursuitNote, note_id)
    if pursuit and note and note.pursuit_id == pursuit_id:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        if body.strip():
            note.category = category[:60]
            note.body = body.strip()
            note.pinned = pinned
            db.add(note)
            activity(db, pursuit_id, user.email, "note_updated", note.body[:160])
            db.commit()
            _flash(request, "Updated owner note.")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=notes", status_code=303)


@router.post("/pursuits/{pursuit_id}/research/import-json")
def import_pursuit_research_json(
    request: Request,
    pursuit_id: int,
    research_json: str = Form(""),
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit and research_json.strip():
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        try:
            ingest_research_json(db, pursuit, research_json, actor=user.email)
            db.commit()
            _flash(request, "Imported research JSON into structured tabs.")
        except Exception as exc:
            activity(db, pursuit.id, user.email, "research_import_failed", str(exc))
            db.commit()
            _flash(request, f"Research JSON import failed: {exc}", "error")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=requirements", status_code=303)


@router.post("/pursuits/{pursuit_id}/research/run-openai")
async def run_openai_research(
    request: Request,
    pursuit_id: int,
    user: User = Depends(require_permission(Permission.MANAGE_PURSUIT_WORKSPACE)),
    db: Session = Depends(get_db),
):
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        if not _can_edit_pursuit_workspace(user, pursuit):
            raise PermissionDenied("This company belongs to another region group.")
        company_context = _company_uscis_context(db, pursuit.company)
        prompt = pursuit.research_prompt or build_company_research_prompt(pursuit.company, company_context)
        job = create_research_job(db, pursuit, prompt)
        db.commit()
        await run_research_job(db, job, pursuit)
        _flash(request, f"OpenAI research finished with status {job.status}.", "success" if job.status == ResearchJobStatus.COMPLETED.value else "error")
    return RedirectResponse(f"/pursuits/{pursuit_id}?tab=prompt", status_code=303)


@router.get("/regions", response_class=HTMLResponse)
def regions(
    request: Request,
    sort: str = "name",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(Region).order_by(Region.name)).all()
    metadata = {item["code"]: item for item in all_region_metadata()}
    if sort == "code":
        rows.sort(key=lambda region: region.code)
    elif sort == "tier":
        rows.sort(key=lambda region: (metadata.get(region.code, {}).get("tier") or "", region.name))
    elif sort == "owner":
        rows.sort(key=lambda region: (region.staff_owner_name or "", region.name))
    else:
        rows.sort(key=lambda region: region.name)
    params = {"sort": sort}
    group_members = _staff_options_by_region(db)
    return templates.TemplateResponse(
        "web/regions.html",
        {
            "request": request,
            "user": user,
            "regions": rows,
            "region_metadata": metadata,
            "group_members": group_members,
            "sort": sort,
            "sort_urls": _sort_urls("/regions", params, ["name", "code", "tier", "owner"]),
            "total_rows": len(rows),
        },
    )


@router.get("/regions/{region_id}", response_class=HTMLResponse)
def region_detail(region_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    region = db.get(Region, region_id)
    if not region:
        return RedirectResponse("/regions", status_code=303)
    metadata = _region_metadata(region.code)
    group_members = _staff_options_by_region(db).get(region.id, [])
    return templates.TemplateResponse(
        "web/region_detail.html",
        {
            "request": request,
            "user": user,
            "region": region,
            "meta": metadata,
            "group_members": group_members,
        },
    )


@router.get("/regions/{region_id}/edit", response_class=HTMLResponse)
def edit_region(region_id: int, request: Request, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    region = db.get(Region, region_id)
    if not region:
        return RedirectResponse("/regions", status_code=303)
    return templates.TemplateResponse(
        "web/region_form.html",
        {
            "request": request,
            "user": user,
            "region": region,
            "meta": _region_metadata(region.code),
        },
    )


@router.get("/region-groups", response_class=HTMLResponse)
def region_groups(
    request: Request,
    status: str = "active",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(RegionGroup)
    if status == "active":
        query = query.where(RegionGroup.active.is_(True))
    elif status == "inactive":
        query = query.where(RegionGroup.active.is_(False))
    query = query.order_by(RegionGroup.name.asc())
    total_rows = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pagination = _pagination_context(total_rows, page, per_page)
    groups = db.scalars(query.limit(per_page).offset(pagination["offset"])).all()
    params = {"status": status, "per_page": per_page}
    coverage = _region_group_coverage(db)
    return templates.TemplateResponse(
        "web/region_groups.html",
        {
            "request": request,
            "user": user,
            "groups": groups,
            "status": status,
            "status_options": [("active", "Active"), ("inactive", "Inactive"), ("all", "All")],
            "coverage": coverage,
            "page_params": urlencode(params),
            **pagination,
        },
    )


@router.get("/region-groups/new", response_class=HTMLResponse)
def new_region_group_form(request: Request, error: str = "", user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "web/region_group_form.html",
        {
            "request": request,
            "user": user,
            "group": None,
            "regions": db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all(),
            "staff": _assignable_staff_options(db),
            "assigned_region_ids": set(),
            "assigned_member_ids": set(),
            "error": error,
        },
    )


@router.post("/region-groups")
def create_region_group(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    active: bool = Form(False),
    region_ids: Optional[list[int]] = Form(None),
    member_ids: Optional[list[int]] = Form(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not name.strip():
        return RedirectResponse("/region-groups/new?error=Group+name+is+required", status_code=303)
    group = RegionGroup(name=name.strip(), description=description.strip(), active=active)
    db.add(group)
    try:
        db.flush()
        _save_region_group_assignments(db, group.id, region_ids, member_ids)
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/region-groups/new?error=Group+name+already+exists", status_code=303)
    _flash(request, f"Created region group {group.name}.")
    return RedirectResponse(f"/region-groups/{group.id}", status_code=303)


@router.get("/region-groups/{group_id}", response_class=HTMLResponse)
def region_group_detail(group_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    group = db.get(RegionGroup, group_id)
    if not group:
        return RedirectResponse("/region-groups", status_code=303)
    return templates.TemplateResponse(
        "web/region_group_detail.html",
        {
            "request": request,
            "user": user,
            "group": group,
            "assigned_regions": _active_group_regions(group),
            "members": _active_group_members(group),
        },
    )


@router.get("/region-groups/{group_id}/edit", response_class=HTMLResponse)
def edit_region_group_form(group_id: int, request: Request, error: str = "", user: User = Depends(require_admin), db: Session = Depends(get_db)):
    group = db.get(RegionGroup, group_id)
    if not group:
        return RedirectResponse("/region-groups", status_code=303)
    return templates.TemplateResponse(
        "web/region_group_form.html",
        {
            "request": request,
            "user": user,
            "group": group,
            "regions": db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all(),
            "staff": _assignable_staff_options(db),
            "assigned_region_ids": {assignment.region_id for assignment in group.regions if assignment.active},
            "assigned_member_ids": {membership.user_id for membership in group.members if membership.active},
            "error": error,
        },
    )


@router.post("/region-groups/{group_id}")
def update_region_group(
    request: Request,
    group_id: int,
    name: str = Form(...),
    description: str = Form(""),
    active: bool = Form(False),
    region_ids: Optional[list[int]] = Form(None),
    member_ids: Optional[list[int]] = Form(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    group = db.get(RegionGroup, group_id)
    if not group:
        return RedirectResponse("/region-groups", status_code=303)
    if not name.strip():
        return RedirectResponse(f"/region-groups/{group_id}/edit?error=Group+name+is+required", status_code=303)
    group.name = name.strip()
    group.description = description.strip()
    group.active = active
    db.add(group)
    try:
        _save_region_group_assignments(db, group.id, region_ids, member_ids)
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(f"/region-groups/{group_id}/edit?error=Group+name+already+exists", status_code=303)
    _flash(request, f"Updated region group {group.name}.")
    return RedirectResponse(f"/region-groups/{group.id}", status_code=303)


@router.post("/region-groups/{group_id}/archive")
def archive_region_group(group_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    group = db.get(RegionGroup, group_id)
    if group:
        group.active = False
        db.add(group)
        db.commit()
        _flash(request, f"Archived region group {group.name}.")
    return RedirectResponse("/region-groups", status_code=303)


@router.post("/region-groups/{group_id}/restore")
def restore_region_group(group_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    group = db.get(RegionGroup, group_id)
    if group:
        group.active = True
        db.add(group)
        db.commit()
        _flash(request, f"Restored region group {group.name}.")
    return RedirectResponse(f"/region-groups/{group_id}", status_code=303)


@router.post("/regions/{region_id}")
def update_region(
    request: Request,
    region_id: int,
    staff_owner_name: str = Form(""),
    staff_owner_email: str = Form(""),
    active: bool = Form(False),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    region = db.get(Region, region_id)
    if region:
        region.staff_owner_name = staff_owner_name
        region.staff_owner_email = staff_owner_email
        region.active = active
        db.add(region)
        db.commit()
        _flash(request, f"Updated {region.name}.")
    return RedirectResponse(f"/regions/{region_id}", status_code=303)


@router.post("/regions/{region_id}/status")
def update_region_status(
    request: Request,
    region_id: int,
    active: bool = Form(True),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    region = db.get(Region, region_id)
    if region:
        region.active = active
        db.add(region)
        db.commit()
        _flash(request, f"{'Activated' if active else 'Deactivated'} {region.name}.")
    return RedirectResponse("/regions", status_code=303)


def _uscis_analysis_cache_key(filters: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), "" if value is None else str(value)) for key, value in filters.items()))


def _uscis_analysis_context(db: Session, **filters: Any) -> dict[str, Any]:
    key = _uscis_analysis_cache_key(filters)
    now = time.monotonic()
    with _USCIS_ANALYSIS_CACHE_LOCK:
        cached = _USCIS_ANALYSIS_CACHE.get(key)
        if cached and now - cached[0] <= _USCIS_ANALYSIS_CACHE_TTL_SECONDS:
            return dict(cached[1])

        context = _build_uscis_analysis_context(db, **filters)
        if len(_USCIS_ANALYSIS_CACHE) > 128:
            _USCIS_ANALYSIS_CACHE.clear()
        _USCIS_ANALYSIS_CACHE[key] = (time.monotonic(), dict(context))
        return context


def _build_uscis_analysis_context(db: Session, **filters: Any) -> dict[str, Any]:
    include_export_rows = bool(filters.get("include_export_rows"))
    q = (filters.get("q") or "").strip()
    state = (filters.get("state") or "").strip().upper()
    region = (filters.get("region") or "").strip()
    naics = (filters.get("naics") or "").strip()
    decision_type = (filters.get("decision_type") or UscisDecisionType.ALL.value).strip()
    profile = (filters.get("profile") or "h1b").strip()
    target_size = (filters.get("target_size") or "all").strip()
    min_approvals = max(int(filters.get("min_approvals") if filters.get("min_approvals") is not None else 10), 0)
    min_approval_rate = max(min(int(filters.get("min_approval_rate") if filters.get("min_approval_rate") is not None else 80), 100), 0)
    sort = (filters.get("sort") or "fit").strip()
    page = max(int(filters.get("page") or 1), 1)
    per_page = max(min(int(filters.get("per_page") or 50), 200), 10)

    available_years = list(db.scalars(select(distinct(UscisEmployerYearlyStat.fiscal_year)).order_by(UscisEmployerYearlyStat.fiscal_year.desc())).all())
    if available_years:
        max_year = max(available_years)
        min_year = max(max_year - 9, min(available_years))
    else:
        max_year = min_year = None
    start_year = filters.get("start_year") or min_year
    end_year = filters.get("end_year") or max_year
    if start_year and end_year and start_year > end_year:
        start_year, end_year = end_year, start_year

    decision_approval_expr, decision_denial_expr = _decision_expressions(decision_type)
    query = select(
        UscisEmployerYearlyStat.company_id,
        func.max(UscisEmployerYearlyStat.employer_name).label("employer_name"),
        func.count(distinct(UscisEmployerYearlyStat.fiscal_year)).label("years"),
        func.count(distinct(UscisEmployerYearlyStat.petitioner_state)).label("states"),
        func.count(distinct(UscisEmployerYearlyStat.petitioner_city)).label("cities"),
        func.max(UscisEmployerYearlyStat.fiscal_year).label("latest_year"),
        func.sum(UscisEmployerYearlyStat.total_approvals).label("approvals"),
        func.sum(UscisEmployerYearlyStat.total_denials).label("denials"),
        func.sum(decision_approval_expr).label("decision_approvals"),
        func.sum(decision_denial_expr).label("decision_denials"),
        func.sum(UscisEmployerYearlyStat.new_employment_approval).label("new_employment_approvals"),
        func.sum(UscisEmployerYearlyStat.new_employment_denial).label("new_employment_denials"),
        func.sum(UscisEmployerYearlyStat.change_employer_approval).label("change_employer_approvals"),
        func.sum(UscisEmployerYearlyStat.change_employer_denial).label("change_employer_denials"),
        func.sum(UscisEmployerYearlyStat.continuation_approval).label("continuation_approvals"),
        func.sum(UscisEmployerYearlyStat.continuation_denial).label("continuation_denials"),
    ).group_by(UscisEmployerYearlyStat.company_id)
    if start_year:
        query = query.where(UscisEmployerYearlyStat.fiscal_year >= start_year)
    if end_year:
        query = query.where(UscisEmployerYearlyStat.fiscal_year <= end_year)
    if q:
        query = query.where(UscisEmployerYearlyStat.employer_name.ilike(f"%{q}%"))
    if state:
        query = query.where(UscisEmployerYearlyStat.petitioner_state == state)
    elif region:
        states = states_for_region(region)
        if states:
            query = query.where(UscisEmployerYearlyStat.petitioner_state.in_(states))
    if naics:
        query = query.where(UscisEmployerYearlyStat.naics_code.startswith(naics))
    decision_approvals_sum = func.sum(decision_approval_expr)
    decision_denials_sum = func.sum(decision_denial_expr)
    if min_approvals:
        query = query.having(decision_approvals_sum >= min_approvals)
    if min_approval_rate:
        query = query.having((decision_approvals_sum * 100) >= (min_approval_rate * (decision_approvals_sum + decision_denials_sum)))
    new_sum = func.sum(UscisEmployerYearlyStat.new_employment_approval)
    transfer_sum = func.sum(UscisEmployerYearlyStat.change_employer_approval)
    continuation_sum = func.sum(UscisEmployerYearlyStat.continuation_approval)
    total_approvals_sum = func.sum(UscisEmployerYearlyStat.total_approvals)
    active_years_count = func.count(distinct(UscisEmployerYearlyStat.fiscal_year))
    annual_approvals_avg = total_approvals_sum / active_years_count
    if profile == "opt":
        query = query.having(new_sum >= 5)
    elif profile == "h1b":
        query = query.having((new_sum + transfer_sum + continuation_sum) >= 20)
    elif profile == "transfer":
        query = query.having(transfer_sum >= 5)
    elif profile == "consulting":
        query = query.having((new_sum + transfer_sum) >= 10)
    if target_size == "emerging":
        query = query.having(annual_approvals_avg.between(5, 50))
    elif target_size == "mid_size":
        query = query.having(annual_approvals_avg.between(51, 500))
    elif target_size == "sweet_spot":
        query = query.having(annual_approvals_avg.between(51, 300))
    elif target_size == "large":
        query = query.having(total_approvals_sum.between(501, 5000))
    elif target_size == "mega":
        query = query.having(total_approvals_sum > 5000)
    query = query.order_by(decision_approvals_sum.desc())

    raw_items = db.execute(query).mappings().all()
    company_ids = [int(item["company_id"]) for item in raw_items if item["company_id"]]
    companies_by_id = {
        company.id: company
        for company in db.scalars(select(Company).where(Company.id.in_(company_ids))).all()
    } if company_ids else {}

    rows = []
    totals = {"companies": 0, "approvals": 0, "denials": 0, "decision_approvals": 0, "decision_denials": 0}
    for item in raw_items:
        approvals = int(item["decision_approvals"] or 0)
        denials = int(item["decision_denials"] or 0)
        total = approvals + denials
        if total <= 0:
            continue
        approval_rate = round(approvals / total * 100, 1)
        all_approvals = int(item["approvals"] or 0)
        all_denials = int(item["denials"] or 0)
        all_total = all_approvals + all_denials
        all_approval_rate = round(all_approvals / all_total * 100, 1) if all_total else 0
        years = int(item["years"] or 0)
        annual_approvals = round(all_approvals / years, 1) if years else 0
        if approvals < min_approvals or approval_rate < min_approval_rate:
            continue
        signal = _company_fit_signal(item, approval_rate, profile)
        if profile != "all" and not signal["matches"]:
            continue
        sponsor_signal = _sponsorship_likelihood_signal(item, all_approval_rate, annual_approvals)
        company = companies_by_id.get(int(item["company_id"])) if item["company_id"] else None
        totals["companies"] += 1
        totals["approvals"] += int(item["approvals"] or 0)
        totals["denials"] += int(item["denials"] or 0)
        totals["decision_approvals"] += approvals
        totals["decision_denials"] += denials
        rows.append(
            {
                **item,
                "company": company,
                "decisions": total,
                "approvals": approvals,
                "denials": denials,
                "approval_rate": approval_rate,
                "all_approval_rate": all_approval_rate,
                "annual_approvals": annual_approvals,
                "fit_score": signal["score"],
                "fit_label": signal["label"],
                "fit_notes": signal["notes"],
                "sponsor_score": sponsor_signal["score"],
                "sponsor_label": sponsor_signal["label"],
                "sponsor_notes": sponsor_signal["notes"],
                "target_size_label": _target_size_label(annual_approvals),
            }
        )
    if sort == "approvals":
        rows.sort(key=lambda row: (row["approvals"], row["approval_rate"]), reverse=True)
    elif sort == "approval_rate":
        rows.sort(key=lambda row: (row["approval_rate"], row["approvals"]), reverse=True)
    elif sort == "latest":
        rows.sort(key=lambda row: (row["latest_year"], row["approvals"]), reverse=True)
    elif sort == "new":
        rows.sort(key=lambda row: (row["new_employment_approvals"], row["approvals"]), reverse=True)
    elif sort == "transfer":
        rows.sort(key=lambda row: (row["change_employer_approvals"], row["approvals"]), reverse=True)
    elif sort == "continue":
        rows.sort(key=lambda row: (row["continuation_approvals"], row["approvals"]), reverse=True)
    elif sort == "sponsor":
        rows.sort(key=lambda row: (row["sponsor_score"], row["approvals"], row["approval_rate"]), reverse=True)
    elif sort == "name":
        rows.sort(key=lambda row: row["employer_name"])
    else:
        rows.sort(key=lambda row: (row["fit_score"], row["approvals"], row["approval_rate"]), reverse=True)
    total_rows = len(rows)
    pages = max(ceil(total_rows / per_page), 1)
    page = min(page, pages)
    start = (page - 1) * per_page
    page_rows = rows[start : start + per_page]
    region_signals = region_signals_for_companies(db, [row["company"].id for row in page_rows if row["company"]])
    for row in page_rows:
        if row["company"]:
            row["region_signal"] = region_signals.get(row["company"].id, {"region": None, "states": []})
        else:
            row["region_signal"] = {"region": None, "states": []}
    params = {
        k: v
        for k, v in {
            "q": q,
            "state": state,
            "region": region,
            "naics": naics,
            "decision_type": decision_type,
            "profile": profile,
            "target_size": target_size,
            "min_approvals": min_approvals,
            "min_approval_rate": min_approval_rate,
            "sort": sort,
            "start_year": start_year,
            "end_year": end_year,
            "per_page": per_page,
        }.items()
        if v not in (None, "")
    }
    sort_params = dict(params)
    decision_total = totals["decision_approvals"] + totals["decision_denials"]
    return {
        "rows": page_rows,
        "export_rows": rows if include_export_rows else [],
        "totals": {
            **totals,
            "decision_rate": round(totals["decision_approvals"] / decision_total * 100, 1) if decision_total else 0,
        },
        "total_rows": total_rows,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "row_start": start + 1 if total_rows else 0,
        "row_end": min(start + per_page, total_rows),
        "query": q,
        "state": state,
        "region": region,
        "naics": naics,
        "decision_type": decision_type,
        "profile": profile,
        "target_size": target_size,
        "min_approvals": min_approvals,
        "min_approval_rate": min_approval_rate,
        "sort": sort,
        "start_year": start_year,
        "end_year": end_year,
        "available_years": available_years,
        "state_options": db.scalars(select(distinct(UscisEmployerYearlyStat.petitioner_state)).where(UscisEmployerYearlyStat.petitioner_state != "").order_by(UscisEmployerYearlyStat.petitioner_state)).all(),
        "region_options": all_region_metadata(),
        "decision_options": _decision_options(),
        "profile_options": _profile_options(),
        "target_size_options": _target_size_options(),
        "sort_options": _sort_options(),
        "params": urlencode(params),
        "export_url": f"/uscis/analysis?{urlencode({**params, 'format': 'csv'})}",
        "sort_urls": _sort_urls("/uscis/analysis", sort_params, ["fit", "sponsor", "name", "latest", "new", "transfer", "continue", "approvals", "approval_rate"]),
    }


def _pagination_context(total_rows: int, page: int, per_page: int) -> dict[str, int]:
    pages = max(ceil(total_rows / per_page), 1)
    page = min(max(page, 1), pages)
    offset = (page - 1) * per_page
    return {
        "total_rows": total_rows,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "offset": offset,
        "row_start": offset + 1 if total_rows else 0,
        "row_end": min(offset + per_page, total_rows),
    }


def _sort_urls(base_path: str, params: dict[str, Any], keys: list[str]) -> dict[str, str]:
    return _named_sort_urls(base_path, params, "sort", keys)


def _named_sort_urls(base_path: str, params: dict[str, Any], param_name: str, keys: list[str]) -> dict[str, str]:
    urls = {}
    for key in keys:
        next_params = {**params, param_name: key}
        next_params.pop("page", None)
        urls[key] = f"{base_path}?{urlencode(next_params)}"
    return urls


def _csv_response(filename: str, rows: list[dict[str, Any]], fieldnames: list[str]) -> Response:
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _merge_company_records(db: Session, source: Company, target: Company, actor: str, notes: str = "") -> None:
    source_id = source.id
    target_id = target.id
    db.add(
        CompanyMergeAudit(
            source_company_id=source_id,
            source_company_name=source.name,
            target_company_id=target_id,
            target_company_name=target.name,
            actor=actor,
            notes=notes,
        )
    )
    db.execute(update(CompanyAlias).where(CompanyAlias.company_id == source_id).values(company_id=target_id))
    db.execute(update(UscisEmployerYearlyStat).where(UscisEmployerYearlyStat.company_id == source_id).values(company_id=target_id))
    db.execute(update(H1BDisclosure).where(H1BDisclosure.company_id == source_id).values(company_id=target_id))
    db.execute(update(JobOpportunity).where(JobOpportunity.company_id == source_id).values(company_id=target_id))
    db.execute(update(InterviewExperience).where(InterviewExperience.company_id == source_id).values(company_id=target_id))
    if source.pursuit and not target.pursuit:
        source.pursuit.company_id = target_id
        db.add(source.pursuit)
    db.delete(source)
    db.flush()
    refresh_companies_from_uscis(db, {target_id})


def _available_uscis_years(db: Session) -> list[int]:
    return list(db.scalars(select(distinct(UscisEmployerYearlyStat.fiscal_year)).order_by(UscisEmployerYearlyStat.fiscal_year.desc())).all())


def _import_sources(db: Session) -> list[dict[str, Any]]:
    return [
        {
            "source_file": row["source_file"] or "Unknown",
            "rows": int(row["rows"] or 0),
            "latest_import": row["latest_import"],
        }
        for row in db.execute(
            select(
                UscisEmployerYearlyStat.source_file,
                func.count(UscisEmployerYearlyStat.id).label("rows"),
                func.max(UscisEmployerYearlyStat.imported_at).label("latest_import"),
            )
            .group_by(UscisEmployerYearlyStat.source_file)
            .order_by(func.max(UscisEmployerYearlyStat.imported_at).desc())
            .limit(10)
        ).mappings()
    ]


def _recent_import_jobs(db: Session) -> list[UscisImportJob]:
    return db.scalars(select(UscisImportJob).order_by(UscisImportJob.created_at.desc()).limit(10)).all()


def _import_upload_dir() -> Path:
    path = Path("data/imports")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_uscis_import_job(job_id: int, default_year: Optional[int]) -> None:
    with SessionLocal() as db:
        job = db.get(UscisImportJob, job_id)
        if not job:
            return
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        db.add(job)
        db.commit()

        def progress(values: dict[str, int]) -> None:
            current = db.get(UscisImportJob, job_id)
            if not current:
                return
            current.processed_rows = values.get("processed_rows", current.processed_rows)
            current.imported = values.get("imported", current.imported)
            current.updated = values.get("updated", current.updated)
            current.skipped = values.get("skipped", current.skipped)
            db.add(current)
            db.commit()

        try:
            with Path(job.stored_path).open("rb") as handle:
                result = import_uscis_employer_rows(
                    db,
                    handle,
                    source_file=job.source_file,
                    default_year=default_year,
                    progress_callback=progress,
                )
            job = db.get(UscisImportJob, job_id)
            if job:
                job.status = "completed"
                job.processed_rows = result.get("processed_rows", job.processed_rows)
                job.imported = result.get("imported", job.imported)
                job.updated = result.get("updated", job.updated)
                job.skipped = result.get("skipped", job.skipped)
                job.completed_at = datetime.now(timezone.utc)
                db.add(job)
                db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(UscisImportJob, job_id)
            if job:
                job.status = "failed"
                job.error = str(exc)
                job.completed_at = datetime.now(timezone.utc)
                db.add(job)
                db.commit()


def _default_year_range(available_years: list[int]) -> tuple[Optional[int], Optional[int]]:
    if not available_years:
        return None, None
    max_year = max(available_years)
    return max(max_year - 4, min(available_years)), max_year


def _watchlist_source_options() -> list[tuple[str, str]]:
    return [("all", "USCIS + promoted"), ("promoted", "Promoted only"), ("uscis_only", "USCIS only")]


def _watchlist_standard_filter_options() -> list[tuple[str, str]]:
    return [("exclude_very_high", "Exclude very high standards"), ("include_all", "Include all"), ("only_high_risk", "Only high-standard risk")]


def _watchlist_sort_options() -> list[tuple[str, str]]:
    return [
        ("watch_score", "Watch score"),
        ("approvals", "Latest approvals"),
        ("approval_rate", "Approval rate"),
        ("new_employment", "New employment"),
        ("standard_risk", "Hiring standards risk"),
        ("company", "Company"),
        ("staff", "Suggested staff"),
    ]


def _location_cost_tier_options() -> list[tuple[str, str]]:
    return [("low", "Low only"), ("medium", "Medium or lower"), ("high", "High or lower"), ("very_high", "All markets")]


_COST_TIER_ORDER = {"low": 1, "medium": 2, "high": 3, "very_high": 4}
_LOCATION_HUBS: dict[str, dict[str, Any]] = {
    "houston-tx": {"hub": "Houston, TX", "cities": [("HOUSTON", "TX"), ("SUGAR LAND", "TX"), ("KATY", "TX"), ("SPRING", "TX")], "cost_tier": "medium", "coverage_notes": "Existing South Texas base for energy, healthcare, consulting, and enterprise IT.", "existing": True},
    "dallas-fort-worth-tx": {"hub": "Dallas / Irving / Plano, TX", "cities": [("DALLAS", "TX"), ("IRVING", "TX"), ("PLANO", "TX"), ("RICHARDSON", "TX"), ("FRISCO", "TX"), ("MCKINNEY", "TX"), ("FORT WORTH", "TX"), ("CARROLLTON", "TX"), ("FARMERS BRANCH", "TX"), ("ADDISON", "TX"), ("LEWISVILLE", "TX")], "cost_tier": "medium", "coverage_notes": "North Texas finance, telecom, consulting, retail tech, cloud, data, and platform coverage."},
    "austin-tx": {"hub": "Austin, TX", "cities": [("AUSTIN", "TX"), ("ROUND ROCK", "TX")], "cost_tier": "high", "coverage_notes": "Texas product, platform, cloud, semiconductor, and startup market; San Antonio is reachable."},
    "san-antonio-tx": {"hub": "San Antonio, TX", "cities": [("SAN ANTONIO", "TX")], "cost_tier": "medium", "coverage_notes": "Low-cost Texas satellite with healthcare, government, defense, and Austin interview support."},
    "phoenix-chandler-az": {"hub": "Phoenix / Chandler, AZ", "cities": [("PHOENIX", "AZ"), ("CHANDLER", "AZ"), ("SCOTTSDALE", "AZ"), ("TEMPE", "AZ")], "cost_tier": "medium", "coverage_notes": "Cost-effective Southwest hub for fintech, semiconductor, healthcare, and cloud operations."},
    "denver-co": {"hub": "Denver, CO", "cities": [("DENVER", "CO"), ("ENGLEWOOD", "CO")], "cost_tier": "high", "coverage_notes": "Mountain West coverage; useful where geography matters more than pure filing density."},
    "sacramento-ca": {"hub": "Sacramento, CA", "cities": [("SACRAMENTO", "CA")], "cost_tier": "high", "coverage_notes": "Lower-cost California base with Bay Area interviews inside practical driving range."},
    "irvine-orange-county-ca": {"hub": "Irvine / Orange County, CA", "cities": [("IRVINE", "CA"), ("NEWPORT BEACH", "CA"), ("SANTA ANA", "CA"), ("COSTA MESA", "CA"), ("ANAHEIM", "CA"), ("CITY OF INDUSTRY", "CA"), ("ONTARIO", "CA")], "cost_tier": "high", "coverage_notes": "Southern California hub for OC, Los Angeles, Inland Empire, and San Diego interviews."},
    "san-diego-ca": {"hub": "San Diego, CA", "cities": [("SAN DIEGO", "CA")], "cost_tier": "high", "coverage_notes": "Biotech, defense, healthcare, and software market; close enough to Orange County but less central."},
    "bay-area-ca": {"hub": "San Jose / Santa Clara / Bay Area, CA", "cities": [("SAN JOSE", "CA"), ("SANTA CLARA", "CA"), ("MOUNTAIN VIEW", "CA"), ("SAN FRANCISCO", "CA"), ("SUNNYVALE", "CA"), ("CUPERTINO", "CA"), ("FREMONT", "CA"), ("PALO ALTO", "CA"), ("MENLO PARK", "CA"), ("REDWOOD CITY", "CA"), ("SAN MATEO", "CA"), ("NEWARK", "CA"), ("PLEASANTON", "CA"), ("MILPITAS", "CA"), ("OAKLAND", "CA"), ("BERKELEY", "CA"), ("SAN RAMON", "CA"), ("SOUTH SAN FRANCISCO", "CA")], "cost_tier": "very_high", "coverage_notes": "Highest California opportunity density, but expensive; use as opportunity hub, not cost hub."},
    "los-angeles-ca": {"hub": "Los Angeles, CA", "cities": [("LOS ANGELES", "CA"), ("SANTA MONICA", "CA"), ("PASADENA", "CA"), ("TORRANCE", "CA")], "cost_tier": "very_high", "coverage_notes": "Large employer base, media, healthcare, fintech, and enterprise IT, with high cost."},
    "chicago-il": {"hub": "Chicago, IL", "cities": [("CHICAGO", "IL"), ("NAPERVILLE", "IL"), ("SCHAUMBURG", "IL"), ("EVANSTON", "IL"), ("BLOOMINGTON", "IL"), ("SPRINGFIELD", "IL")], "cost_tier": "medium", "coverage_notes": "Midwest anchor for finance, insurance, retail, consulting, SRE, cloud, and data roles."},
    "kansas-city-overland-park-ks": {"hub": "Kansas City / Overland Park, KS", "cities": [("OVERLAND PARK", "KS"), ("KANSAS CITY", "KS"), ("KANSAS CITY", "MO")], "cost_tier": "low", "coverage_notes": "Low-cost Midwest expansion option; smaller opportunity volume but strong affordability."},
    "detroit-troy-mi": {"hub": "Detroit / Troy, MI", "cities": [("DETROIT", "MI"), ("TROY", "MI"), ("FARMINGTON HILLS", "MI"), ("FARMINGTN HLS", "MI"), ("SOUTHFIELD", "MI"), ("NOVI", "MI"), ("ANN ARBOR", "MI"), ("AUBURN HILLS", "MI"), ("LIVONIA", "MI"), ("GRAND RAPIDS", "MI")], "cost_tier": "medium", "coverage_notes": "Automotive, manufacturing, insurance, healthcare, and enterprise platform coverage."},
    "minneapolis-mn": {"hub": "Minneapolis, MN", "cities": [("MINNEAPOLIS", "MN")], "cost_tier": "medium", "coverage_notes": "Healthcare, retail, financial services, and enterprise IT coverage in Upper Midwest."},
    "philadelphia-pa": {"hub": "Philadelphia, PA", "cities": [("PHILADELPHIA", "PA"), ("MECHANICSBURG", "PA"), ("PITTSBURGH", "PA")], "cost_tier": "medium", "coverage_notes": "Cost-effective Northeast/Mid-Atlantic access to healthcare, pharma, finance, and South NJ."},
    "north-jersey-corridor-nj": {"hub": "Princeton / Edison / Piscataway, NJ", "cities": [("PRINCETON", "NJ"), ("EDISON", "NJ"), ("PISCATAWAY", "NJ"), ("EAST BRUNSWICK", "NJ"), ("ISELIN", "NJ"), ("SOMERSET", "NJ"), ("PARSIPPANY", "NJ"), ("BASKING RIDGE", "NJ"), ("JERSEY CITY", "NJ"), ("NEWARK", "NJ"), ("NEW BRUNSWICK", "NJ"), ("SECAUCUS", "NJ"), ("PLAINSBORO", "NJ"), ("ROSELAND", "NJ")], "cost_tier": "high", "coverage_notes": "Single NJ corridor for pharma, finance, consulting, NYC access, and enterprise IT."},
    "boston-cambridge-ma": {"hub": "Boston / Cambridge, MA", "cities": [("BOSTON", "MA"), ("CAMBRIDGE", "MA"), ("WALTHAM", "MA"), ("SOMERVILLE", "MA"), ("WILMINGTON", "MA"), ("WOBURN", "MA"), ("NATICK", "MA"), ("SOUTHBOROUGH", "MA")], "cost_tier": "very_high", "coverage_notes": "Biotech, healthcare, finance, research, and platform roles; expensive but high opportunity."},
    "new-york-ny": {"hub": "New York, NY", "cities": [("NEW YORK", "NY"), ("BROOKLYN", "NY"), ("FLUSHING", "NY"), ("LONG ISLAND CITY", "NY"), ("BRONX", "NY"), ("ROCHESTER", "NY"), ("HAUPPAUGE", "NY")], "cost_tier": "very_high", "coverage_notes": "Massive opportunity density, but very high cost and competitive hiring standards."},
    "hartford-stamford-ct": {"hub": "Hartford / Stamford, CT", "cities": [("HARTFORD", "CT"), ("STAMFORD", "CT"), ("WINDSOR", "CT")], "cost_tier": "high", "coverage_notes": "Insurance, finance, healthcare, and New England coverage between NYC and Boston."},
    "atlanta-alpharetta-ga": {"hub": "Atlanta / Alpharetta, GA", "cities": [("ATLANTA", "GA"), ("ALPHARETTA", "GA"), ("CUMMING", "GA"), ("SUWANEE", "GA")], "cost_tier": "medium", "coverage_notes": "Southeast enterprise, fintech, logistics, healthcare, cloud, data, and consulting hub."},
    "charlotte-nc": {"hub": "Charlotte, NC", "cities": [("CHARLOTTE", "NC")], "cost_tier": "medium", "coverage_notes": "Banking, finance, insurance, and enterprise platform hub with practical living costs."},
    "raleigh-durham-nc": {"hub": "Raleigh / Durham, NC", "cities": [("RALEIGH", "NC"), ("DURHAM", "NC"), ("CARY", "NC"), ("MORRISVILLE", "NC")], "cost_tier": "medium", "coverage_notes": "Research Triangle for healthcare, biotech, SaaS, cloud, data, and enterprise engineering."},
    "nashville-tn": {"hub": "Nashville, TN", "cities": [("NASHVILLE", "TN"), ("MEMPHIS", "TN"), ("KNOXVILLE", "TN")], "cost_tier": "medium", "coverage_notes": "Healthcare IT, insurance, enterprise operations, and central Southeast coverage."},
    "dc-northern-virginia": {"hub": "DC / Northern Virginia", "cities": [("WASHINGTON", "DC"), ("ARLINGTON", "VA"), ("HERNDON", "VA"), ("ASHBURN", "VA"), ("FAIRFAX", "VA"), ("STERLING", "VA"), ("CHANTILLY", "VA"), ("MCLEAN", "VA"), ("RESTON", "VA"), ("VIENNA", "VA")], "cost_tier": "high", "coverage_notes": "Federal, cloud, security, data center, consulting, and enterprise IT market."},
    "baltimore-rockville-md": {"hub": "Rockville / Baltimore, MD", "cities": [("ROCKVILLE", "MD"), ("BALTIMORE", "MD"), ("COLUMBIA", "MD")], "cost_tier": "high", "coverage_notes": "Healthcare, federal, biotech, research, and consulting corridor."},
    "tampa-orlando-fl": {"hub": "Tampa / Orlando, FL", "cities": [("TAMPA", "FL"), ("ORLANDO", "FL"), ("JACKSONVILLE", "FL"), ("BOCA RATON", "FL"), ("SUNRISE", "FL"), ("MIAMI", "FL"), ("GAINESVILLE", "FL")], "cost_tier": "medium", "coverage_notes": "Florida healthcare, finance, insurance, logistics, and enterprise IT coverage."},
}


def _targeting_stage_options() -> list[tuple[str, str]]:
    return [
        ("actionable", "Actionable for staff"),
        ("ready_to_promote", "Ready to promote"),
        ("needs_json", "Needs job intelligence JSON"),
        ("needs_review", "Needs evidence review"),
        ("eliminated", "Eliminated / too senior"),
        ("all", "All signals"),
    ]


def _targeting_sort_options() -> list[tuple[str, str]]:
    return [
        ("watch_score", "Best fit first"),
        ("stage", "Action stage"),
        ("approvals", "Latest approvals"),
        ("approval_rate", "Approval rate"),
        ("new_employment", "New employment"),
        ("standard_risk", "Hiring standards risk"),
        ("company", "Company"),
        ("staff", "Suggested staff"),
    ]


def _location_city_to_hub() -> dict[tuple[str, str], tuple[str, dict[str, Any]]]:
    mapping: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    for key, profile in _LOCATION_HUBS.items():
        for city, state in profile["cities"]:
            mapping[(city.upper(), state.upper())] = (key, profile)
    return mapping


def _fallback_location_hub(city: str, state: str) -> tuple[str, dict[str, Any]]:
    normalized_city = re.sub(r"[^a-z0-9]+", "-", (city or "").lower()).strip("-") or "unknown"
    normalized_state = (state or "").lower() or "unknown"
    key = f"{normalized_city}-{normalized_state}"
    return key, {
        "hub": f"{city.title()}, {state.upper()}",
        "cities": [(city.upper(), state.upper())],
        "cost_tier": "medium",
        "coverage_notes": "USCIS-backed local market. Cost tier should be reviewed before final office decision.",
        "existing": False,
    }


def _location_expansion_report(
    db: Session,
    *,
    fiscal_year: Optional[int],
    plan_size: int,
    slots_per_region: int,
    max_cost_tier: str,
    min_new_employment: int,
    include_existing_houston: bool,
) -> dict[str, Any]:
    if not fiscal_year:
        return {"rows": [], "plan_rows": [], "region_rows": [], "summary": {}}
    max_cost_rank = _COST_TIER_ORDER.get(max_cost_tier, _COST_TIER_ORDER["high"])
    city_to_hub = _location_city_to_hub()
    stat_rows = db.execute(
        select(
            UscisEmployerYearlyStat.petitioner_city,
            UscisEmployerYearlyStat.petitioner_state,
            func.sum(UscisEmployerYearlyStat.total_approvals).label("approvals"),
            func.sum(UscisEmployerYearlyStat.total_denials).label("denials"),
            func.sum(UscisEmployerYearlyStat.new_employment_approval).label("new_employment"),
            func.count(distinct(UscisEmployerYearlyStat.normalized_employer_name)).label("employer_count"),
        )
        .where(
            UscisEmployerYearlyStat.fiscal_year == fiscal_year,
            UscisEmployerYearlyStat.petitioner_city != "",
            UscisEmployerYearlyStat.petitioner_state != "",
        )
        .group_by(UscisEmployerYearlyStat.petitioner_city, UscisEmployerYearlyStat.petitioner_state)
    ).mappings().all()
    region_meta = {item["code"]: item for item in all_region_metadata()}
    hubs: dict[str, dict[str, Any]] = {}
    for row in stat_rows:
        city = str(row["petitioner_city"] or "").strip().upper()
        state = str(row["petitioner_state"] or "").strip().upper()
        if not city or not state:
            continue
        hub_key, profile = city_to_hub.get((city, state), _fallback_location_hub(city, state))
        hub = hubs.setdefault(
            hub_key,
            {
                "hub_key": hub_key,
                "hub": profile["hub"],
                "cost_tier": profile.get("cost_tier", "medium"),
                "coverage_notes": profile.get("coverage_notes", ""),
                "existing": bool(profile.get("existing")),
                "states": {},
                "cities": set(),
                "approvals": 0,
                "denials": 0,
                "new_employment": 0,
                "employer_count": 0,
            },
        )
        approvals = int(row["approvals"] or 0)
        hub["approvals"] += approvals
        hub["denials"] += int(row["denials"] or 0)
        hub["new_employment"] += int(row["new_employment"] or 0)
        hub["employer_count"] += int(row["employer_count"] or 0)
        hub["states"][state] = hub["states"].get(state, 0) + approvals
        hub["cities"].add(f"{city.title()}, {state}")
    rows = []
    for hub in hubs.values():
        decisions = hub["approvals"] + hub["denials"]
        approval_rate = round(hub["approvals"] / decisions * 100, 1) if decisions else 0
        top_state = max(hub["states"].items(), key=lambda pair: pair[1])[0] if hub["states"] else ""
        region_code = region_code_for_state(top_state) or "unknown"
        meta = region_meta.get(region_code, _region_metadata("unknown"))
        cost_rank = _COST_TIER_ORDER.get(hub["cost_tier"], 2)
        opportunity_score = min(35, hub["new_employment"] // 40) + min(25, hub["approvals"] // 200) + min(20, hub["employer_count"] // 50) + (10 if approval_rate >= 90 else 6 if approval_rate >= 80 else 2)
        affordability_score = {"low": 30, "medium": 24, "high": 14, "very_high": 3}.get(hub["cost_tier"], 18)
        coverage_score = 10 if hub["existing"] else 8 if hub["employer_count"] >= 100 else 5
        expansion_score = max(0, min(100, opportunity_score + affordability_score + coverage_score))
        if hub["new_employment"] < min_new_employment:
            recommendation = "Too small for core office"
        elif cost_rank > max_cost_rank:
            recommendation = "Watch only: cost too high"
        elif expansion_score >= 70:
            recommendation = "Open / core hub"
        elif expansion_score >= 55:
            recommendation = "Strong satellite"
        elif expansion_score >= 40:
            recommendation = "Watch / travel market"
        else:
            recommendation = "Low priority"
        rows.append(
            {
                **hub,
                "latest_year": fiscal_year,
                "region_code": region_code,
                "region_name": meta.get("name", "Unknown / Unmapped"),
                "tier": meta.get("tier", "N/A"),
                "states_label": ", ".join(sorted(hub["states"])),
                "cities_label": ", ".join(sorted(hub["cities"])[:10]),
                "decisions": decisions,
                "approval_rate": approval_rate,
                "opportunity_score": int(opportunity_score),
                "affordability_score": int(affordability_score),
                "coverage_score": int(coverage_score),
                "expansion_score": int(expansion_score),
                "cost_rank": cost_rank,
                "recommendation": recommendation,
            }
        )
    rows = sorted(rows, key=lambda row: (-row["expansion_score"], -row["new_employment"], row["hub"]))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    eligible = [row for row in rows if row["new_employment"] >= min_new_employment and row["cost_rank"] <= max_cost_rank]
    plan_rows: list[dict[str, Any]] = []
    if include_existing_houston:
        houston = next((row for row in rows if row["hub_key"] == "houston-tx"), None)
        if houston:
            plan_rows.append({**houston, "plan_note": "Existing base"})
    used = {row["hub_key"] for row in plan_rows}
    if slots_per_region:
        per_region_counts: dict[str, int] = {}
        for row in plan_rows:
            per_region_counts[row["region_code"]] = per_region_counts.get(row["region_code"], 0) + 1
        for row in eligible:
            if row["hub_key"] in used:
                continue
            count = per_region_counts.get(row["region_code"], 0)
            if count >= slots_per_region:
                continue
            plan_rows.append({**row, "plan_note": f"Slot {count + 1} for {row['region_name']}"})
            used.add(row["hub_key"])
            per_region_counts[row["region_code"]] = count + 1
            if len(plan_rows) >= plan_size:
                break
    for row in eligible:
        if len(plan_rows) >= plan_size:
            break
        if row["hub_key"] not in used:
            plan_rows.append({**row, "plan_note": "Best remaining score"})
            used.add(row["hub_key"])
    for index, row in enumerate(plan_rows, start=1):
        row["plan_rank"] = index
    region_rows = []
    for code, meta in region_meta.items():
        region_hubs = [row for row in rows if row["region_code"] == code]
        selected = [row for row in plan_rows if row["region_code"] == code]
        region_rows.append(
            {
                "region_code": code,
                "region_name": meta["name"],
                "selected_count": len(selected),
                "top_hubs": region_hubs[:5],
                "selected_hubs": selected,
                "new_employment": sum(row["new_employment"] for row in region_hubs),
                "employer_count": sum(row["employer_count"] for row in region_hubs),
            }
        )
    summary = {
        "latest_year": fiscal_year,
        "hub_count": len(rows),
        "eligible_hub_count": len(eligible),
        "plan_size": len(plan_rows),
        "plan_new_employment": sum(row["new_employment"] for row in plan_rows),
        "plan_approvals": sum(row["approvals"] for row in plan_rows),
        "plan_employers": sum(row["employer_count"] for row in plan_rows),
    }
    return {"rows": rows, "plan_rows": plan_rows, "region_rows": region_rows, "summary": summary}


def _location_hub_company_report(
    db: Session,
    *,
    hub_key: str,
    fiscal_year: Optional[int],
    status: str,
    min_approvals: int,
    limit: int,
    sort: str,
) -> dict[str, Any]:
    profile = _LOCATION_HUBS.get(hub_key)
    if not profile or not fiscal_year:
        return {"hub": profile or {}, "rows": [], "summary": {}}
    city_state_pairs = [(city.upper(), state.upper()) for city, state in profile["cities"]]
    clauses = [
        (func.upper(UscisEmployerYearlyStat.petitioner_city) == city) & (func.upper(UscisEmployerYearlyStat.petitioner_state) == state)
        for city, state in city_state_pairs
    ]
    if not clauses:
        return {"hub": profile, "rows": [], "summary": {}}
    stat_rows = db.execute(
        select(
            UscisEmployerYearlyStat.company_id,
            Company.name.label("company_name"),
            UscisEmployerYearlyStat.petitioner_city,
            UscisEmployerYearlyStat.petitioner_state,
            func.sum(UscisEmployerYearlyStat.total_approvals).label("approvals"),
            func.sum(UscisEmployerYearlyStat.total_denials).label("denials"),
            func.sum(UscisEmployerYearlyStat.new_employment_approval).label("new_employment"),
            func.sum(UscisEmployerYearlyStat.change_employer_approval).label("change_employer"),
            func.sum(UscisEmployerYearlyStat.continuation_approval).label("continuation"),
        )
        .join(Company, Company.id == UscisEmployerYearlyStat.company_id)
        .where(
            UscisEmployerYearlyStat.fiscal_year == fiscal_year,
            UscisEmployerYearlyStat.company_id.is_not(None),
            or_(*clauses),
        )
        .group_by(
            UscisEmployerYearlyStat.company_id,
            Company.name,
            UscisEmployerYearlyStat.petitioner_city,
            UscisEmployerYearlyStat.petitioner_state,
        )
    ).mappings().all()
    company_rows: dict[int, dict[str, Any]] = {}
    for row in stat_rows:
        company_id = int(row["company_id"])
        current = company_rows.setdefault(
            company_id,
            {
                "company_id": company_id,
                "company_name": row["company_name"],
                "approvals": 0,
                "denials": 0,
                "new_employment": 0,
                "change_employer": 0,
                "continuation": 0,
                "cities": set(),
                "states": set(),
            },
        )
        current["approvals"] += int(row["approvals"] or 0)
        current["denials"] += int(row["denials"] or 0)
        current["new_employment"] += int(row["new_employment"] or 0)
        current["change_employer"] += int(row["change_employer"] or 0)
        current["continuation"] += int(row["continuation"] or 0)
        city = str(row["petitioner_city"] or "").title()
        state = str(row["petitioner_state"] or "").upper()
        if city or state:
            current["cities"].add(", ".join(part for part in [city, state] if part))
        if state:
            current["states"].add(state)
    company_ids = list(company_rows)
    pursuits_by_company = {pursuit.company_id: pursuit for pursuit in db.scalars(select(CompanyPursuit).where(CompanyPursuit.company_id.in_(company_ids or [-1]))).all()}
    top_state = city_state_pairs[0][1] if city_state_pairs else ""
    region_code = region_code_for_state(top_state) or ""
    region = db.scalar(select(Region).where(Region.code == region_code))
    staff = _staff_options_by_region(db).get(region.id if region else -1, [])
    suggested_staff = staff[0] if staff else None
    rows = []
    promoted_count = 0
    for item in company_rows.values():
        decisions = item["approvals"] + item["denials"]
        approval_rate = round(item["approvals"] / decisions * 100, 1) if decisions else 0
        if item["approvals"] < min_approvals:
            continue
        pursuit = pursuits_by_company.get(item["company_id"])
        if pursuit:
            promoted_count += 1
        if status == "promoted" and not pursuit:
            continue
        if status == "uscis_only" and pursuit:
            continue
        owner_name = pursuit.assigned_staff_name or pursuit.assigned_staff_email if pursuit else ""
        owner_email = pursuit.assigned_staff_email if pursuit else ""
        if not owner_name and suggested_staff:
            owner_name = suggested_staff.name or suggested_staff.email
            owner_email = suggested_staff.email
        rows.append(
            {
                **item,
                "hub_key": hub_key,
                "hub": profile["hub"],
                "latest_year": fiscal_year,
                "region_id": region.id if region else None,
                "region_name": region.name if region else "Unmapped",
                "region_code": region.code if region else region_code,
                "pursuit": pursuit,
                "promoted": bool(pursuit),
                "suggested_staff": owner_name or "No region staff assigned",
                "suggested_staff_email": owner_email,
                "decisions": decisions,
                "approval_rate": approval_rate,
                "cities_label": ", ".join(sorted(item["cities"])),
                "states_label": ", ".join(sorted(item["states"])),
            }
        )
    sort_map = {
        "approvals": lambda row: (-row["approvals"], row["company_name"].lower()),
        "approval_rate": lambda row: (-row["approval_rate"], -row["approvals"], row["company_name"].lower()),
        "company": lambda row: row["company_name"].lower(),
        "new_employment": lambda row: (-row["new_employment"], -row["approvals"], row["company_name"].lower()),
    }
    rows = sorted(rows, key=sort_map.get(sort, sort_map["new_employment"]))[:limit]
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    summary = {
        "latest_year": fiscal_year,
        "company_count": len(company_rows),
        "visible_count": len(rows),
        "promoted_count": promoted_count,
        "uscis_only_count": max(0, len(company_rows) - promoted_count),
        "approvals": sum(row["approvals"] for row in rows),
        "new_employment": sum(row["new_employment"] for row in rows),
        "region_name": region.name if region else "Unmapped",
        "suggested_staff": suggested_staff.name or suggested_staff.email if suggested_staff else "No region staff assigned",
    }
    return {"hub": {**profile, "hub_key": hub_key}, "rows": rows, "summary": summary}


def _company_watchlist_report(
    db: Session,
    *,
    fiscal_year: Optional[int],
    region_id: Optional[int],
    source: str,
    min_approvals: int,
    min_approval_rate: int,
    standard_filter: str,
    capacity_per_staff: int,
    sort: str,
) -> dict[str, Any]:
    if not fiscal_year:
        return {"rows": [], "summary": {}, "staff_rows": []}
    selected_region = db.get(Region, region_id) if region_id else None
    region_states = states_for_region(selected_region.code) if selected_region else []
    stat_query = (
        select(
            UscisEmployerYearlyStat.company_id,
            func.max(UscisEmployerYearlyStat.employer_name).label("company_name"),
            func.max(UscisEmployerYearlyStat.naics_label).label("industry"),
            UscisEmployerYearlyStat.petitioner_state,
            func.sum(UscisEmployerYearlyStat.total_approvals).label("approvals"),
            func.sum(UscisEmployerYearlyStat.total_denials).label("denials"),
            func.sum(UscisEmployerYearlyStat.new_employment_approval).label("new_employment"),
            func.sum(UscisEmployerYearlyStat.change_employer_approval).label("change_employer"),
            func.sum(UscisEmployerYearlyStat.continuation_approval).label("continuation"),
        )
        .where(UscisEmployerYearlyStat.fiscal_year == fiscal_year, UscisEmployerYearlyStat.company_id.is_not(None))
    )
    if region_states:
        stat_query = stat_query.where(UscisEmployerYearlyStat.petitioner_state.in_(region_states))
    stat_rows = db.execute(stat_query.group_by(UscisEmployerYearlyStat.company_id, UscisEmployerYearlyStat.petitioner_state)).mappings().all()
    company_map: dict[int, dict[str, Any]] = {}
    for row in stat_rows:
        company_id = int(row["company_id"])
        approvals = int(row["approvals"] or 0)
        denials = int(row["denials"] or 0)
        current = company_map.setdefault(
            company_id,
            {
                "company_id": company_id,
                "company_name": row["company_name"],
                "industry": row["industry"],
                "latest_year": fiscal_year,
                "approvals": 0,
                "denials": 0,
                "new_employment": 0,
                "change_employer": 0,
                "continuation": 0,
                "states": {},
            },
        )
        current["approvals"] += approvals
        current["denials"] += denials
        current["new_employment"] += int(row["new_employment"] or 0)
        current["change_employer"] += int(row["change_employer"] or 0)
        current["continuation"] += int(row["continuation"] or 0)
        state = row["petitioner_state"] or ""
        if state:
            current["states"][state] = current["states"].get(state, 0) + approvals

    company_ids = list(company_map)
    companies_by_id = {company.id: company for company in db.scalars(select(Company).where(Company.id.in_(company_ids or [-1]))).all()}
    pursuits_by_company = {pursuit.company_id: pursuit for pursuit in db.scalars(select(CompanyPursuit).where(CompanyPursuit.company_id.in_(company_ids or [-1]))).all()}
    pursuit_ids = [pursuit.id for pursuit in pursuits_by_company.values()]
    latest_snapshots: dict[int, PursuitIntelligenceSnapshot] = {}
    for snapshot in db.scalars(select(PursuitIntelligenceSnapshot).where(PursuitIntelligenceSnapshot.pursuit_id.in_(pursuit_ids or [-1])).order_by(PursuitIntelligenceSnapshot.pursuit_id, PursuitIntelligenceSnapshot.created_at.desc())).all():
        latest_snapshots.setdefault(snapshot.pursuit_id, snapshot)
    job_counts: dict[int, dict[str, int]] = {}
    for job in db.scalars(select(PursuitJobPostingEvidence).where(PursuitJobPostingEvidence.pursuit_id.in_(pursuit_ids or [-1]))).all():
        counts = job_counts.setdefault(job.pursuit_id, {"included": 0, "verified": 0, "estimated": 0, "excluded_seniority": 0})
        if job.included:
            counts["included"] += 1
            if job.experience_evidence_type == "verified_experience_below_8":
                counts["verified"] += 1
            elif job.experience_evidence_type == "estimated_experience_below_8":
                counts["estimated"] += 1
        elif job.exclusion_group == "excluded_jobs_due_to_experience_or_seniority":
            counts["excluded_seniority"] += 1

    region_by_code = {region.code: region for region in db.scalars(select(Region).where(Region.active.is_(True))).all()}
    staff_by_region = _staff_options_by_region(db)
    rows: list[dict[str, Any]] = []
    excluded_high = 0
    for item in company_map.values():
        decisions = item["approvals"] + item["denials"]
        approval_rate = round(item["approvals"] / decisions * 100, 1) if decisions else 0
        if item["approvals"] < min_approvals or approval_rate < min_approval_rate:
            continue
        company = companies_by_id.get(item["company_id"])
        pursuit = pursuits_by_company.get(item["company_id"])
        if source == "promoted" and not pursuit:
            continue
        if source == "uscis_only" and pursuit:
            continue
        top_state = max(item["states"].items(), key=lambda pair: pair[1])[0] if item["states"] else ""
        region_code = region_code_for_state(top_state) or ""
        region = selected_region or region_by_code.get(region_code)
        if region_id and (not region or region.id != region_id):
            continue
        snapshot = latest_snapshots.get(pursuit.id) if pursuit else None
        counts = job_counts.get(pursuit.id, {}) if pursuit else {}
        role_counts = _safe_json_loads(snapshot.role_counts_json, {}) if snapshot else {}
        excluded_seniority = int(counts.get("excluded_seniority", 0)) + _role_count_seniority_exclusions(role_counts)
        eligible_jobs = int(counts.get("included", 0) or (snapshot.total_eligible_usa_job_signal if snapshot else 0))
        verified_jobs = int(counts.get("verified", 0) or (snapshot.verified_below_8_year_usa_jobs if snapshot else 0))
        estimated_jobs = int(counts.get("estimated", 0) or (snapshot.estimated_below_8_year_usa_jobs if snapshot else 0))
        standard_risk, standard_reason = _hiring_standard_risk(excluded_seniority, eligible_jobs, verified_jobs, snapshot)
        if standard_filter == "exclude_very_high" and standard_risk == "Very high":
            excluded_high += 1
            continue
        if standard_filter == "only_high_risk" and standard_risk not in {"High", "Very high"}:
            continue
        watch_score, recommendation, reason = _watch_score_and_recommendation(item["approvals"], approval_rate, item["new_employment"], eligible_jobs, verified_jobs, estimated_jobs, standard_risk, bool(pursuit))
        rows.append(
            {
                **item,
                "company_name": company.name if company else item["company_name"],
                "company": company,
                "pursuit": pursuit,
                "snapshot": snapshot,
                "promoted": bool(pursuit),
                "region_id": region.id if region else None,
                "region_name": region.name if region else "Unmapped",
                "region_code": region.code if region else region_code,
                "states_label": ", ".join(sorted(item["states"])) or "Unmapped",
                "approval_rate": approval_rate,
                "eligible_jobs": eligible_jobs,
                "verified_jobs": verified_jobs,
                "estimated_jobs": estimated_jobs,
                "excluded_seniority": excluded_seniority,
                "standard_risk": standard_risk,
                "standard_reason": standard_reason,
                "watch_score": watch_score,
                "recommendation": recommendation,
                "reason": reason,
                "assigned_staff": pursuit.assigned_staff_name or pursuit.assigned_staff_email if pursuit else "",
                "assigned_staff_email": pursuit.assigned_staff_email if pursuit else "",
            }
        )
    rows = _sort_watchlist_rows(rows, sort)
    rows, staff_rows = _assign_watchlist_capacity(rows, staff_by_region, capacity_per_staff)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    summary = {
        "candidate_count": len(rows),
        "promoted_count": len([row for row in rows if row["promoted"]]),
        "uscis_only_count": len([row for row in rows if not row["promoted"]]),
        "excluded_high_standard": excluded_high,
        "staff_count": len(staff_rows),
        "total_capacity": len(staff_rows) * capacity_per_staff,
        "capacity_per_staff": capacity_per_staff,
        "latest_year": fiscal_year,
    }
    return {"rows": rows, "summary": summary, "staff_rows": staff_rows}


def _company_targeting_queue_report(
    db: Session,
    *,
    fiscal_year: Optional[int],
    region_id: Optional[int],
    staff_email: str,
    source: str,
    min_approvals: int,
    min_approval_rate: int,
    capacity_per_staff: int,
    stage: str,
    sort: str,
) -> dict[str, Any]:
    base = _company_watchlist_report(
        db,
        fiscal_year=fiscal_year,
        region_id=region_id,
        source=source,
        min_approvals=min_approvals,
        min_approval_rate=min_approval_rate,
        standard_filter="include_all",
        capacity_per_staff=capacity_per_staff,
        sort=sort if sort != "stage" else "watch_score",
    )
    rows = [_targeting_queue_row(row) for row in base["rows"]]
    if staff_email:
        needle = staff_email.strip().lower()
        rows = [row for row in rows if (row.get("suggested_staff_email") or row.get("assigned_staff_email") or "").lower() == needle]
    if stage != "all":
        rows = [row for row in rows if row["action_stage"] == stage]
    rows = _sort_targeting_rows(rows, sort)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    staff_options = [
        {"email": row["email"], "label": f"{row['staff']} · {row['region_name']}"}
        for row in base["staff_rows"]
        if row.get("email")
    ]
    summary = {
        **base["summary"],
        "candidate_count": len(rows),
        "actionable_count": len([row for row in rows if row["action_stage"] == "actionable"]),
        "ready_to_promote_count": len([row for row in rows if row["action_stage"] == "ready_to_promote"]),
        "needs_json_count": len([row for row in rows if row["action_stage"] == "needs_json"]),
        "needs_review_count": len([row for row in rows if row["action_stage"] == "needs_review"]),
        "eliminated_count": len([row for row in rows if row["action_stage"] == "eliminated"]),
    }
    return {"rows": rows, "summary": summary, "staff_rows": base["staff_rows"], "staff_options": staff_options}


def _targeting_queue_row(row: dict[str, Any]) -> dict[str, Any]:
    snapshot = _latest_row_snapshot(row)
    best_roles = _targeting_best_roles(snapshot, row)
    tech_stack = _targeting_tech_stack(snapshot)
    use_cases = _targeting_use_cases(snapshot)
    readiness = _targeting_readiness(row, snapshot, best_roles, tech_stack)
    return {
        **row,
        "best_roles": best_roles,
        "best_roles_label": ", ".join(best_roles[:3]) or "Role signal missing",
        "tech_stack": tech_stack,
        "tech_stack_label": ", ".join(tech_stack[:8]) or "Tech stack missing",
        "top_use_cases": use_cases[:4],
        "action_stage": readiness["stage"],
        "stage_label": readiness["label"],
        "next_staff_action": readiness["next_action"],
        "candidate_pitch": readiness["candidate_pitch"],
        "elimination_reason": readiness["elimination_reason"],
        "evidence_quality": readiness["evidence_quality"],
    }


def _latest_row_snapshot(row: dict[str, Any]) -> PursuitIntelligenceSnapshot | None:
    snapshot = row.get("snapshot")
    return snapshot if isinstance(snapshot, PursuitIntelligenceSnapshot) else None


def _targeting_best_roles(snapshot: PursuitIntelligenceSnapshot | None, row: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    for value in [getattr(snapshot, "top_marketing_role", "") if snapshot else "", getattr(snapshot, "second_best_role", "") if snapshot else ""]:
        for part in str(value or "").replace("/", ",").split(","):
            cleaned = part.strip()
            if cleaned and cleaned not in roles:
                roles.append(cleaned)
    role_counts = _safe_json_loads(snapshot.role_counts_json, {}) if snapshot else {}
    if isinstance(role_counts, dict):
        ranked = sorted(
            [item for item in role_counts.values() if isinstance(item, dict)],
            key=lambda item: int(item.get("total_eligible_usa_signal") or 0),
            reverse=True,
        )
        for item in ranked:
            name = item.get("display_name") or ""
            if name and name not in roles and int(item.get("total_eligible_usa_signal") or 0) > 0:
                roles.append(name)
    if not roles and row.get("eligible_jobs"):
        roles.append("Platform / DevOps / Cloud candidates")
    return roles[:5]


def _targeting_tech_stack(snapshot: PursuitIntelligenceSnapshot | None) -> list[str]:
    if not snapshot:
        return []
    summary = _safe_json_loads(snapshot.company_tech_stack_summary_json, {})
    tech: list[str] = []
    if isinstance(summary, dict):
        for key in ["most_frequent_technologies", "cloud_platforms", "devops_tools", "data_platform_tools", "mlops_ai_tools", "observability_sre_tools", "security_governance_tools"]:
            values = summary.get(key) or []
            if isinstance(values, list):
                for value in values:
                    cleaned = str(value).strip()
                    if cleaned and cleaned not in tech:
                        tech.append(cleaned)
    return tech[:18]


def _targeting_use_cases(snapshot: PursuitIntelligenceSnapshot | None) -> list[str]:
    if not snapshot:
        return []
    values = _safe_json_loads(snapshot.company_level_use_cases_json, [])
    return [str(item).strip() for item in values if str(item).strip()] if isinstance(values, list) else []


def _targeting_readiness(row: dict[str, Any], snapshot: PursuitIntelligenceSnapshot | None, roles: list[str], tech_stack: list[str]) -> dict[str, str]:
    if row["standard_risk"] == "Very high":
        return {
            "stage": "eliminated",
            "label": "Eliminate initial pass",
            "next_action": "Skip for the first pass unless a manager sees direct below-8-year evidence.",
            "candidate_pitch": "Not recommended for broad OPT/H1B targeting right now.",
            "elimination_reason": row["standard_reason"],
            "evidence_quality": "High risk",
        }
    if not row.get("promoted"):
        return {
            "stage": "ready_to_promote",
            "label": "Promote to workspace",
            "next_action": "Promote this USCIS-backed company, assign region owner, then import job intelligence JSON.",
            "candidate_pitch": f"USCIS signal is strong enough to research for {', '.join(roles[:2]) or 'target roles'}.",
            "elimination_reason": "",
            "evidence_quality": "USCIS only",
        }
    if not snapshot or not row.get("eligible_jobs"):
        return {
            "stage": "needs_json",
            "label": "Needs job intelligence",
            "next_action": "Run/import OpenAI job intelligence JSON before staff makes the company decision.",
            "candidate_pitch": "USCIS signal exists, but current job-role evidence is missing.",
            "elimination_reason": "",
            "evidence_quality": "Needs enrichment",
        }
    if row.get("verified_jobs", 0) == 0 and row.get("estimated_jobs", 0) > 0:
        return {
            "stage": "needs_review",
            "label": "Needs evidence review",
            "next_action": "Review estimated below-8-year postings and confirm titles are realistic for candidates.",
            "candidate_pitch": f"Potential fit for {', '.join(roles[:2])}; verify experience bar before assigning candidates.",
            "elimination_reason": "",
            "evidence_quality": "Estimated evidence",
        }
    return {
        "stage": "actionable",
        "label": "Candidate-ready",
        "next_action": "Match candidates by role and tech stack, create job opportunities from official postings, and prepare outreach notes.",
        "candidate_pitch": f"Best fit for {', '.join(roles[:2]) or 'target candidates'} with {', '.join(tech_stack[:4]) or 'the imported stack'}.",
        "elimination_reason": "",
        "evidence_quality": "USCIS + job evidence",
    }


def _sort_targeting_rows(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "stage":
        order = {"actionable": 0, "ready_to_promote": 1, "needs_json": 2, "needs_review": 3, "eliminated": 4}
        return sorted(rows, key=lambda row: (order.get(row["action_stage"], 9), -row["watch_score"], row["company_name"].lower()))
    return _sort_watchlist_rows(rows, sort)


def _candidate_company_matches_report(
    db: Session,
    *,
    fiscal_year: Optional[int],
    consultant_id: Optional[int],
    marketing_role_id: Optional[int],
    region_id: Optional[int],
    source: str,
    min_approvals: int,
    min_approval_rate: int,
    min_match_score: int,
) -> dict[str, Any]:
    base = _company_watchlist_report(
        db,
        fiscal_year=fiscal_year,
        region_id=region_id,
        source=source,
        min_approvals=min_approvals,
        min_approval_rate=min_approval_rate,
        standard_filter="include_all",
        capacity_per_staff=100,
        sort="watch_score",
    )
    company_rows = [_targeting_queue_row(row) for row in base["rows"]]
    query = select(ConsultantProfile).outerjoin(MarketingRole).where(ConsultantProfile.active.is_(True))
    if consultant_id:
        query = query.where(ConsultantProfile.id == consultant_id)
    if marketing_role_id:
        query = query.where(ConsultantProfile.marketing_role_id == marketing_role_id)
    consultants = db.scalars(query.order_by(ConsultantProfile.name.asc(), ConsultantProfile.email.asc())).all()
    rows: list[dict[str, Any]] = []
    for consultant in consultants:
        for company_row in company_rows:
            if company_row["action_stage"] == "eliminated":
                continue
            match = _candidate_company_match(consultant, company_row)
            if match["score"] < min_match_score:
                continue
            rows.append(_candidate_company_match_row(consultant, company_row, match))
    rows.sort(key=lambda row: (-row["match_score"], -row["watch_score"], row["consultant_name"].lower(), row["company_name"].lower()))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    summary = {
        "match_count": len(rows),
        "candidate_count": len({row["consultant_id"] for row in rows}),
        "company_count": len({row["company_id"] for row in rows}),
        "strong_match_count": len([row for row in rows if row["match_score"] >= 70]),
        "needs_resume_work_count": len([row for row in rows if "readiness below 60" in row["candidate_gaps"]]),
        "latest_year": fiscal_year,
    }
    return {"rows": rows, "summary": summary, "consultants": consultants}


def _operating_workbench_report(
    db: Session,
    *,
    fiscal_year: Optional[int],
    region_id: Optional[int],
    staff_email: str,
    capacity_per_staff: int,
    min_approvals: int,
    min_approval_rate: int,
) -> dict[str, Any]:
    queue = _company_targeting_queue_report(
        db,
        fiscal_year=fiscal_year,
        region_id=region_id,
        staff_email=staff_email,
        source="all",
        min_approvals=min_approvals,
        min_approval_rate=min_approval_rate,
        capacity_per_staff=capacity_per_staff,
        stage="all",
        sort="stage",
    )
    company_rows = queue["rows"]
    _attach_candidate_matches(db, company_rows, limit_per_company=3)
    staff_user = _user_for_email(db, staff_email)
    owner_filter = (staff_user.name or staff_user.email) if staff_user else staff_email
    journeys = _role_journey_report(db, marketing_role_id=None, owner="", stage="all", min_readiness=0)["rows"]
    if owner_filter:
        needle = owner_filter.lower()
        email_needle = (staff_email or "").lower()
        journeys = [
            row
            for row in journeys
            if needle in (row.get("owner_label") or "").lower() or (email_needle and email_needle in (row.get("owner_label") or "").lower())
        ]
    submission_rows = _workbench_submission_rows(db, owner_filter, staff_email)
    mock_rows = _workbench_mock_rows(db, owner_filter, staff_email)
    company_lanes = _workbench_company_lanes(company_rows)
    company_lane_counts = _workbench_company_lane_counts(company_rows)
    journey_lanes = _workbench_journey_lanes(journeys)
    journey_lane_counts = _workbench_journey_lane_counts(journeys)
    perspectives = _workbench_perspectives(company_rows, journeys, submission_rows, mock_rows)
    priorities = _workbench_priorities(company_rows, journeys, submission_rows, mock_rows)
    role_rows = _workbench_role_rows(company_rows, journeys)
    handoff_rows = _workbench_handoff_rows(company_rows, journeys, submission_rows, mock_rows)
    lifecycle = build_lifecycle_backbone(company_rows, journeys, submission_rows, mock_rows)
    summary = {
        "latest_year": fiscal_year,
        "visible_companies": len(company_rows),
        "candidate_ready": company_lane_counts["candidate_ready"],
        "needs_promotion": company_lane_counts["needs_promotion"],
        "needs_intelligence": company_lane_counts["needs_intelligence"],
        "needs_review": company_lane_counts["needs_review"],
        "eliminated": company_lane_counts["eliminated"],
        "active_journeys": len(journeys),
        "market_ready": len([row for row in journeys if row.get("market_ready")]),
        "open_submissions": len(submission_rows),
        "mock_attention": len([row for row in mock_rows if row["needs_attention"]]),
    }
    staff_options = [
        {"email": row["email"], "label": f"{row['staff']} · {row['region_name']}"}
        for row in queue["staff_rows"]
        if row.get("email")
    ]
    return {
        "summary": summary,
        "perspectives": perspectives,
        "priorities": priorities,
        "role_rows": role_rows,
        "handoff_rows": handoff_rows,
        "company_lanes": company_lanes,
        "company_lane_counts": company_lane_counts,
        "journey_lanes": journey_lanes,
        "journey_lane_counts": journey_lane_counts,
        "submission_rows": submission_rows,
        "mock_rows": mock_rows,
        "lifecycle": lifecycle,
        "staff_rows": queue["staff_rows"],
        "staff_options": staff_options,
        "owner_filter": owner_filter,
    }


def _default_workbench_region_id(user: User, db: Session) -> Optional[int]:
    region_ids = sorted(_staff_region_ids_for_user(db, user))
    if region_ids:
        return region_ids[0]
    first = db.scalar(select(Region).where(Region.active.is_(True)).order_by(Region.name))
    return first.id if first else None


def _staff_region_ids_for_user(db: Session, user: User | object | None) -> set[int]:
    user_id = getattr(user, "id", None)
    if not user_id:
        return set()
    direct_ids = {
        int(region_id)
        for region_id in db.scalars(
            select(StaffRegionAssignment.region_id).where(
                StaffRegionAssignment.user_id == user_id,
                StaffRegionAssignment.active.is_(True),
            )
        ).all()
    }
    group_ids = {
        int(region_id)
        for region_id in db.scalars(
            select(RegionGroupRegion.region_id)
            .join(RegionGroup, RegionGroup.id == RegionGroupRegion.group_id)
            .join(RegionGroupMember, RegionGroupMember.group_id == RegionGroup.id)
            .where(
                RegionGroupMember.user_id == user_id,
                RegionGroupRegion.active.is_(True),
                RegionGroupMember.active.is_(True),
                RegionGroup.active.is_(True),
            )
        ).all()
    }
    return direct_ids | group_ids


def _user_for_email(db: Session, email: str) -> Optional[User]:
    cleaned = (email or "").strip().lower()
    if not cleaned:
        return None
    return db.scalar(select(User).where(func.lower(User.email) == cleaned))


def _workbench_company_lanes(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    lanes = {
        "candidate_ready": [],
        "needs_promotion": [],
        "needs_intelligence": [],
        "needs_review": [],
        "eliminated": [],
    }
    mapping = {
        "actionable": "candidate_ready",
        "ready_to_promote": "needs_promotion",
        "needs_json": "needs_intelligence",
        "needs_review": "needs_review",
        "eliminated": "eliminated",
    }
    for row in rows:
        lane = mapping.get(row.get("action_stage") or "", "needs_review")
        lanes[lane].append(row)
    return {key: value[:8] for key, value in lanes.items()}


def _workbench_company_lane_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in ["candidate_ready", "needs_promotion", "needs_intelligence", "needs_review", "eliminated"]}
    mapping = {
        "actionable": "candidate_ready",
        "ready_to_promote": "needs_promotion",
        "needs_json": "needs_intelligence",
        "needs_review": "needs_review",
        "eliminated": "eliminated",
    }
    for row in rows:
        counts[mapping.get(row.get("action_stage") or "", "needs_review")] += 1
    return counts


def _workbench_journey_lanes(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    lane_keys = {
        "role_intake": "intake",
        "profile_intake": "intake",
        "training_plan": "training",
        "project_story": "positioning",
        "positioning": "positioning",
        "interview_readiness": "mock",
        "final_evidence": "evidence",
        "company_matching": "company_matching",
        "campaign_active": "in_market",
        "submission_pipeline": "submissions",
        "interview_pipeline": "interviews",
    }
    lanes = {key: [] for key in ["intake", "training", "positioning", "mock", "evidence", "company_matching", "in_market", "submissions", "interviews"]}
    for row in rows:
        lanes[lane_keys.get(row.get("journey_stage") or "", "intake")].append(row)
    return {key: value[:6] for key, value in lanes.items()}


def _workbench_journey_lane_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in ["intake", "training", "positioning", "mock", "evidence", "company_matching", "in_market", "submissions", "interviews"]}
    lane_keys = {
        "role_intake": "intake",
        "profile_intake": "intake",
        "training_plan": "training",
        "project_story": "positioning",
        "positioning": "positioning",
        "interview_readiness": "mock",
        "final_evidence": "evidence",
        "company_matching": "company_matching",
        "campaign_active": "in_market",
        "submission_pipeline": "submissions",
        "interview_pipeline": "interviews",
    }
    for row in rows:
        counts[lane_keys.get(row.get("journey_stage") or "", "intake")] += 1
    return counts


def _workbench_submission_rows(db: Session, owner_filter: str, staff_email: str) -> list[dict[str, Any]]:
    query = (
        select(ConsultantSubmission)
        .join(ConsultantProfile, ConsultantProfile.id == ConsultantSubmission.consultant_id)
        .join(JobOpportunity, JobOpportunity.id == ConsultantSubmission.job_id)
        .where(ConsultantSubmission.status.in_([SubmissionStatus.SUBMITTED.value, SubmissionStatus.CLIENT_REVIEW.value, SubmissionStatus.INTERVIEW.value, SubmissionStatus.OFFER.value]))
    )
    owner_needle = (owner_filter or "").strip().lower()
    email_needle = (staff_email or "").strip().lower()
    if owner_needle or email_needle:
        query = query.where(
            or_(
                func.lower(ConsultantProfile.staff_owner).contains(owner_needle or email_needle),
                func.lower(ConsultantProfile.recruiter_owner).contains(owner_needle or email_needle),
                func.lower(ConsultantProfile.staff_owner).contains(email_needle or owner_needle),
                func.lower(ConsultantProfile.recruiter_owner).contains(email_needle or owner_needle),
            )
        )
    submissions = db.scalars(query.order_by(ConsultantSubmission.updated_at.desc()).limit(12)).all()
    rows = []
    for submission in submissions:
        status_value = getattr(submission.status, "value", submission.status)
        rows.append(
            {
                "id": submission.id,
                "consultant_id": submission.consultant_id,
                "consultant_name": submission.consultant.name or submission.consultant.email,
                "job_title": submission.job.title,
                "company_name": submission.job.company.name if submission.job and submission.job.company else "",
                "status": str(status_value).replace("_", " ").title(),
                "next_step": submission.next_step or "Record feedback and prepare the next follow-up.",
                "submitted_on": submission.submitted_on,
            }
        )
    return rows


def _workbench_mock_rows(db: Session, owner_filter: str, staff_email: str) -> list[dict[str, Any]]:
    query = select(MockInterview).join(ConsultantProfile, ConsultantProfile.id == MockInterview.consultant_id)
    owner_needle = (owner_filter or "").strip().lower()
    email_needle = (staff_email or "").strip().lower()
    if owner_needle or email_needle:
        query = query.where(
            or_(
                func.lower(ConsultantProfile.staff_owner).contains(owner_needle or email_needle),
                func.lower(ConsultantProfile.recruiter_owner).contains(owner_needle or email_needle),
                func.lower(ConsultantProfile.staff_owner).contains(email_needle or owner_needle),
                func.lower(ConsultantProfile.recruiter_owner).contains(email_needle or owner_needle),
            )
        )
    mocks = db.scalars(query.order_by(MockInterview.scheduled_on.asc().nullslast(), MockInterview.updated_at.desc()).limit(12)).all()
    attention_statuses = {
        MockInterviewStatus.PLANNED.value,
        MockInterviewStatus.PENDING_ACK.value,
        MockInterviewStatus.RESCHEDULE_REQUESTED.value,
        MockInterviewStatus.WAITING_FEEDBACK.value,
        MockInterviewStatus.NEEDS_WORK.value,
    }
    rows = []
    for mock in mocks:
        status_value = getattr(mock.status, "value", mock.status)
        rows.append(
            {
                "id": mock.id,
                "consultant_id": mock.consultant_id,
                "consultant_name": mock.consultant.name or mock.consultant.email,
                "role": mock.marketing_role.name if mock.marketing_role else mock.role_snapshot or "Role not set",
                "scheduled_on": mock.scheduled_on,
                "scheduled_time": mock.scheduled_time,
                "status": str(status_value).replace("_", " ").title(),
                "needs_attention": status_value in attention_statuses,
                "next_action": _mock_workbench_next_action(mock),
            }
        )
    return rows


def _mock_workbench_next_action(mock: MockInterview) -> str:
    status = str(getattr(mock.status, "value", mock.status))
    if status == MockInterviewStatus.PENDING_ACK.value:
        return "Confirm attendance and meeting details."
    if status == MockInterviewStatus.RESCHEDULE_REQUESTED.value:
        return "Pick a new slot before the consultant loses momentum."
    if status == MockInterviewStatus.WAITING_FEEDBACK.value:
        return "Capture feedback and convert it into checklist tasks."
    if status == MockInterviewStatus.NEEDS_WORK.value:
        return "Assign role-specific practice before the next submission."
    if status == MockInterviewStatus.MARKET_READY.value:
        return "Use this result in positioning and submission notes."
    return "Prepare questions from the target role and recent company postings."


def _workbench_perspectives(company_rows: list[dict[str, Any]], journeys: list[dict[str, Any]], submissions: list[dict[str, Any]], mocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": "Consultant",
            "metric": len(journeys),
            "label": "active role journeys",
            "focus": "Keep every consultant on a visible path from role assignment to company-specific submissions.",
            "gap": _first_gap([row.get("gaps_label", "") for row in journeys], "No consultant blockers captured yet."),
            "action": "Open the checklist when readiness, project story, mock result, or positioning is incomplete.",
        },
        {
            "title": "Region",
            "metric": len(company_rows),
            "label": "companies in queue",
            "focus": "Use latest USCIS year as source of truth and keep staff capacity near 100 companies each.",
            "gap": f"{len([row for row in company_rows if row.get('action_stage') == 'ready_to_promote'])} companies still need promotion.",
            "action": "Promote strong USCIS-only companies, then import job intelligence before assigning candidates.",
        },
        {
            "title": "Marketing Role",
            "metric": len({row.get("target_role") for row in journeys if row.get("target_role")}),
            "label": "roles represented",
            "focus": "Make training, resume bullets, mock questions, and company targeting role-specific from day 1.",
            "gap": f"{len([row for row in journeys if row.get('journey_stage') in {'training_plan', 'project_story', 'positioning'}])} journeys need role-positioning work.",
            "action": "Use the positioning brief to convert skills into a company-ready role narrative.",
        },
        {
            "title": "Mock Interview",
            "metric": len([row for row in mocks if row["needs_attention"]]),
            "label": "need attention",
            "focus": "Mocks should validate the exact story needed for the next company or submission.",
            "gap": f"{len([row for row in journeys if row.get('journey_stage') == 'interview_readiness'])} consultants are waiting on interview readiness.",
            "action": "Schedule, confirm, or close feedback so mock results move the consultant forward.",
        },
        {
            "title": "Submissions",
            "metric": len(submissions),
            "label": "open follow-ups",
            "focus": "Every submission should have a tailored resume reason, company evidence, and next step.",
            "gap": _first_gap([row.get("next_step", "") for row in submissions], "No open submission next steps found."),
            "action": "Review stale next steps and connect feedback back to training or positioning.",
        },
        {
            "title": "Interviews",
            "metric": len([row for row in submissions if row.get("status") in {"Interview", "Offer"}]),
            "label": "active interview pipeline",
            "focus": "Interview prep should use the actual company postings, tech stack, and role use cases.",
            "gap": f"{len([row for row in mocks if row.get('status') == 'Waiting Feedback'])} mock/interview items are waiting for feedback.",
            "action": "Prepare company-specific story flows before each round and record feedback after each round.",
        },
    ]


def _workbench_priorities(company_rows: list[dict[str, Any]], journeys: list[dict[str, Any]], submissions: list[dict[str, Any]], mocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in company_rows:
        stage = row.get("action_stage") or ""
        if stage in {"actionable", "ready_to_promote", "needs_json", "needs_review"}:
            score = int(row.get("watch_score") or 0)
            priority = 95 if stage == "actionable" else 86 if stage == "ready_to_promote" else 78 if stage == "needs_json" else 68
            items.append(
                {
                    "priority": priority + min(10, score // 10),
                    "type": "Company",
                    "title": row.get("company_name") or "Company",
                    "status": row.get("stage_label") or "Review",
                    "detail": row.get("candidate_pitch") or row.get("reason") or "",
                    "next_action": row.get("next_staff_action") or "",
                    "url": f"/pursuits/{row['pursuit'].id}?tab=intelligence" if row.get("pursuit") else f"/companies/{row.get('company_id')}/uscis",
                }
            )
    for row in journeys:
        stage = row.get("journey_stage") or ""
        if stage in {"training_plan", "project_story", "positioning", "interview_readiness", "company_matching"} or int(row.get("readiness_score") or 0) < 60:
            items.append(
                {
                    "priority": 88 if stage in {"positioning", "interview_readiness"} else 72,
                    "type": "Consultant",
                    "title": row.get("consultant_name") or "Consultant",
                    "status": row.get("stage_label") or "Journey",
                    "detail": row.get("gaps_label") or "",
                    "next_action": row.get("next_action") or "",
                    "url": f"/consultants/{row.get('consultant_id')}/journey",
                }
            )
    for row in submissions:
        items.append(
            {
                "priority": 82 if row.get("status") == "Interview" else 70,
                "type": "Submission",
                "title": f"{row.get('consultant_name')} · {row.get('company_name')}",
                "status": row.get("status") or "Submitted",
                "detail": row.get("job_title") or "",
                "next_action": row.get("next_step") or "",
                "url": f"/consultants/{row.get('consultant_id')}",
            }
        )
    for row in mocks:
        if row.get("needs_attention"):
            items.append(
                {
                    "priority": 84 if row.get("status") in {"Waiting Feedback", "Needs Work"} else 66,
                    "type": "Mock",
                    "title": row.get("consultant_name") or "Mock interview",
                    "status": row.get("status") or "Mock",
                    "detail": row.get("role") or "",
                    "next_action": row.get("next_action") or "",
                    "url": f"/mock-interviews/{row.get('id')}",
                }
            )
    items.sort(key=lambda item: (-int(item["priority"]), item["type"], item["title"]))
    for index, item in enumerate(items[:10], start=1):
        item["rank"] = index
    return items[:10]


def _workbench_role_rows(company_rows: list[dict[str, Any]], journeys: list[dict[str, Any]]) -> list[dict[str, Any]]:
    role_map: dict[str, dict[str, Any]] = {}
    for row in journeys:
        role = row.get("target_role") or "Role not set"
        item = role_map.setdefault(role, {"role": role, "consultants": 0, "ready": 0, "companies": 0, "needs_training": 0, "needs_positioning": 0, "needs_mock": 0})
        item["consultants"] += 1
        if row.get("market_ready"):
            item["ready"] += 1
        if row.get("journey_stage") == "training_plan":
            item["needs_training"] += 1
        if row.get("journey_stage") in {"project_story", "positioning"}:
            item["needs_positioning"] += 1
        if row.get("journey_stage") == "interview_readiness":
            item["needs_mock"] += 1
    for row in company_rows:
        for role in row.get("best_roles", []) or ["Role signal missing"]:
            item = role_map.setdefault(role, {"role": role, "consultants": 0, "ready": 0, "companies": 0, "needs_training": 0, "needs_positioning": 0, "needs_mock": 0})
            if row.get("action_stage") == "actionable":
                item["companies"] += 1
    rows = sorted(role_map.values(), key=lambda item: (-item["consultants"], -item["companies"], item["role"].lower()))
    return rows[:8]


def _workbench_handoff_rows(company_rows: list[dict[str, Any]], journeys: list[dict[str, Any]], submissions: list[dict[str, Any]], mocks: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "from": "USCIS import",
            "to": "Region owner",
            "ready": str(len([row for row in company_rows if row.get("action_stage") == "ready_to_promote"])),
            "handoff": "Promote high-signal companies, carry latest-year filings into the workspace, and assign owner.",
        },
        {
            "from": "Region owner",
            "to": "Company intelligence",
            "ready": str(len([row for row in company_rows if row.get("action_stage") == "needs_json"])),
            "handoff": "Import JSON from current job postings before staff decides candidate fit.",
        },
        {
            "from": "Training",
            "to": "Positioning",
            "ready": str(len([row for row in journeys if row.get("journey_stage") in {"project_story", "positioning"}])),
            "handoff": "Convert project work into resume headline, bullets, interview story, and company-specific pitch.",
        },
        {
            "from": "Positioning",
            "to": "Mock interview",
            "ready": str(len([row for row in journeys if row.get("journey_stage") == "interview_readiness"])),
            "handoff": "Validate role ownership, project story, and job-posting use cases before active submission.",
        },
        {
            "from": "Mock interview",
            "to": "Submissions",
            "ready": str(len([row for row in journeys if row.get("journey_stage") in {"company_matching", "campaign_active"}])),
            "handoff": "Create campaigns only when role fit and evidence are clear without overclaiming.",
        },
        {
            "from": "Submissions",
            "to": "Interview prep",
            "ready": str(len([row for row in submissions if row.get("status") in {"Interview", "Offer"}]) + len([row for row in mocks if row.get("status") == "Waiting Feedback"])),
            "handoff": "Feed outcome and feedback back into checklist, mock questions, and the next company pitch.",
        },
    ]


def _first_gap(values: list[str], fallback: str) -> str:
    for value in values:
        cleaned = (value or "").strip()
        if cleaned and cleaned.lower() != "no major blockers":
            return cleaned
    return fallback


def _role_journey_report(
    db: Session,
    *,
    marketing_role_id: Optional[int],
    owner: str,
    stage: str,
    min_readiness: int,
) -> dict[str, Any]:
    query = select(ConsultantProfile).outerjoin(MarketingRole).where(ConsultantProfile.active.is_(True))
    if marketing_role_id:
        query = query.where(ConsultantProfile.marketing_role_id == marketing_role_id)
    owner_needle = owner.strip().lower()
    if owner_needle:
        query = query.where(
            or_(
                func.lower(ConsultantProfile.staff_owner) == owner_needle,
                func.lower(ConsultantProfile.recruiter_owner) == owner_needle,
            )
        )
    consultants = db.scalars(query.order_by(ConsultantProfile.name.asc(), ConsultantProfile.email.asc())).all()
    consultant_ids = [consultant.id for consultant in consultants]
    campaigns_by_consultant: dict[int, list[TargetingCampaign]] = {}
    for campaign in db.scalars(select(TargetingCampaign).where(TargetingCampaign.consultant_id.in_(consultant_ids or [-1])).order_by(TargetingCampaign.updated_at.desc())).all():
        campaigns_by_consultant.setdefault(campaign.consultant_id, []).append(campaign)
    targets_by_campaign: dict[int, list[TargetingCampaignTarget]] = {}
    campaign_ids = [campaign.id for campaigns in campaigns_by_consultant.values() for campaign in campaigns]
    for target in db.scalars(select(TargetingCampaignTarget).where(TargetingCampaignTarget.campaign_id.in_(campaign_ids or [-1]))).all():
        targets_by_campaign.setdefault(target.campaign_id, []).append(target)
    submissions_by_consultant: dict[int, list[ConsultantSubmission]] = {}
    for submission in db.scalars(select(ConsultantSubmission).where(ConsultantSubmission.consultant_id.in_(consultant_ids or [-1])).order_by(ConsultantSubmission.updated_at.desc())).all():
        submissions_by_consultant.setdefault(submission.consultant_id, []).append(submission)
    resumes_by_consultant: dict[int, list[ResumeVersion]] = {}
    for resume in db.scalars(select(ResumeVersion).where(ResumeVersion.consultant_id.in_(consultant_ids or [-1]), ResumeVersion.active.is_(True)).order_by(ResumeVersion.updated_at.desc())).all():
        resumes_by_consultant.setdefault(resume.consultant_id, []).append(resume)
    mocks_by_consultant: dict[int, list[MockInterview]] = {}
    for mock in db.scalars(select(MockInterview).where(MockInterview.consultant_id.in_(consultant_ids or [-1])).order_by(MockInterview.scheduled_on.desc().nullslast(), MockInterview.updated_at.desc())).all():
        mocks_by_consultant.setdefault(mock.consultant_id, []).append(mock)
    training_by_key: dict[tuple[int, str], TrainingProgram] = {}
    training_by_role: dict[int, TrainingProgram] = {}
    for program in db.scalars(select(TrainingProgram).where(TrainingProgram.active.is_(True)).order_by(TrainingProgram.display_order, TrainingProgram.title)).all():
        training_by_key.setdefault((program.marketing_role_id, (program.industry_domain or "").strip().lower()), program)
        training_by_role.setdefault(program.marketing_role_id, program)

    rows: list[dict[str, Any]] = []
    for consultant in consultants:
        persisted_journey = _ensure_consultant_role_journey(db, consultant)
        row = _role_journey_row(
            consultant,
            campaigns_by_consultant.get(consultant.id, []),
            targets_by_campaign,
            submissions_by_consultant.get(consultant.id, []),
            resumes_by_consultant.get(consultant.id, []),
            mocks_by_consultant.get(consultant.id, []),
            training_by_key,
            training_by_role,
        )
        activity_rows = db.scalars(select(ConsultantJourneyActivity).where(ConsultantJourneyActivity.journey_id == persisted_journey.id)).all()
        row["journey_id"] = persisted_journey.id
        row["checklist_completed"] = len([item for item in activity_rows if item.status == ConsultantJourneyActivityStatus.COMPLETED.value])
        row["checklist_total"] = len(activity_rows)
        row["checklist_blocked"] = len([item for item in activity_rows if item.status == ConsultantJourneyActivityStatus.BLOCKED.value])
        row["checklist_percent"] = persisted_journey.readiness_score
        row["stage_label"] = dict(_role_journey_stage_options()).get(persisted_journey.current_stage, row["stage_label"])
        row["journey_stage"] = persisted_journey.current_stage
        row["stage_order"] = _role_journey_stage_order(persisted_journey.current_stage)
        row["readiness_score"] = persisted_journey.readiness_score
        row["next_action"] = persisted_journey.next_action or row["next_action"]
        if stage != "all" and row["journey_stage"] != stage:
            continue
        if row["readiness_score"] < min_readiness:
            continue
        rows.append(row)
    rows.sort(key=lambda row: (row["stage_order"], -row["readiness_score"], row["consultant_name"].lower()))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    stage_rows = []
    for value, label in _role_journey_stage_options():
        if value == "all":
            continue
        stage_rows.append(
            {
                "stage": value,
                "label": label,
                "count": len([row for row in rows if row["journey_stage"] == value]),
            }
        )
    owner_values = sorted(
        {
            value.strip()
            for consultant in consultants
            for value in [consultant.staff_owner or "", consultant.recruiter_owner or ""]
            if value and value.strip()
        },
        key=str.lower,
    )
    summary = {
        "consultant_count": len(rows),
        "market_ready_count": len([row for row in rows if row["market_ready"]]),
        "needs_training_count": len([row for row in rows if row["journey_stage"] == "training_plan"]),
        "needs_positioning_count": len([row for row in rows if row["journey_stage"] in {"project_story", "positioning"}]),
        "needs_campaign_count": len([row for row in rows if row["journey_stage"] == "company_matching"]),
        "in_market_count": len([row for row in rows if row["journey_stage"] in {"campaign_active", "submission_pipeline", "interview_pipeline"}]),
        "offer_count": len([row for row in rows if row["journey_stage"] == "offer"]),
        "placed_count": len([row for row in rows if row["journey_stage"] in {"placement", "post_placement"}]),
    }
    return {"rows": rows, "summary": summary, "stage_rows": stage_rows, "owner_options": owner_values}


def _consultant_onboarding_workbench_context(
    db: Session,
    user: User,
    *,
    stage: str = "all",
    owner: str = "",
    q: str = "",
    min_readiness: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    query = select(ConsultantProfile).where(ConsultantProfile.active.is_(True))
    visibility_clause = _consultant_visibility_clause(user)
    if visibility_clause is not None:
        query = query.where(visibility_clause)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(
            func.lower(ConsultantProfile.name).like(pattern)
            | func.lower(ConsultantProfile.email).like(pattern)
            | func.lower(ConsultantProfile.primary_skills).like(pattern)
            | func.lower(ConsultantProfile.target_industry_domain).like(pattern)
        )
    if owner:
        owner_key = owner.strip().lower()
        query = query.where(
            or_(
                func.lower(ConsultantProfile.staff_owner) == owner_key,
                func.lower(ConsultantProfile.recruiter_owner) == owner_key,
            )
        )
    consultants = db.scalars(query.order_by(ConsultantProfile.updated_at.desc(), ConsultantProfile.name.asc()).limit(max(limit, 1) * 4)).all()
    rows: list[dict[str, Any]] = []
    today = date.today()
    stage_labels = dict(_role_journey_stage_options())
    for consultant in consultants:
        journey = _ensure_consultant_role_journey(db, consultant)
        activities = db.scalars(select(ConsultantJourneyActivity).where(ConsultantJourneyActivity.journey_id == journey.id).order_by(ConsultantJourneyActivity.sequence)).all()
        if stage != "all" and journey.current_stage != stage:
            continue
        if journey.readiness_score < min_readiness:
            continue
        active_activities = [
            activity
            for activity in activities
            if activity.status not in {ConsultantJourneyActivityStatus.COMPLETED.value, ConsultantJourneyActivityStatus.SKIPPED.value}
        ]
        blocked = [activity for activity in activities if activity.status == ConsultantJourneyActivityStatus.BLOCKED.value]
        overdue = [
            activity
            for activity in active_activities
            if activity.due_date and activity.due_date < today
        ]
        evidence_package = _final_evidence_package_context(db, consultant.id, activities)
        program = _consultant_training_program(db, consultant)
        latest_mock = db.scalar(
            select(MockInterview)
            .where(MockInterview.consultant_id == consultant.id)
            .order_by(MockInterview.scheduled_on.desc().nullslast(), MockInterview.updated_at.desc())
        )
        rows.append(
            {
                "consultant": consultant,
                "journey": journey,
                "stage_label": stage_labels.get(journey.current_stage, journey.current_stage.replace("_", " ").title()),
                "next_activity": active_activities[0] if active_activities else None,
                "active_count": len(active_activities),
                "blocked_count": len(blocked),
                "overdue_count": len(overdue),
                "completed_count": len([activity for activity in activities if activity.status == ConsultantJourneyActivityStatus.COMPLETED.value]),
                "activity_count": len(activities),
                "evidence_package": evidence_package,
                "training_program": program,
                "latest_mock": latest_mock,
            }
        )
        if len(rows) >= limit:
            break
    summary = {
        "consultants": len(rows),
        "blocked": len([row for row in rows if row["blocked_count"]]),
        "overdue": len([row for row in rows if row["overdue_count"]]),
        "missing_owner": len([row for row in rows if not row["consultant"].staff_owner.strip()]),
        "missing_training": len([row for row in rows if not row["training_program"]]),
        "evidence_ready": len([row for row in rows if row["evidence_package"]["ready"]]),
        "market_ready": len([row for row in rows if marketing_ready_context(row["consultant"])["ready"]]),
        "offers": len([row for row in rows if row["journey"].current_stage == "offer"]),
        "placed": len([row for row in rows if row["journey"].current_stage in {"placement", "post_placement"}]),
    }
    pipeline = _consultant_placement_pipeline_context(rows)
    stage_rows = [
        {"stage": value, "label": label, "count": len([row for row in rows if row["journey"].current_stage == value])}
        for value, label in _role_journey_stage_options()
        if value != "all"
    ]
    owner_options = sorted(
        {
            value.strip()
            for row in rows
            for value in [row["consultant"].staff_owner or "", row["consultant"].recruiter_owner or ""]
            if value and value.strip()
        },
        key=str.lower,
    )
    rows.sort(key=lambda row: (-row["blocked_count"], -row["overdue_count"], row["journey"].readiness_score, (row["consultant"].name or row["consultant"].email or "").lower()))
    return {"rows": rows, "summary": summary, "pipeline": pipeline, "stage_rows": stage_rows, "owner_options": owner_options, "stage_options": _role_journey_stage_options()}


def _consultant_placement_pipeline_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = [
        {
            "key": "role_lock",
            "label": "Role/domain locked",
            "goal": "Every active consultant has one target marketing role, one domain, and one staff owner.",
            "ready": lambda row: bool(row["consultant"].marketing_role_id and row["consultant"].target_industry_domain and row["consultant"].staff_owner.strip()),
            "route": "Fix missing role, domain, or owner before any training beyond Basics.",
        },
        {
            "key": "basics",
            "label": "Basics complete",
            "goal": "Foundation is complete and the matching role/domain training program can open.",
            "ready": lambda row: bool(row["consultant"].basics_prep_complete and row["consultant"].training_plan_assigned),
            "route": "Keep consultant in Basics Prep until the completion gate assigns training.",
        },
        {
            "key": "role_training",
            "label": "Role training progressing",
            "goal": "Project story, glossary, and role/domain use cases are moving toward interview language.",
            "ready": lambda row: bool(row["consultant"].training_plan_assigned and row["consultant"].latest_project_updated and row["consultant"].project_story_validated),
            "route": "Use the assigned training program to finish project story and evidence gaps.",
        },
        {
            "key": "positioning",
            "label": "Resume and evidence ready",
            "goal": "Resume, project proof, and final evidence package are clean enough for mock interviews.",
            "ready": lambda row: bool(row["consultant"].resume_tailoring_complete and row["consultant"].project_story_validated and row["evidence_package"]["percent"] >= 75),
            "route": "Finish resume/project evidence before mock interview approval.",
        },
        {
            "key": "mock",
            "label": "Mock passed",
            "goal": "Consultant can explain role, domain, architecture, failures, and boundaries under interview pressure.",
            "ready": lambda row: bool(row["consultant"].mock_interview_passed),
            "route": "Schedule or repeat mock until project and technical answers are credible.",
        },
        {
            "key": "marketing",
            "label": "Marketing ready",
            "goal": "Submissions and campaigns are unlocked only after owner approval and final evidence readiness.",
            "ready": lambda row: bool(marketing_ready_context(row["consultant"])["ready"]),
            "route": "Do not submit until Marketing Ready gate is complete.",
        },
    ]
    cards = []
    total = len(rows)
    for bucket in buckets:
        ready_rows = [row for row in rows if bucket["ready"](row)]
        cards.append(
            {
                "key": bucket["key"],
                "label": bucket["label"],
                "goal": bucket["goal"],
                "ready_count": len(ready_rows),
                "blocked_count": total - len(ready_rows),
                "percent": round(len(ready_rows) / total * 100) if total else 0,
                "route": bucket["route"],
            }
        )
    by_stage = {row["journey"].current_stage: row for row in rows}
    first_bottleneck = next((card for card in cards if card["blocked_count"]), cards[-1] if cards else None)
    return {
        "goal": "Four-month placement pipeline",
        "summary": "Basics first, then assigned role/domain training, project evidence, mock approval, marketing activity, interviews, offer, and post-placement support.",
        "cards": cards,
        "first_bottleneck": first_bottleneck,
        "ready_for_marketing": len([row for row in rows if marketing_ready_context(row["consultant"])["ready"]]),
        "in_interviews": len([row for row in rows if row["journey"].current_stage in {"interview_pipeline", "offer"}]),
        "placed": len([row for row in rows if row["journey"].current_stage in {"placement", "post_placement"}]),
        "role_locked": len([row for row in rows if row["consultant"].marketing_role_id and row["consultant"].target_industry_domain]),
        "stage_has_rows": bool(by_stage),
    }


def _role_journey_row(
    consultant: ConsultantProfile,
    campaigns: list[TargetingCampaign],
    targets_by_campaign: dict[int, list[TargetingCampaignTarget]],
    submissions: list[ConsultantSubmission],
    resumes: list[ResumeVersion],
    mocks: list[MockInterview],
    training_by_key: dict[tuple[int, str], TrainingProgram],
    training_by_role: dict[int, TrainingProgram],
) -> dict[str, Any]:
    role = consultant.marketing_role
    domain_key = (consultant.target_industry_domain or "").strip().lower()
    program = training_by_key.get((consultant.marketing_role_id or 0, domain_key)) if consultant.marketing_role_id else None
    program = program or training_by_role.get(consultant.marketing_role_id or 0)
    readiness = _candidate_readiness_score(consultant)
    active_campaigns = [campaign for campaign in campaigns if campaign.status in {TargetingCampaignStatus.PLANNED.value, TargetingCampaignStatus.ACTIVE.value}]
    campaign_targets = [target for campaign in campaigns for target in targets_by_campaign.get(campaign.id, [])]
    submitted_targets = [target for target in campaign_targets if target.status in {TargetingCampaignTargetStatus.SUBMITTED.value, TargetingCampaignTargetStatus.INTERVIEW.value}]
    active_submissions = [submission for submission in submissions if submission.status in {SubmissionStatus.SUBMITTED.value, SubmissionStatus.CLIENT_REVIEW.value, SubmissionStatus.INTERVIEW.value, SubmissionStatus.OFFER.value}]
    interview_submissions = [submission for submission in submissions if submission.status in {SubmissionStatus.INTERVIEW.value, SubmissionStatus.OFFER.value}]
    latest_campaign = active_campaigns[0] if active_campaigns else (campaigns[0] if campaigns else None)
    latest_mock = mocks[0] if mocks else None
    gaps = _role_journey_gaps(consultant, program, campaigns, submissions, readiness)
    stage, stage_label, stage_order, next_action = _role_journey_stage(consultant, program, campaigns, submitted_targets, submissions, active_submissions, interview_submissions)
    positioning = _role_positioning_brief(consultant, program, gaps)
    return {
        "consultant_id": consultant.id,
        "consultant_name": consultant.name or consultant.email,
        "consultant_email": consultant.email,
        "target_role": role.name if role else "Role not set",
        "target_domain": consultant.target_industry_domain or "Domain not set",
        "journey_stage": stage,
        "stage_label": stage_label,
        "stage_order": stage_order,
        "readiness_score": readiness,
        "market_ready": bool(marketing_ready_context(consultant)["ready"]),
        "training_program": program.title or f"{program.marketing_role.name} - {program.industry_domain}" if program else "No role/domain program assigned",
        "training_program_id": program.id if program else None,
        "campaign_count": len(campaigns),
        "active_campaign_count": len(active_campaigns),
        "latest_campaign_id": latest_campaign.id if latest_campaign else None,
        "latest_campaign_name": latest_campaign.name if latest_campaign else "",
        "target_count": len(campaign_targets),
        "submitted_target_count": len(submitted_targets),
        "submission_count": len(submissions),
        "active_submission_count": len(active_submissions),
        "resume_count": len(resumes),
        "latest_resume_id": resumes[0].id if resumes else None,
        "mock_count": len(mocks),
        "latest_mock_id": latest_mock.id if latest_mock else None,
        "latest_mock_status": latest_mock.status if latest_mock else "Not scheduled",
        "next_action": next_action,
        "gaps": gaps,
        "gaps_label": ", ".join(gaps) or "No major blockers",
        "owner_label": _role_journey_owner_label(consultant),
        "positioning": positioning,
        "primary_skills": _split_training_items(consultant.primary_skills)[:8],
        "campaign_url": f"/targeting-campaigns/{latest_campaign.id}" if latest_campaign else "",
        "match_url": f"/reports/candidate-company-matches?consultant_id={consultant.id}&min_match_score=35",
    }


def _role_journey_stage(
    consultant: ConsultantProfile,
    program: TrainingProgram | None,
    campaigns: list[TargetingCampaign],
    submitted_targets: list[TargetingCampaignTarget],
    submissions: list[ConsultantSubmission],
    active_submissions: list[ConsultantSubmission],
    interview_submissions: list[ConsultantSubmission],
) -> tuple[str, str, int, str]:
    if consultant.marketing_status == "post_placement":
        return "post_placement", "Post-placement support", 120, "Track first-week support, manager feedback, escalation path, and consultant stability."
    if consultant.marketing_status == "placed" or consultant.placement_company:
        return "placement", "Placement", 110, "Confirm placement details, joining plan, and post-placement support notes."
    if consultant.marketing_status == "offer" or any(str(getattr(submission.status, "value", submission.status)) == SubmissionStatus.OFFER.value for submission in submissions):
        return "offer", "Offer", 100, "Review offer, start date, client expectations, and joining plan."
    if not consultant.marketing_role_id:
        return "role_intake", "Role intake", 10, "Assign a Mintel marketing role before training or company targeting starts."
    if not consultant.profile_intake_complete or not consultant.primary_skills.strip():
        return "profile_intake", "Profile intake", 20, "Complete profile intake, work authorization, location, and primary skills."
    if not consultant.basics_prep_complete:
        return "training_plan", "Training plan", 30, "Complete Basics Preparation before assigning role/domain training."
    if not consultant.training_plan_assigned or not program:
        return "training_plan", "Training plan", 30, "Assign the role/domain training program and convert gaps into weekly deliverables."
    if not consultant.latest_project_updated or not consultant.project_story_validated:
        return "project_story", "Project story", 40, "Build the role-specific project story, ownership boundary, and interview-use case examples."
    if not consultant.base_resume_received or not consultant.resume_tailoring_complete:
        return "positioning", "Positioning", 50, "Prepare the role positioning brief, resume headline, project bullets, and recruiter pitch."
    if not consultant.glossary_review_complete or not consultant.mock_interview_passed:
        return "interview_readiness", "Interview readiness", 60, "Finish glossary review and mock interview before active company targeting."
    if not consultant.marketing_brief_ready:
        return "final_evidence", "Final evidence package", 62, "Complete the final evidence package before active company targeting."
    if interview_submissions:
        return "interview_pipeline", "Interview pipeline", 90, "Prepare company-specific interview stories and close feedback gaps after each round."
    if active_submissions or submissions or submitted_targets:
        return "submission_pipeline", "Submission pipeline", 80, "Track submissions, tailor follow-ups, and keep company-specific role evidence tied to posted jobs."
    if campaigns:
        return "campaign_active", "Campaign active", 70, "Work the targeting campaign: verify jobs, tailor resume, create submissions, and record outcomes."
    return "company_matching", "Company matching", 65, "Use Candidate Company Matches to create a role-specific company campaign."


def _role_journey_gaps(consultant: ConsultantProfile, program: TrainingProgram | None, campaigns: list[TargetingCampaign], submissions: list[ConsultantSubmission], readiness: int) -> list[str]:
    gaps = []
    if not consultant.staff_owner.strip():
        gaps.append("staff owner missing")
    if not consultant.marketing_role_id:
        gaps.append("target role missing")
    if not consultant.profile_intake_complete:
        gaps.append("profile intake incomplete")
    if not consultant.work_authorization.strip():
        gaps.append("work auth missing")
    if not consultant.primary_skills.strip():
        gaps.append("skills missing")
    if not consultant.basics_prep_complete:
        gaps.append("basics prep incomplete")
    if not consultant.training_plan_assigned or not program:
        gaps.append("training plan missing")
    if not consultant.project_story_validated:
        gaps.append("project story not validated")
    if not consultant.resume_tailoring_complete:
        gaps.append("resume tailoring incomplete")
    if not consultant.marketing_brief_ready:
        gaps.append("final evidence package missing")
    if not consultant.mock_interview_passed:
        gaps.append("mock interview not passed")
    if readiness < 60:
        gaps.append("readiness below 60")
    if consultant.mock_interview_passed and consultant.marketing_brief_ready and not campaigns:
        gaps.append("no targeting campaign")
    if campaigns and not submissions:
        gaps.append("no submissions yet")
    return gaps[:6]


def _role_positioning_brief(consultant: ConsultantProfile, program: TrainingProgram | None, gaps: list[str]) -> dict[str, Any]:
    role_name = consultant.marketing_role.name if consultant.marketing_role else "target Mintel role"
    domain = consultant.target_industry_domain or consultant.latest_project_domain or (program.industry_domain if program else "")
    skills = _split_training_items(consultant.primary_skills)[:6]
    project = consultant.latest_project_title or (program.title if program else "")
    headline_parts = [role_name]
    if domain:
        headline_parts.append(domain)
    if skills:
        headline_parts.append(", ".join(skills[:3]))
    return {
        "headline": " | ".join(headline_parts),
        "pitch": f"Position as a {role_name} who can explain ownership boundaries, production support, and measurable delivery evidence" + (f" in {domain}." if domain else "."),
        "project_story": project or "Project story still needs to be selected and validated.",
        "skills_to_emphasize": skills,
        "interview_focus": [
            "Exact role ownership versus application/product team ownership",
            "Production issue, evidence used, action taken, and result",
            "How the project connects to the target company's current job postings",
        ],
        "risk": gaps[0] if gaps else "Ready to match against companies",
    }


def _role_journey_owner_label(consultant: ConsultantProfile) -> str:
    owners = [value for value in [consultant.staff_owner, consultant.recruiter_owner] if value and value.strip()]
    return " / ".join(owners) if owners else "Owner not assigned"


def _role_journey_stage_options() -> list[tuple[str, str]]:
    return [
        ("all", "All stages"),
        ("role_intake", "Role intake"),
        ("profile_intake", "Profile intake"),
        ("training_plan", "Training plan"),
        ("project_story", "Project story"),
        ("positioning", "Positioning"),
        ("interview_readiness", "Interview readiness"),
        ("final_evidence", "Final evidence package"),
        ("company_matching", "Company matching"),
        ("campaign_active", "Campaign active"),
        ("submission_pipeline", "Submission pipeline"),
        ("interview_pipeline", "Interview pipeline"),
        ("offer", "Offer"),
        ("placement", "Placement"),
        ("post_placement", "Post-placement support"),
    ]


def _role_journey_stage_order(stage: str) -> int:
    order = {value: index * 10 for index, (value, _) in enumerate(_role_journey_stage_options()) if value != "all"}
    return order.get(stage, 999)


def _candidate_company_match_row(consultant: ConsultantProfile, company_row: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    next_action = "Prepare tailored resume and submit to matching official postings."
    if company_row["action_stage"] == "ready_to_promote":
        next_action = "Promote company first, import job intelligence JSON, then tailor candidate."
    elif company_row["action_stage"] == "needs_json":
        next_action = "Import job intelligence JSON before candidate submission."
    elif company_row["action_stage"] == "needs_review":
        next_action = "Staff should verify estimated experience fit before assigning candidate."
    if match["gaps"]:
        next_action = f"{next_action} Address: {', '.join(match['gaps'])}."
    return {
        "consultant_id": consultant.id,
        "consultant_name": consultant.name or consultant.email,
        "consultant_email": consultant.email,
        "consultant_role": match["role"],
        "consultant_readiness": match["readiness"],
        "work_authorization": match["work_authorization"],
        "company_id": company_row["company_id"],
        "company_name": company_row["company_name"],
        "pursuit": company_row.get("pursuit"),
        "pursuit_id": company_row["pursuit"].id if company_row.get("pursuit") else None,
        "region_name": company_row["region_name"],
        "match_score": match["score"],
        "watch_score": company_row["watch_score"],
        "company_stage": company_row["stage_label"],
        "company_recommendation": company_row["recommendation"],
        "best_roles_label": company_row["best_roles_label"],
        "tech_stack_label": company_row["tech_stack_label"],
        "skill_hits": match["skill_hits"],
        "skill_hits_label": ", ".join(match["skill_hits"]) or "No stack overlap captured",
        "candidate_gaps": match["gaps"],
        "candidate_gaps_label": ", ".join(match["gaps"]) or "No major gaps",
        "match_reason": match["why"],
        "next_action": next_action,
        "approvals": company_row["approvals"],
        "approval_rate": company_row["approval_rate"],
        "eligible_jobs": company_row["eligible_jobs"],
        "verified_jobs": company_row["verified_jobs"],
        "estimated_jobs": company_row["estimated_jobs"],
    }


def _attach_candidate_matches(db: Session, rows: list[dict[str, Any]], limit_per_company: int = 4) -> None:
    if not rows:
        return
    consultants = db.scalars(
        select(ConsultantProfile)
        .outerjoin(MarketingRole)
        .where(ConsultantProfile.active.is_(True))
        .order_by(ConsultantProfile.marketing_status.asc(), ConsultantProfile.resume_readiness_score.desc(), ConsultantProfile.name.asc())
    ).all()
    for row in rows:
        matches = sorted(
            [_candidate_company_match(consultant, row) for consultant in consultants],
            key=lambda item: (-item["score"], item["name"].lower()),
        )
        useful = [item for item in matches if item["score"] >= 35][:limit_per_company]
        row["candidate_matches"] = useful
        row["candidate_match_count"] = len([item for item in matches if item["score"] >= 35])
        row["candidate_match_label"] = f"{row['candidate_match_count']} candidate matches" if row["candidate_match_count"] else "No ready candidate match"


def _candidate_company_match(consultant: ConsultantProfile, row: dict[str, Any]) -> dict[str, Any]:
    role_name = consultant.marketing_role.name if consultant.marketing_role else ""
    role_hit = _candidate_role_match(role_name, row.get("best_roles", []))
    skill_hits = _candidate_skill_hits(consultant.primary_skills, row.get("tech_stack", []))
    readiness = _candidate_readiness_score(consultant)
    auth_hit = bool(re.search(r"\b(opt|stem|h-?1b|cpt|ead)\b", consultant.work_authorization or "", re.I))
    location_hit = _candidate_location_fit(consultant, row)
    score = 0
    score += 35 if role_hit else 0
    score += min(25, len(skill_hits) * 5)
    score += min(25, readiness // 4)
    score += 8 if auth_hit else 0
    score += 7 if location_hit else 0
    gaps = []
    if not role_hit:
        gaps.append("role mismatch")
    if len(skill_hits) < 3:
        gaps.append("needs stronger stack overlap")
    if readiness < 60:
        gaps.append("readiness below 60")
    if not auth_hit:
        gaps.append("work authorization not clear")
    return {
        "id": consultant.id,
        "name": consultant.name or consultant.email,
        "email": consultant.email,
        "role": role_name or "Role not set",
        "score": max(0, min(100, score)),
        "readiness": readiness,
        "work_authorization": consultant.work_authorization or "Auth not set",
        "location": consultant.current_location or "Location not set",
        "skill_hits": skill_hits[:8],
        "why": _candidate_match_reason(role_hit, skill_hits, readiness, auth_hit, location_hit),
        "gaps": gaps[:3],
    }


def _candidate_role_match(role_name: str, best_roles: list[str]) -> bool:
    if not role_name:
        return False
    role_tokens = set(_tokenize_match_text(role_name))
    for target in best_roles:
        target_tokens = set(_tokenize_match_text(target))
        if role_tokens & target_tokens:
            return True
    return False


def _candidate_skill_hits(skills: str, tech_stack: list[str]) -> list[str]:
    skill_text = f" {skills.lower()} "
    hits = []
    for tech in tech_stack:
        cleaned = str(tech).strip()
        if cleaned and re.search(rf"(?<![a-z0-9]){re.escape(cleaned.lower())}(?![a-z0-9])", skill_text):
            hits.append(cleaned)
    return hits


def _candidate_readiness_score(consultant: ConsultantProfile) -> int:
    scores = [
        consultant.marketing_readiness_percent,
        consultant.resume_readiness_score or 0,
        consultant.technical_readiness_score or 0,
        consultant.interview_readiness_score or 0,
    ]
    return round(sum(scores) / len(scores))


def _candidate_location_fit(consultant: ConsultantProfile, row: dict[str, Any]) -> bool:
    text = f"{consultant.current_location} {consultant.relocation_preference} {consultant.onsite_preference}".lower()
    if any(word in text for word in ["remote", "open", "relocat", "anywhere", "hybrid"]):
        return True
    states = {state.lower() for state in row.get("states", {})}
    return any(re.search(rf"\b{re.escape(state)}\b", text) for state in states)


def _candidate_match_reason(role_hit: bool, skill_hits: list[str], readiness: int, auth_hit: bool, location_hit: bool) -> str:
    parts = []
    if role_hit:
        parts.append("role aligned")
    if skill_hits:
        parts.append(f"{len(skill_hits)} stack hits")
    parts.append(f"{readiness}% ready")
    if auth_hit:
        parts.append("OPT/H1B signal")
    if location_hit:
        parts.append("location flexible")
    return ", ".join(parts)


def _tokenize_match_text(value: str) -> list[str]:
    stop = {"engineer", "engineering", "platform", "senior", "associate", "cloud", "ai"}
    return [token for token in re.findall(r"[a-z0-9]+", value.lower()) if token not in stop and len(token) > 2]


def _safe_json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def _role_count_seniority_exclusions(role_counts: Any) -> int:
    if not isinstance(role_counts, dict):
        return 0
    return sum(int(row.get("excluded_seniority_risk") or 0) for row in role_counts.values() if isinstance(row, dict))


def _hiring_standard_risk(excluded_seniority: int, eligible_jobs: int, verified_jobs: int, snapshot: PursuitIntelligenceSnapshot | None) -> tuple[str, str]:
    if excluded_seniority >= 3 and eligible_jobs == 0:
        return "Very high", "Imported postings are mostly seniority-excluded and no eligible jobs remain."
    if excluded_seniority >= max(3, eligible_jobs * 2):
        return "Very high", "Seniority exclusions heavily outweigh eligible job evidence."
    if excluded_seniority and not verified_jobs:
        return "High", "Seniority exclusions exist and no verified below-8-year jobs were imported."
    if snapshot and snapshot.total_eligible_usa_job_signal == 0 and snapshot.company_rating == "Not enough USA evidence":
        return "High", "Imported company rating did not find enough USA job evidence."
    if excluded_seniority:
        return "Medium", "Some seniority-risk postings were excluded."
    return "Low", "No seniority-risk exclusion signal found."


def _watch_score_and_recommendation(approvals: int, approval_rate: float, new_employment: int, eligible_jobs: int, verified_jobs: int, estimated_jobs: int, standard_risk: str, promoted: bool) -> tuple[int, str, str]:
    score = 0
    score += min(30, approvals // 2)
    score += 15 if approval_rate >= 90 else 10 if approval_rate >= 80 else 5
    score += min(20, new_employment * 2)
    score += min(20, eligible_jobs * 4)
    score += min(10, verified_jobs * 3 + estimated_jobs)
    if promoted:
        score += 5
    if standard_risk == "Very high":
        score -= 35
    elif standard_risk == "High":
        score -= 20
    elif standard_risk == "Medium":
        score -= 8
    score = max(0, min(100, score))
    if standard_risk == "Very high":
        return score, "Eliminate initial pass", "Hiring standards look too senior-heavy for first-pass staff research."
    if score >= 70:
        return score, "Promote / actively watch", "Strong USCIS signal and usable job evidence."
    if score >= 45:
        return score, "Watch", "USCIS signal is useful; staff should confirm current postings and contacts."
    return score, "Low priority", "Signal is below current watchlist threshold."


def _sort_watchlist_rows(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    sort_map = {
        "approvals": lambda row: (-row["approvals"], row["company_name"].lower()),
        "approval_rate": lambda row: (-row["approval_rate"], -row["approvals"], row["company_name"].lower()),
        "new_employment": lambda row: (-row["new_employment"], -row["watch_score"], row["company_name"].lower()),
        "standard_risk": lambda row: ({"Very high": 0, "High": 1, "Medium": 2, "Low": 3}.get(row["standard_risk"], 4), -row["watch_score"]),
        "company": lambda row: row["company_name"].lower(),
        "staff": lambda row: (row.get("assigned_staff") or row.get("suggested_staff") or "", row["company_name"].lower()),
        "watch_score": lambda row: (-row["watch_score"], -row["approvals"], row["company_name"].lower()),
    }
    return sorted(rows, key=sort_map.get(sort, sort_map["watch_score"]))


def _assign_watchlist_capacity(rows: list[dict[str, Any]], staff_by_region: dict[int, list[User]], capacity_per_staff: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    counters: dict[int, int] = {}
    staff_rows_by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        region_id = row.get("region_id")
        staff = staff_by_region.get(region_id or -1, []) if region_id else []
        assigned = row.get("assigned_staff") or ""
        assigned_email = row.get("assigned_staff_email") or ""
        if not assigned and staff:
            index = counters.get(region_id, 0)
            owner = staff[index % len(staff)]
            assigned = owner.name or owner.email
            assigned_email = owner.email
            counters[region_id] = index + 1
        row["suggested_staff"] = assigned or "No staff assigned to region"
        row["suggested_staff_email"] = assigned_email
        key = (region_id or 0, assigned_email or row["suggested_staff"])
        staff_row = staff_rows_by_key.setdefault(
            key,
            {
                "region_name": row.get("region_name") or "Unmapped",
                "staff": row["suggested_staff"],
                "email": assigned_email,
                "assigned": 0,
                "capacity": capacity_per_staff,
            },
        )
        staff_row["assigned"] += 1
        row["within_capacity"] = staff_row["assigned"] <= capacity_per_staff
    staff_rows = sorted(staff_rows_by_key.values(), key=lambda item: (item["region_name"], item["staff"]))
    for item in staff_rows:
        item["remaining"] = max(0, item["capacity"] - item["assigned"])
        item["over_capacity"] = max(0, item["assigned"] - item["capacity"])
    return rows, staff_rows


def _companies_by_region_report(db: Session, *, decision_type: str, start_year: Optional[int], end_year: Optional[int], summary_sort: str, sort: str) -> dict[str, Any]:
    approval_expr, denial_expr = _decision_expressions(decision_type)
    query = (
        select(
            UscisEmployerYearlyStat.company_id,
            Company.name.label("company_name"),
            UscisEmployerYearlyStat.petitioner_state,
            func.sum(approval_expr).label("approvals"),
            func.sum(denial_expr).label("denials"),
        )
        .join(Company, Company.id == UscisEmployerYearlyStat.company_id)
        .where(UscisEmployerYearlyStat.company_id.is_not(None))
    )
    if start_year:
        query = query.where(UscisEmployerYearlyStat.fiscal_year >= start_year)
    if end_year:
        query = query.where(UscisEmployerYearlyStat.fiscal_year <= end_year)
    state_rows = db.execute(query.group_by(UscisEmployerYearlyStat.company_id, Company.name, UscisEmployerYearlyStat.petitioner_state)).mappings().all()

    region_meta = {item["code"]: item for item in all_region_metadata()}
    company_region_scores: dict[tuple[int, str], dict[str, Any]] = {}
    for row in state_rows:
        company_id = int(row["company_id"])
        approvals = int(row["approvals"] or 0)
        denials = int(row["denials"] or 0)
        decisions = approvals + denials
        if decisions <= 0:
            continue
        code = region_code_for_state(row["petitioner_state"]) or "unknown"
        current = company_region_scores.setdefault(
            (company_id, code),
            {"company_id": company_id, "company_name": row["company_name"], "region_code": code, "approvals": 0, "denials": 0, "decisions": 0, "states": set()},
        )
        current["approvals"] += approvals
        current["denials"] += denials
        current["decisions"] += decisions
        if row["petitioner_state"]:
            current["states"].add(row["petitioner_state"])

    company_rows = []
    summary_by_code: dict[str, dict[str, Any]] = {}
    for item in all_region_metadata():
        summary_by_code[item["code"]] = {"region_code": item["code"], "region_name": item["name"], "tier": item["tier"], "company_count": 0, "approvals": 0, "denials": 0, "decisions": 0}
    summary_by_code["unknown"] = {"region_code": "unknown", "region_name": "Unknown / Unmapped", "tier": "N/A", "company_count": 0, "approvals": 0, "denials": 0, "decisions": 0}

    for company_region in company_region_scores.values():
        meta = region_meta.get(company_region["region_code"])
        region_name = meta["name"] if meta else "Unknown / Unmapped"
        tier = meta["tier"] if meta else "N/A"
        approval_rate = round(company_region["approvals"] / company_region["decisions"] * 100, 1) if company_region["decisions"] else 0
        row = {
            "company_id": company_region["company_id"],
            "company_name": company_region["company_name"],
            "region_code": company_region["region_code"],
            "region_name": region_name,
            "tier": tier,
            "states": ", ".join(sorted(company_region["states"])),
            "approvals": company_region["approvals"],
            "denials": company_region["denials"],
            "decisions": company_region["decisions"],
            "approval_rate": approval_rate,
        }
        company_rows.append(row)
        summary = summary_by_code.setdefault(company_region["region_code"], {"region_code": company_region["region_code"], "region_name": region_name, "tier": tier, "company_count": 0, "approvals": 0, "denials": 0, "decisions": 0})
        summary["company_count"] += 1
        summary["approvals"] += row["approvals"]
        summary["denials"] += row["denials"]
        summary["decisions"] += row["decisions"]

    for summary in summary_by_code.values():
        summary["approval_rate"] = round(summary["approvals"] / summary["decisions"] * 100, 1) if summary["decisions"] else 0
    summary_sort_map = {
        "region": lambda row: (row["region_name"].lower(),),
        "tier": lambda row: (row["tier"], row["region_name"].lower()),
        "companies": lambda row: (-row["company_count"], row["region_name"].lower()),
        "approvals": lambda row: (-row["approvals"], row["region_name"].lower()),
        "denials": lambda row: (-row["denials"], row["region_name"].lower()),
        "decisions": lambda row: (-row["decisions"], row["region_name"].lower()),
        "approval_rate": lambda row: (-row["approval_rate"], -row["decisions"], row["region_name"].lower()),
    }
    summary_rows = sorted(summary_by_code.values(), key=summary_sort_map.get(summary_sort, summary_sort_map["decisions"]))

    sort_map = {
        "region": lambda row: (row["region_name"], row["company_name"]),
        "company": lambda row: row["company_name"].lower(),
        "approvals": lambda row: (-row["approvals"], row["company_name"].lower()),
        "denials": lambda row: (-row["denials"], row["company_name"].lower()),
        "decisions": lambda row: (-row["decisions"], row["company_name"].lower()),
        "approval_rate": lambda row: (-row["approval_rate"], -row["decisions"], row["company_name"].lower()),
    }
    company_rows = sorted(company_rows, key=sort_map.get(sort, sort_map["approvals"]))
    unique_company_ids = {row["company_id"] for row in company_rows}
    totals = {
        "company_count": len(unique_company_ids),
        "company_region_count": len(company_rows),
        "approvals": sum(row["approvals"] for row in company_rows),
        "denials": sum(row["denials"] for row in company_rows),
        "decisions": sum(row["decisions"] for row in company_rows),
    }
    totals["approval_rate"] = round(totals["approvals"] / totals["decisions"] * 100, 1) if totals["decisions"] else 0
    return {"summary_rows": summary_rows, "company_rows": company_rows, "totals": totals}


def _region_metadata(region_code: str) -> dict[str, Any]:
    if region_code == "unknown":
        return {
            "code": "unknown",
            "name": "Unknown / Unmapped",
            "tier": "N/A",
            "description": "Companies with USCIS petitioner states that are blank or not mapped to a Mintel region.",
            "states": (),
        }
    return next((item for item in all_region_metadata() if item["code"] == region_code), {})


def _empty_region_summary(region_code: str) -> dict[str, Any]:
    meta = _region_metadata(region_code)
    return {
        "region_code": region_code,
        "region_name": meta.get("name", "Unknown / Unmapped"),
        "tier": meta.get("tier", "N/A"),
        "company_count": 0,
        "approvals": 0,
        "denials": 0,
        "decisions": 0,
        "approval_rate": 0,
    }


def _region_drilldown_standards(region_meta: dict[str, Any]) -> list[str]:
    states = ", ".join(region_meta.get("states") or ())
    mapped_scope = f"Region scope follows petitioner states: {states}." if states else "Unmapped rows are kept separate until a petitioner state can be assigned."
    return [
        "Counts use the selected fiscal year range and USCIS decision type from the parent report.",
        mapped_scope,
        "Company rows only include region-decision combinations with at least one approval or denial.",
        "Open the USCIS detail before promoting a company so pursuit notes are tied to the source evidence.",
    ]


def _marketing_role_code(code: str, name: str) -> str:
    value = (code or name).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "marketing-role"


def _active_marketing_roles(db: Session) -> list[MarketingRole]:
    return db.scalars(select(MarketingRole).where(MarketingRole.active.is_(True)).order_by(MarketingRole.name)).all()


def _mock_interviews_query_for_user(user: User, visible_role_ids: Optional[set[int]] = None):
    query = select(MockInterview)
    if _is_consultant_user(user):
        return query.join(ConsultantProfile).where(func.lower(ConsultantProfile.email) == (user.email or "").strip().lower())
    if visible_role_ids is None:
        visible_role_ids = _visible_mock_marketing_role_ids(user)
    if visible_role_ids is not None:
        query = query.where(MockInterview.marketing_role_id.in_(visible_role_ids or {-1}))
    return query


def _mock_interview_for_user(db: Session, user: User, mock_id: int) -> MockInterview:
    if _is_consultant_user(user):
        profile = _consultant_profile_for_user(db, user)
        row = db.scalar(select(MockInterview).where(MockInterview.id == mock_id, MockInterview.consultant_id == (profile.id if profile else -1)))
        if not row:
            raise PermissionDenied("Mock interview was not found for this consultant account.")
        return row
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    query = select(MockInterview).where(MockInterview.id == mock_id)
    if visible_role_ids is not None:
        query = query.where(MockInterview.marketing_role_id.in_(visible_role_ids or {-1}))
    row = db.scalar(query)
    if not row:
        raise PermissionDenied("Mock interview was not found or is outside your assigned marketing roles.")
    return row


def _record_mock_event(db: Session, row: MockInterview, user: User | object | None, event_type: str, from_status: str, to_status: str, note: str = "") -> None:
    db.add(
        MockInterviewStatusEvent(
            mock_interview_id=row.id,
            actor_id=getattr(user, "id", None),
            event_type=event_type,
            from_status=str(from_status or ""),
            to_status=str(to_status or ""),
            note=note.strip(),
        )
    )


def _mock_status_event_map(db: Session, mock_ids: list[int]) -> dict[int, list[str]]:
    if not mock_ids:
        return {}
    rows = db.scalars(
        select(MockInterviewStatusEvent)
        .where(MockInterviewStatusEvent.mock_interview_id.in_(mock_ids))
        .order_by(MockInterviewStatusEvent.created_at.asc())
    ).all()
    event_map: dict[int, list[str]] = {}
    for event in rows:
        label = event.event_type.replace("_", " ").title()
        if event.to_status:
            label = f"{label}: {event.to_status.replace('_', ' ').title()}"
        if event.note:
            label = f"{label} - {event.note}"
        event_map.setdefault(event.mock_interview_id, []).append(label)
    return event_map


def _parse_time_value(value: str) -> Optional[datetime_time]:
    if not value:
        return None
    try:
        return datetime_time.fromisoformat(value)
    except ValueError:
        return None


def _parse_local_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _mock_scheduled_datetime(scheduled_on: str | date | None, scheduled_time: str | None) -> Optional[datetime]:
    day = scheduled_on if isinstance(scheduled_on, date) else _parse_date(str(scheduled_on or ""))
    clock = _parse_time_value(scheduled_time or "")
    if not day or not clock:
        return None
    return datetime.combine(day, clock).replace(tzinfo=timezone.utc)


def _mock_end_datetime(start_at: Optional[datetime], duration_minutes: int) -> Optional[datetime]:
    if not start_at:
        return None
    return start_at.timestamp() + max(15, duration_minutes) * 60


def _ranges_overlap(start_a: datetime, end_a_ts: float, start_b: datetime, end_b_ts: float) -> bool:
    return start_a.timestamp() < end_b_ts and start_b.timestamp() < end_a_ts


def _mock_interview_conflicts(
    db: Session,
    consultant_id: Optional[int],
    staff_id: Optional[int],
    scheduled_on: str | date | None,
    scheduled_time: str | None,
    duration_minutes: int,
    exclude_mock_id: Optional[int] = None,
) -> list[str]:
    start_at = _mock_scheduled_datetime(scheduled_on, scheduled_time)
    end_at_ts = _mock_end_datetime(start_at, duration_minutes)
    if not start_at or not end_at_ts:
        return []
    conflicts: list[str] = []
    if consultant_id:
        blocks = db.scalars(select(ConsultantAvailabilityBlock).where(ConsultantAvailabilityBlock.consultant_id == consultant_id, ConsultantAvailabilityBlock.active.is_(True))).all()
        for block in blocks:
            if _ranges_overlap(start_at, end_at_ts, block.start_at, block.end_at.timestamp()):
                conflicts.append(f"Consultant unavailable: {block.reason or block.notes or block.start_at}")
        existing = select(MockInterview).where(MockInterview.consultant_id == consultant_id, MockInterview.scheduled_on == start_at.date(), MockInterview.status.notin_([MockInterviewStatus.CANCELLED.value, MockInterviewStatus.NO_SHOW.value]))
        if exclude_mock_id:
            existing = existing.where(MockInterview.id != exclude_mock_id)
        for row in db.scalars(existing).all():
            other_start = _mock_scheduled_datetime(row.scheduled_on, row.scheduled_time)
            other_end_ts = _mock_end_datetime(other_start, row.duration_minutes or 60)
            if other_start and other_end_ts and _ranges_overlap(start_at, end_at_ts, other_start, other_end_ts):
                conflicts.append(f"Consultant already has mock interview #{row.id}.")
    if staff_id:
        existing = select(MockInterview).where(MockInterview.assigned_staff_id == staff_id, MockInterview.scheduled_on == start_at.date(), MockInterview.status.notin_([MockInterviewStatus.CANCELLED.value, MockInterviewStatus.NO_SHOW.value]))
        if exclude_mock_id:
            existing = existing.where(MockInterview.id != exclude_mock_id)
        for row in db.scalars(existing).all():
            other_start = _mock_scheduled_datetime(row.scheduled_on, row.scheduled_time)
            other_end_ts = _mock_end_datetime(other_start, row.duration_minutes or 60)
            if other_start and other_end_ts and _ranges_overlap(start_at, end_at_ts, other_start, other_end_ts):
                conflicts.append(f"Interviewer already has mock interview #{row.id}.")
    return conflicts


def _availability_summary_by_staff(db: Session, staff_options: list[User]) -> dict[int, dict[str, int]]:
    result: dict[int, dict[str, int]] = {}
    for staff in staff_options:
        result[staff.id] = {
            "weekly": db.scalar(select(func.count()).select_from(TrainerWeeklyAvailability).where(TrainerWeeklyAvailability.staff_id == staff.id, TrainerWeeklyAvailability.active.is_(True))) or 0,
            "adhoc": db.scalar(select(func.count()).select_from(TrainerAdhocAvailability).where(TrainerAdhocAvailability.staff_id == staff.id, TrainerAdhocAvailability.active.is_(True))) or 0,
        }
    return result


def _weekday_options() -> list[tuple[int, str]]:
    return [(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday")]


def _can_view_all_mock_interviews(user: User | object | None) -> bool:
    return has_permission(user, Permission.MANAGE_OPERATIONS) or has_permission(user, Permission.MANAGE_STAFF)


def _can_manage_mock_interviews(user: User | object | None) -> bool:
    return _can_view_all_mock_interviews(user) or getattr(getattr(user, "role", ""), "value", getattr(user, "role", "")) == UserRole.REGIONAL_STAFF.value


def _can_view_all_consultants(user: User | object | None) -> bool:
    return has_permission(user, Permission.ASSIGN_PURSUITS) or has_permission(user, Permission.MANAGE_OPERATIONS) or has_permission(user, Permission.MANAGE_STAFF)


def _consultant_visibility_clause(user: User | object | None):
    if _can_view_all_consultants(user):
        return None
    role_ids = _staff_marketing_role_ids(user)
    identity_values = {
        (getattr(user, "email", "") or "").strip().lower(),
        (getattr(user, "name", "") or "").strip().lower(),
        f"{(getattr(user, 'first_name', '') or '').strip()} {(getattr(user, 'last_name', '') or '').strip()}".strip().lower(),
    }
    identity_values = {value for value in identity_values if value}
    clauses = []
    if role_ids:
        clauses.append(ConsultantProfile.marketing_role_id.in_(role_ids))
    for value in identity_values:
        clauses.append(func.lower(ConsultantProfile.staff_owner) == value)
        clauses.append(func.lower(ConsultantProfile.recruiter_owner) == value)
    return or_(*clauses) if clauses else ConsultantProfile.id == -1


def _can_access_consultant(consultant: ConsultantProfile, user: User | object | None) -> bool:
    if _can_view_all_consultants(user):
        return True
    role_ids = _staff_marketing_role_ids(user)
    if consultant.marketing_role_id and consultant.marketing_role_id in role_ids:
        return True
    identity_values = {
        (getattr(user, "email", "") or "").strip().lower(),
        (getattr(user, "name", "") or "").strip().lower(),
        f"{(getattr(user, 'first_name', '') or '').strip()} {(getattr(user, 'last_name', '') or '').strip()}".strip().lower(),
    }
    owner_values = {
        (consultant.staff_owner or "").strip().lower(),
        (consultant.recruiter_owner or "").strip().lower(),
    }
    return bool({value for value in identity_values if value} & {value for value in owner_values if value})


def _can_manage_consultant_journey(user: User | object | None, consultant: ConsultantProfile) -> bool:
    if _can_view_all_consultants(user):
        return True
    role_value = getattr(getattr(user, "role", ""), "value", getattr(user, "role", ""))
    return role_value == UserRole.REGIONAL_STAFF.value and _can_access_consultant(consultant, user)


def _staff_marketing_role_ids(user: User | object | None) -> set[int]:
    assignments = getattr(user, "marketing_role_assignments", []) or []
    return {
        assignment.marketing_role_id
        for assignment in assignments
        if assignment.active and assignment.marketing_role_id
    }


def _visible_mock_marketing_role_ids(user: User | object | None) -> Optional[set[int]]:
    if _is_consultant_user(user):
        return None
    if _can_view_all_mock_interviews(user):
        return None
    return _staff_marketing_role_ids(user)


def _staff_assigned_to_marketing_role(staff_member: User, marketing_role_id: int) -> bool:
    if _can_view_all_mock_interviews(staff_member):
        return True
    return marketing_role_id in _staff_marketing_role_ids(staff_member)


def _visible_marketing_roles_for_user(db: Session, user: User) -> list[MarketingRole]:
    visible_role_ids = _visible_mock_marketing_role_ids(user)
    query = select(MarketingRole).where(MarketingRole.active.is_(True))
    if visible_role_ids is not None:
        query = query.where(MarketingRole.id.in_(visible_role_ids or {-1}))
    return db.scalars(query.order_by(MarketingRole.name)).all()


def _visible_training_programs(db: Session, visible_role_ids: Optional[set[int]]) -> list[TrainingProgram]:
    query = select(TrainingProgram).where(TrainingProgram.active.is_(True))
    if visible_role_ids is not None:
        query = query.where(TrainingProgram.marketing_role_id.in_(visible_role_ids or {-1}))
    return db.scalars(query.order_by(TrainingProgram.title)).all()


def _visible_training_marketing_roles(db: Session, user: User | object | None) -> list[MarketingRole]:
    query = select(MarketingRole).where(MarketingRole.active.is_(True))
    if _is_consultant_user(user):
        role_id, _domain = _consultant_training_scope(db, user)
        query = query.where(MarketingRole.id == (role_id or -1))
    return db.scalars(query.order_by(MarketingRole.name)).all()


def _is_consultant_user(user: User | object | None) -> bool:
    role = getattr(user, "role", "")
    return getattr(role, "value", role) == UserRole.CONSULTANT.value


def _consultant_profile_for_user(db: Session, user: User | object | None) -> Optional[ConsultantProfile]:
    email = (getattr(user, "email", "") or "").strip().lower()
    if not email:
        return None
    return db.scalar(select(ConsultantProfile).where(func.lower(ConsultantProfile.email) == email, ConsultantProfile.active.is_(True)))


def _consultant_training_scope(db: Session, user: User | object | None) -> tuple[Optional[int], str]:
    if not _is_consultant_user(user):
        return None, ""
    profile = _consultant_profile_for_user(db, user)
    if not profile:
        return -1, "__no_domain__"
    return profile.marketing_role_id or -1, (profile.target_industry_domain or "").strip()


def _can_access_training_program(program: TrainingProgram, db: Session, user: User | object | None) -> bool:
    if not _is_consultant_user(user):
        return True
    profile = _consultant_profile_for_user(db, user)
    return consultant_training_scope_matches(profile, program)


def _mock_interview_staff_options(db: Session, visible_role_ids: Optional[set[int]]) -> list[User]:
    query = select(User).where(User.active.is_(True), User.role.in_(_assignable_staff_roles()))
    if visible_role_ids is not None:
        query = query.join(StaffMarketingRoleAssignment).where(
            StaffMarketingRoleAssignment.active.is_(True),
            StaffMarketingRoleAssignment.marketing_role_id.in_(visible_role_ids or {-1}),
        )
    return db.scalars(query.order_by(User.name.asc(), User.email.asc()).distinct()).all()


def _marketing_role_owner_options(db: Session) -> list[User]:
    return db.scalars(
        select(User)
        .where(User.active.is_(True), User.role.in_(_assignable_staff_roles()))
        .order_by(User.name.asc(), User.email.asc())
    ).all()


def _marketing_role_owner_id(db: Session, owner_id: Optional[int]) -> Optional[int]:
    if not owner_id:
        return None
    owner = _assignable_staff_member(db, owner_id)
    if not owner or not owner.active:
        return None
    return owner.id


def _promoted_companies(db: Session) -> list[Company]:
    return db.scalars(select(Company).join(CompanyPursuit).order_by(Company.name)).all()


def _promoted_companies_for_user(db: Session, user: User | object | None) -> list[Company]:
    query = select(Company).join(CompanyPursuit)
    visible_clause = _pursuit_visibility_clause(user)
    if visible_clause is not None:
        query = query.where(visible_clause)
    return db.scalars(query.order_by(Company.name)).all()


def _is_promoted_company(db: Session, company_id: int) -> bool:
    return bool(db.scalar(select(CompanyPursuit.id).where(CompanyPursuit.company_id == company_id)))


def _promoted_jobs(db: Session) -> list[JobOpportunity]:
    return db.scalars(select(JobOpportunity).join(Company).join(CompanyPursuit).where(JobOpportunity.active.is_(True)).order_by(Company.name, JobOpportunity.title)).all()


def _job_form_response(request: Request, user: User, db: Session, job: Optional[JobOpportunity], error: str = ""):
    return templates.TemplateResponse(
        "web/job_form.html",
        {
            "request": request,
            "user": user,
            "job": job,
            "promoted_companies": _promoted_companies_for_user(db, user),
            "marketing_roles": _active_marketing_roles(db),
            "source_options": _job_source_options(),
            "job_type_options": _job_type_options(),
            "experience_level_options": _experience_level_options(),
            "cloud_specialization_options": _cloud_specialization_options(),
            "selected_marketing_roles": _csv_int_set(job.marketing_role_ids if job else ""),
            "selected_cloud_specializations": set((job.additional_cloud_specializations or "").split(",")) if job else set(),
            "error": error,
        },
    )


def _can_edit_job(user: User | object | None, job: JobOpportunity | object | None) -> bool:
    if not job or not has_permission(user, Permission.MANAGE_PURSUIT_WORKSPACE):
        return False
    if has_permission(user, Permission.ASSIGN_PURSUITS):
        return True
    return bool((getattr(job, "created_by", "") or "").lower() == (getattr(user, "email", "") or "").lower())


def _create_staff_manual_job(
    request: Request,
    db: Session,
    user: User,
    *,
    company_id: Optional[int] = None,
    company_name: str = "",
    title: str = "",
    requirement_key: str = "",
    certifications_required: str = "",
    marketing_roles: Optional[list[int]] = None,
    additional_cloud_specializations: Optional[list[str]] = None,
    location: str = "",
    job_type: str = "",
    experience_level: str = "",
    source: str = JobSource.STAFF_MANUAL.value,
    url: str = "",
    posted_on: str = "",
    ats_platform: str = "",
    description: str = "",
    decision_payload: str = "",
    sponsorship_notes: str = "",
    is_active: bool = True,
    job_alerts_created: bool = False,
    next_url: str = "",
    error_target: str = "/jobs/new",
) -> RedirectResponse:
    company = _job_company_from_form(db, company_id, company_name)
    if not company or not _is_promoted_company(db, company.id) or not _can_view_pursuit(user, company.pursuit):
        return _job_error_redirect(error_target, "Jobs can only be added for promoted companies")
    validation_error = _job_validation_error(db, company.id, title, requirement_key, location, url, description, marketing_roles or [])
    if validation_error:
        return _job_error_redirect(error_target, validation_error)
    job = JobOpportunity()
    _apply_job_fields(
        job,
        company=company,
        title=title,
        requirement_key=requirement_key,
        certifications_required=certifications_required,
        marketing_roles=marketing_roles,
        additional_cloud_specializations=additional_cloud_specializations,
        location=location,
        job_type=job_type,
        experience_level=experience_level,
        source=source,
        url=url,
        posted_on=posted_on,
        ats_platform=ats_platform,
        description=description,
        decision_payload=decision_payload,
        sponsorship_notes=sponsorship_notes,
        active=is_active,
        job_alerts_created=job_alerts_created,
    )
    job.approval_status = "pending"
    job.created_by = user.email
    job.source_type = JobSource.STAFF_MANUAL.value
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _job_error_redirect(error_target, "Requirement key must be unique for this company")
    _flash(request, f"Created job {job.title}. Approval status is pending.")
    if next_url:
        return RedirectResponse(_safe_next_url(next_url), status_code=303)
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


def _apply_job_fields(
    job: JobOpportunity,
    *,
    company: Company,
    title: str,
    requirement_key: str,
    certifications_required: str,
    marketing_roles: Optional[list[int]],
    additional_cloud_specializations: Optional[list[str]],
    location: str,
    job_type: str,
    experience_level: str,
    source: str,
    url: str,
    posted_on: str,
    ats_platform: str,
    description: str,
    decision_payload: str,
    sponsorship_notes: str,
    active: bool,
    job_alerts_created: bool,
) -> None:
    job.company_id = company.id
    job.title = title.strip()
    job.requirement_key = requirement_key.strip()
    job.certifications_required = certifications_required.strip()
    job.marketing_role_ids = ",".join(str(role_id) for role_id in sorted(set(marketing_roles or [])))
    job.additional_cloud_specializations = ",".join(_valid_cloud_specializations(additional_cloud_specializations or []))
    job.location = location.strip()
    job.job_type = job_type if job_type in _job_type_values() else ""
    job.experience_level = experience_level if experience_level in _experience_level_values() else ""
    job.source = source if source in _job_source_values() else JobSource.STAFF_MANUAL.value
    job.source_type = job.source_type or JobSource.STAFF_MANUAL.value
    job.url = url.strip()
    job.posted_on = _parse_date(posted_on)
    job.ats_platform = ats_platform.strip()
    job.description = _sanitize_rich_text(description)
    job.decision_payload = decision_payload.strip()
    job.sponsorship_notes = _sanitize_rich_text(sponsorship_notes)
    job.active = active
    job.job_alerts_created = job_alerts_created


def _job_validation_error(db: Session, company_id: int, title: str, requirement_key: str, location: str, url: str, description: str, marketing_roles: list[int], job_id: Optional[int] = None) -> str:
    required = {
        "title": title,
        "requirement key": requirement_key,
        "location": location,
        "url": url,
        "description": description,
    }
    missing = [label for label, value in required.items() if not str(value or "").strip()]
    if missing:
        return f"Missing required job fields: {', '.join(missing)}"
    if not marketing_roles:
        return "Select at least one marketing role"
    if len(_rich_text_plain_text(description).split()) < 10:
        return "Description must contain at least ten words"
    if _blocked_job_url(url):
        return "Use the original company or ATS posting URL, not LinkedIn, Jobright, mail, docs, or social links"
    duplicate_query = select(JobOpportunity.id).where(
        JobOpportunity.company_id == company_id,
        func.lower(JobOpportunity.requirement_key) == requirement_key.strip().lower(),
    )
    if job_id:
        duplicate_query = duplicate_query.where(JobOpportunity.id != job_id)
    if db.scalar(duplicate_query):
        return "Requirement key must be unique for this company"
    return ""


def _job_company_from_form(db: Session, company_id: Optional[int], company_name: str) -> Optional[Company]:
    if company_id:
        return db.get(Company, company_id)
    name = company_name.strip()
    if not name:
        return None
    return db.scalar(select(Company).join(CompanyPursuit).where(func.lower(Company.name) == name.lower()))


def _blocked_job_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return True
    blocked_hosts = ("linkedin.", "jobright.", "mail.", "docs.", "facebook.", "instagram.", "x.com", "twitter.", "tiktok.", "reddit.")
    return any(marker in host for marker in blocked_hosts)


def _job_error_redirect(target: str, error: str) -> RedirectResponse:
    separator = "&" if "?" in target else "?"
    return RedirectResponse(f"{target}{separator}{urlencode({'error': error})}", status_code=303)


def _csv_int_set(value: str) -> set[int]:
    result: set[int] = set()
    for item in (value or "").split(","):
        parsed = _optional_query_int(item)
        if parsed:
            result.add(parsed)
    return result


def _is_promoted_job(db: Session, job_id: int) -> bool:
    return bool(
        db.scalar(
            select(JobOpportunity.id)
            .join(Company)
            .join(CompanyPursuit)
            .where(JobOpportunity.id == job_id)
        )
    )


def _pursuit_visibility_clause(user: User | object | None):
    if has_permission(user, Permission.ASSIGN_PURSUITS):
        return None
    email = (getattr(user, "email", "") or "").lower()
    region_ids = _staff_region_ids(user)
    clauses = []
    if email:
        clauses.append(func.lower(CompanyPursuit.assigned_staff_email) == email)
    if region_ids:
        clauses.append(CompanyPursuit.region_id.in_(region_ids))
    if not clauses:
        return CompanyPursuit.id == -1
    return or_(*clauses)


def _can_view_pursuit(user: User | object | None, pursuit: CompanyPursuit | object | None) -> bool:
    if not pursuit or not has_permission(user, Permission.VIEW_PURSUITS):
        return False
    if has_permission(user, Permission.ASSIGN_PURSUITS):
        return True
    email = (getattr(user, "email", "") or "").lower()
    assigned_email = (getattr(pursuit, "assigned_staff_email", "") or "").lower()
    return bool((assigned_email and assigned_email == email) or _staff_assigned_to_region(user, getattr(pursuit, "region_id", None)))


def _pursuit_queue_options() -> list[tuple[str, str]]:
    return [
        ("", "All queues"),
        ("needs_owner", "Needs owner"),
        ("needs_research", "Needs research"),
        ("overdue", "Overdue follow-up"),
    ]


def _visible_followups_query(user: User | object | None, before: Optional[date], db: Session):
    query = select(CompanyPursuit).where(
        CompanyPursuit.next_follow_up_date.is_not(None),
        CompanyPursuit.status != PursuitStatus.CLOSED.value,
    )
    visible_clause = _pursuit_visibility_clause(user)
    if visible_clause is not None:
        query = query.where(visible_clause)
    if before is not None:
        query = query.where(CompanyPursuit.next_follow_up_date < before)
    return query


def _owner_needed_pursuits_query(db: Session):
    grouped_regions = select(RegionGroupRegion.region_id).join(RegionGroup).where(RegionGroup.active.is_(True), RegionGroupRegion.active.is_(True)).distinct()
    direct_regions = select(StaffRegionAssignment.region_id).where(StaffRegionAssignment.active.is_(True)).distinct()
    covered_region_ids = [row[0] for row in db.execute(grouped_regions).all()] + [row[0] for row in db.execute(direct_regions).all()]
    query = select(CompanyPursuit).join(Company).where(CompanyPursuit.status != PursuitStatus.CLOSED.value)
    return query.where(
        or_(
            CompanyPursuit.assigned_staff_email == "",
            CompanyPursuit.region_id.is_(None),
            ~CompanyPursuit.region_id.in_(covered_region_ids or [-1]),
        )
    )


def _staff_options_payload(db: Session) -> str:
    def item(user: User) -> dict[str, object]:
        return {"id": user.id, "label": user.name or user.email, "email": user.email}

    by_region = {str(region_id): [item(staff) for staff in staff_members] for region_id, staff_members in _staff_options_by_region(db).items()}
    payload = {"all": [item(staff) for staff in _assignable_staff_options(db)], "byRegion": by_region}
    return json.dumps(payload)


def _active_consultants(db: Session, marketing_role_ids: Optional[set[int]] = None) -> list[ConsultantProfile]:
    query = select(ConsultantProfile).where(ConsultantProfile.active.is_(True))
    if marketing_role_ids is not None:
        query = query.where(ConsultantProfile.marketing_role_id.in_(marketing_role_ids or {-1}))
    return db.scalars(query.order_by(ConsultantProfile.name, ConsultantProfile.email)).all()


def _active_resume_versions(db: Session) -> list[ResumeVersion]:
    return db.scalars(select(ResumeVersion).join(ConsultantProfile).where(ResumeVersion.active.is_(True)).order_by(ConsultantProfile.name, ResumeVersion.updated_at.desc())).all()


def _active_resume_versions_for_consultant(db: Session, consultant_id: int) -> list[ResumeVersion]:
    return db.scalars(select(ResumeVersion).where(ResumeVersion.consultant_id == consultant_id, ResumeVersion.active.is_(True)).order_by(ResumeVersion.updated_at.desc(), ResumeVersion.id.desc())).all()


def _campaign_jobs_by_company(db: Session, targets: list[TargetingCampaignTarget]) -> dict[int, list[JobOpportunity]]:
    company_ids = sorted({target.company_id for target in targets})
    if not company_ids:
        return {}
    jobs = db.scalars(
        select(JobOpportunity)
        .join(Company)
        .join(CompanyPursuit)
        .where(JobOpportunity.company_id.in_(company_ids), JobOpportunity.active.is_(True))
        .order_by(JobOpportunity.company_id, JobOpportunity.updated_at.desc(), JobOpportunity.title.asc())
    ).all()
    grouped: dict[int, list[JobOpportunity]] = {}
    for job in jobs:
        grouped.setdefault(job.company_id, []).append(job)
    return grouped


def _resume_version_belongs_to_consultant(db: Session, resume_version_id: int, consultant_id: int) -> bool:
    return bool(db.scalar(select(ResumeVersion.id).where(ResumeVersion.id == resume_version_id, ResumeVersion.consultant_id == consultant_id)))


def _submission_belongs_to_consultant(db: Session, submission_id: int, consultant_id: int) -> bool:
    return bool(db.scalar(select(ConsultantSubmission.id).where(ConsultantSubmission.id == submission_id, ConsultantSubmission.consultant_id == consultant_id)))


def _recent_submissions(db: Session, marketing_role_ids: Optional[set[int]] = None) -> list[ConsultantSubmission]:
    query = (
        select(ConsultantSubmission)
        .join(ConsultantProfile)
        .join(JobOpportunity)
        .join(Company)
        .join(CompanyPursuit)
    )
    if marketing_role_ids is not None:
        query = query.where(ConsultantProfile.marketing_role_id.in_(marketing_role_ids or {-1}))
    return db.scalars(
        query.order_by(ConsultantSubmission.updated_at.desc()).limit(200)
    ).all()


def _parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _submission_status_options(include_all: bool = False) -> list[tuple[str, str]]:
    options = [(item.value, item.value.replace("_", " ").title()) for item in SubmissionStatus]
    return [("all", "All"), *options] if include_all else options


def _submission_status_values() -> set[str]:
    return {value for value, _label in _submission_status_options()}


def _campaign_status_options(include_all: bool = False) -> list[tuple[str, str]]:
    options = [(item.value, item.value.replace("_", " ").title()) for item in TargetingCampaignStatus]
    return [("all", "All"), *options] if include_all else options


def _campaign_status_values() -> set[str]:
    return {value for value, _label in _campaign_status_options()}


def _campaign_target_status_options() -> list[tuple[str, str]]:
    return [(item.value, item.value.replace("_", " ").title()) for item in TargetingCampaignTargetStatus]


def _campaign_target_status_values() -> set[str]:
    return {value for value, _label in _campaign_target_status_options()}


def _campaign_target_counts(db: Session, campaign_ids: list[int]) -> dict[int, dict[str, int]]:
    counts = {campaign_id: {"total": 0, "submitted": 0, "active": 0, "skipped": 0} for campaign_id in campaign_ids}
    if not campaign_ids:
        return counts
    for campaign_id, status, total in db.execute(
        select(TargetingCampaignTarget.campaign_id, TargetingCampaignTarget.status, func.count(TargetingCampaignTarget.id))
        .where(TargetingCampaignTarget.campaign_id.in_(campaign_ids))
        .group_by(TargetingCampaignTarget.campaign_id, TargetingCampaignTarget.status)
    ):
        bucket = counts.setdefault(campaign_id, {"total": 0, "submitted": 0, "active": 0, "skipped": 0})
        value = int(total or 0)
        bucket["total"] += value
        if status in {TargetingCampaignTargetStatus.SUBMITTED.value, TargetingCampaignTargetStatus.INTERVIEW.value}:
            bucket["submitted"] += value
        elif status == TargetingCampaignTargetStatus.SKIPPED.value:
            bucket["skipped"] += value
        else:
            bucket["active"] += value
    return counts


def _campaign_summary(targets: list[TargetingCampaignTarget]) -> dict[str, int]:
    return {
        "total": len(targets),
        "queued": len([item for item in targets if item.status == TargetingCampaignTargetStatus.QUEUED.value]),
        "resume": len([item for item in targets if item.status == TargetingCampaignTargetStatus.RESUME_TAILORING.value]),
        "ready": len([item for item in targets if item.status == TargetingCampaignTargetStatus.READY_TO_SUBMIT.value]),
        "submitted": len([item for item in targets if item.status in {TargetingCampaignTargetStatus.SUBMITTED.value, TargetingCampaignTargetStatus.INTERVIEW.value}]),
        "skipped": len([item for item in targets if item.status == TargetingCampaignTargetStatus.SKIPPED.value]),
    }


def _mock_status_options(include_all: bool = False) -> list[tuple[str, str]]:
    options = [(item.value, item.value.replace("_", " ").title()) for item in MockInterviewStatus]
    return [("all", "All"), *options] if include_all else options


def _mock_status_values() -> set[str]:
    return {value for value, _label in _mock_status_options()}


def _mock_round_type_options() -> list[tuple[str, str]]:
    return [
        ("mock", "Role Mock"),
        ("screening", "Screening Prep"),
        ("technical", "Technical Round"),
        ("managerial", "Managerial Round"),
        ("client", "Client Round"),
        ("follow_up", "Follow-up Mock"),
    ]


def _mock_round_type_values() -> set[str]:
    return {value for value, _label in _mock_round_type_options()}


def _job_source_options() -> list[tuple[str, str]]:
    return [
        (JobSource.CAREERS_PAGE.value, "Careers Page"),
        (JobSource.ATS.value, "ATS"),
        (JobSource.REFERRAL.value, "Referral"),
        (JobSource.STAFF_MANUAL.value, "Staff Manual"),
        (JobSource.OTHER.value, "Other"),
    ]


def _job_source_values() -> set[str]:
    return {value for value, _label in _job_source_options()}


def _job_type_options() -> list[tuple[str, str]]:
    return [("Full-time", "Full-time"), ("Contract", "Contract"), ("Part-time", "Part-time"), ("Internship", "Internship")]


def _job_type_values() -> set[str]:
    return {value for value, _label in _job_type_options()}


def _experience_level_options() -> list[tuple[str, str]]:
    return [("0-2", "0-2"), ("2-4", "2-4"), ("4-6", "4-6"), ("6+", "6+")]


def _experience_level_values() -> set[str]:
    return {value for value, _label in _experience_level_options()}


def _cloud_specialization_options() -> list[tuple[str, str]]:
    return [("aws", "AWS"), ("azure", "Azure"), ("gcp", "GCP")]


def _valid_cloud_specializations(values: list[str]) -> list[str]:
    allowed = {value for value, _label in _cloud_specialization_options()}
    return sorted({value for value in values if value in allowed})


def _company_ats_type_options() -> list[tuple[str, str]]:
    return [("greenhouse", "Greenhouse"), ("lever", "Lever"), ("workday", "Workday"), ("phenom", "Phenom"), ("ashby", "Ashby"), ("custom", "Custom")]


def _company_ats_type_values() -> set[str]:
    return {value for value, _label in _company_ats_type_options()}


def _company_signal_options() -> list[tuple[str, str]]:
    return [("unknown", "Unknown"), ("yes", "Yes"), ("no", "No"), ("mixed", "Mixed")]


def _company_signal_values() -> set[str]:
    return {value for value, _label in _company_signal_options()}


def _company_opt_risk_options() -> list[tuple[str, str]]:
    return [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("blocked", "Blocked")]


def _company_opt_risk_values() -> set[str]:
    return {value for value, _label in _company_opt_risk_options()}


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _normalize_username(username: str) -> str:
    value = username.strip().lower()
    return re.sub(r"[^a-z0-9_.-]", "_", value)


def _consultant_access_user(db: Session, email: str) -> Optional[User]:
    normalized = _normalize_email(email)
    if not normalized:
        return None
    return db.scalar(select(User).where(func.lower(User.email) == normalized))


def _consultant_profile_for_email(db: Session, email: str, *, exclude_id: Optional[int] = None) -> Optional[ConsultantProfile]:
    normalized = _normalize_email(email)
    if not normalized:
        return None
    query = select(ConsultantProfile).where(func.lower(ConsultantProfile.email) == normalized)
    if exclude_id:
        query = query.where(ConsultantProfile.id != exclude_id)
    return db.scalar(query)


def _user_with_username(db: Session, username: str, *, exclude_user_id: Optional[int] = None) -> Optional[User]:
    normalized = _normalize_username(username)
    if not normalized:
        return None
    query = select(User).where(func.lower(User.username) == normalized)
    if exclude_user_id:
        query = query.where(User.id != exclude_user_id)
    return db.scalar(query)


def _sync_consultant_access_user(db: Session, consultant: ConsultantProfile, password: str = "", *, previous_email: str = "") -> User:
    email = _normalize_email(consultant.email)
    if not email:
        raise ValueError("Consultant email is required before creating login access.")
    access_user = _consultant_access_user(db, email)
    if not access_user and previous_email and _normalize_email(previous_email) != email:
        previous_user = _consultant_access_user(db, previous_email)
        if previous_user and getattr(previous_user.role, "value", previous_user.role) == UserRole.CONSULTANT.value:
            access_user = previous_user
    if access_user and getattr(access_user.role, "value", access_user.role) != UserRole.CONSULTANT.value:
        raise ValueError("A non-consultant user already exists with this email.")
    if not access_user and not password.strip():
        raise ValueError("Temporary password is required when creating consultant login access.")
    display_name = consultant.preferred_name or consultant.name or email
    first_name = consultant.preferred_name or (consultant.name.split(" ", 1)[0] if consultant.name else display_name)
    username = _normalize_username(email)
    username_owner = _user_with_username(db, username, exclude_user_id=access_user.id if access_user else None)
    if username_owner:
        raise ValueError(f"Login username {username} is already used by {username_owner.email}. Use a different consultant email or update that existing login.")
    if access_user:
        access_user.email = email
        access_user.username = access_user.username or username
        access_user.name = display_name
        access_user.first_name = first_name
        access_user.role = UserRole.CONSULTANT.value
        access_user.active = consultant.active
        if password.strip():
            access_user.password_hash = hash_password(password.strip())
    else:
        access_user = User(
            email=email,
            username=username,
            first_name=first_name,
            last_name="",
            name=display_name,
            role=UserRole.CONSULTANT.value,
            timezone="America/Chicago",
            password_hash=hash_password(password.strip()),
            active=consultant.active,
        )
    db.add(access_user)
    return access_user


def _staff_display_name(first_name: str, last_name: str, fallback_email: str) -> str:
    name = " ".join(part for part in [first_name.strip(), last_name.strip()] if part)
    return name or fallback_email


def _staff_role_values() -> set[str]:
    return {role.value for role in UserRole}


def _staff_role_options(include_all: bool = False) -> list[tuple[str, str]]:
    options = [
        (UserRole.ADMIN.value, "Admin"),
        (UserRole.MANAGER.value, "Manager"),
        (UserRole.REGIONAL_STAFF.value, "Regional Staff"),
        (UserRole.VIEWER.value, "Viewer"),
        (UserRole.CONSULTANT.value, "Consultant"),
    ]
    if include_all:
        return [("all", "All roles"), *options]
    return options


def _timezone_options() -> list[tuple[str, str]]:
    return [
        ("America/Chicago", "America/Chicago (CST/CDT)"),
        ("America/New_York", "America/New_York (EST/EDT)"),
        ("America/Los_Angeles", "America/Los_Angeles (PST/PDT)"),
        ("America/Denver", "America/Denver (MST/MDT)"),
        ("Asia/Kolkata", "Asia/Kolkata (IST)"),
    ]


def _timezone_values() -> set[str]:
    return {value for value, _label in _timezone_options()}


def _save_staff_assignments(db: Session, staff_id: int, region_ids: Optional[list[int]], marketing_role_ids: Optional[list[int]]) -> None:
    selected_region_ids = set(region_ids or [])
    selected_role_ids = set(marketing_role_ids or [])
    valid_region_ids = set(db.scalars(select(Region.id).where(Region.id.in_(selected_region_ids), Region.active.is_(True))).all()) if selected_region_ids else set()
    valid_role_ids = set(db.scalars(select(MarketingRole.id).where(MarketingRole.id.in_(selected_role_ids), MarketingRole.active.is_(True))).all()) if selected_role_ids else set()

    db.execute(delete(StaffRegionAssignment).where(StaffRegionAssignment.user_id == staff_id))
    db.execute(delete(StaffMarketingRoleAssignment).where(StaffMarketingRoleAssignment.user_id == staff_id))
    for region_id in sorted(valid_region_ids):
        db.add(StaffRegionAssignment(user_id=staff_id, region_id=region_id, active=True))
    for role_id in sorted(valid_role_ids):
        db.add(StaffMarketingRoleAssignment(user_id=staff_id, marketing_role_id=role_id, active=True))


def _save_region_group_assignments(db: Session, group_id: int, region_ids: Optional[list[int]], member_ids: Optional[list[int]]) -> None:
    selected_region_ids = set(region_ids or [])
    selected_member_ids = set(member_ids or [])
    valid_region_ids = set(db.scalars(select(Region.id).where(Region.id.in_(selected_region_ids), Region.active.is_(True))).all()) if selected_region_ids else set()
    valid_member_ids = set(
        db.scalars(
            select(User.id).where(
                User.id.in_(selected_member_ids),
                User.active.is_(True),
                User.role.in_(_assignable_staff_roles()),
            )
        ).all()
    ) if selected_member_ids else set()

    db.execute(delete(RegionGroupRegion).where(RegionGroupRegion.group_id == group_id))
    db.execute(delete(RegionGroupMember).where(RegionGroupMember.group_id == group_id))
    for region_id in sorted(valid_region_ids):
        db.add(RegionGroupRegion(group_id=group_id, region_id=region_id, active=True))
    for member_id in sorted(valid_member_ids):
        db.add(RegionGroupMember(group_id=group_id, user_id=member_id, active=True))


def _active_group_regions(group: RegionGroup) -> list[Region]:
    regions = [assignment.region for assignment in group.regions if assignment.active and assignment.region and assignment.region.active]
    return sorted(regions, key=lambda region: region.name)


def _active_group_members(group: RegionGroup) -> list[User]:
    members = [membership.user for membership in group.members if membership.active and membership.user and membership.user.active]
    return sorted(members, key=lambda member: (member.name or member.email, member.email))


def _active_region_group_memberships(staff_member: User) -> list[RegionGroup]:
    groups = [
        membership.group
        for membership in staff_member.region_group_memberships
        if membership.active and membership.group and membership.group.active
    ]
    return sorted(groups, key=lambda group: group.name)


def _region_group_coverage(db: Session) -> dict[str, list[Any]]:
    active_regions = db.scalars(select(Region).where(Region.active.is_(True)).order_by(Region.name)).all()
    grouped_region_ids = set(
        db.scalars(
            select(RegionGroupRegion.region_id)
            .join(RegionGroup)
            .where(RegionGroup.active.is_(True), RegionGroupRegion.active.is_(True))
            .distinct()
        ).all()
    )
    groups = db.scalars(select(RegionGroup).where(RegionGroup.active.is_(True)).order_by(RegionGroup.name)).all()
    return {
        "regions_without_groups": [region for region in active_regions if region.id not in grouped_region_ids],
        "groups_without_regions": [group for group in groups if not _active_group_regions(group)],
        "groups_without_members": [group for group in groups if not _active_group_members(group)],
    }


def _assignable_staff_roles() -> list[str]:
    return [UserRole.ADMIN.value, UserRole.MANAGER.value, UserRole.REGIONAL_STAFF.value]


def _assignable_staff_member(db: Session, staff_id: int) -> Optional[User]:
    return db.scalar(select(User).where(User.id == staff_id, User.role.in_(_assignable_staff_roles())))


def _assignable_staff_options(db: Session) -> list[User]:
    return db.scalars(select(User).where(User.active.is_(True), User.role.in_(_assignable_staff_roles())).order_by(User.name, User.email)).all()


def _staff_options_for_region(db: Session, region_id: Optional[int]) -> list[User]:
    if not region_id:
        return _assignable_staff_options(db)
    direct_staff = db.scalars(
        select(User)
        .join(StaffRegionAssignment)
        .where(
            StaffRegionAssignment.region_id == region_id,
            StaffRegionAssignment.active.is_(True),
            User.active.is_(True),
            User.role.in_(_assignable_staff_roles()),
        )
        .order_by(User.name, User.email)
    ).all()
    group_staff = db.scalars(
        select(User)
        .join(RegionGroupMember, RegionGroupMember.user_id == User.id)
        .join(RegionGroup, RegionGroup.id == RegionGroupMember.group_id)
        .join(RegionGroupRegion, RegionGroupRegion.group_id == RegionGroup.id)
        .where(
            RegionGroupRegion.region_id == region_id,
            RegionGroupRegion.active.is_(True),
            RegionGroupMember.active.is_(True),
            RegionGroup.active.is_(True),
            User.active.is_(True),
            User.role.in_(_assignable_staff_roles()),
        )
        .order_by(User.name, User.email)
    ).all()
    staff_by_id = {staff_member.id: staff_member for staff_member in [*direct_staff, *group_staff]}
    return sorted(staff_by_id.values(), key=lambda item: (item.name or item.email, item.email))


def _staff_options_by_region(db: Session) -> dict[int, list[User]]:
    direct_rows = db.execute(
        select(StaffRegionAssignment.region_id, User)
        .join(User, User.id == StaffRegionAssignment.user_id)
        .where(
            StaffRegionAssignment.active.is_(True),
            User.active.is_(True),
            User.role.in_(_assignable_staff_roles()),
        )
        .order_by(StaffRegionAssignment.region_id, User.name, User.email)
    ).all()
    group_rows = db.execute(
        select(RegionGroupRegion.region_id, User)
        .join(RegionGroup, RegionGroup.id == RegionGroupRegion.group_id)
        .join(RegionGroupMember, RegionGroupMember.group_id == RegionGroup.id)
        .join(User, User.id == RegionGroupMember.user_id)
        .where(
            RegionGroupRegion.active.is_(True),
            RegionGroupMember.active.is_(True),
            RegionGroup.active.is_(True),
            User.active.is_(True),
            User.role.in_(_assignable_staff_roles()),
        )
        .order_by(RegionGroupRegion.region_id, User.name, User.email)
    ).all()
    grouped: dict[int, dict[int, User]] = {}
    for region_id, staff_member in [*direct_rows, *group_rows]:
        grouped.setdefault(region_id, {})[staff_member.id] = staff_member
    return {
        region_id: sorted(staff.values(), key=lambda item: (item.name or item.email, item.email))
        for region_id, staff in grouped.items()
    }


def _recommended_pursuit_owner(db: Session, region_id: int) -> Optional[User]:
    staff = _staff_options_for_region(db, region_id)
    if not staff:
        return None
    open_statuses = [status.value for status in PursuitStatus if status != PursuitStatus.CLOSED]
    load_by_email = dict(
        db.execute(
            select(CompanyPursuit.assigned_staff_email, func.count(CompanyPursuit.id))
            .where(
                CompanyPursuit.region_id == region_id,
                CompanyPursuit.assigned_staff_email != "",
                CompanyPursuit.status.in_(open_statuses),
            )
            .group_by(CompanyPursuit.assigned_staff_email)
        ).all()
    )
    return min(staff, key=lambda item: (load_by_email.get(item.email, 0), item.name or item.email))


def _pursuit_owner_from_form(db: Session, owner_user_id: Optional[int], region_id: Optional[int]) -> Optional[User]:
    if not owner_user_id:
        return None
    owner = _assignable_staff_member(db, owner_user_id)
    if not owner or not owner.active:
        return None
    if region_id and not _staff_assigned_to_region(owner, region_id):
        return None
    return owner


def _assign_pursuit_owner(pursuit: CompanyPursuit, owner: User) -> None:
    pursuit.assigned_staff_name = owner.name or owner.email
    pursuit.assigned_staff_email = owner.email


def _staff_region_ids(user: User | object | None) -> set[int]:
    assignments = getattr(user, "region_assignments", []) or []
    direct_ids = {
        assignment.region_id
        for assignment in assignments
        if getattr(assignment, "active", False) and getattr(assignment, "region_id", None)
    }
    group_ids: set[int] = set()
    memberships = getattr(user, "region_group_memberships", []) or []
    for membership in memberships:
        group = getattr(membership, "group", None)
        if not getattr(membership, "active", False) or not group or not getattr(group, "active", False):
            continue
        for assignment in getattr(group, "regions", []) or []:
            region = getattr(assignment, "region", None)
            if getattr(assignment, "active", False) and getattr(assignment, "region_id", None) and (region is None or getattr(region, "active", True)):
                group_ids.add(assignment.region_id)
    return direct_ids | group_ids


def _staff_assigned_to_region(user: User | object | None, region_id: Optional[int]) -> bool:
    return bool(region_id and region_id in _staff_region_ids(user))


def _can_edit_pursuit_workspace(user: User | object | None, pursuit: CompanyPursuit | object | None) -> bool:
    if not pursuit or not has_permission(user, Permission.MANAGE_PURSUIT_WORKSPACE):
        return False
    if has_permission(user, Permission.ASSIGN_PURSUITS):
        return True
    user_email = (getattr(user, "email", "") or "").lower()
    assigned_email = (getattr(pursuit, "assigned_staff_email", "") or "").lower()
    return bool((assigned_email and assigned_email == user_email) or _staff_assigned_to_region(user, getattr(pursuit, "region_id", None)))


def _pursuit_research_checklist(
    pursuit: CompanyPursuit,
    structured: dict[str, list[Any]],
    company_jobs: list[JobOpportunity],
    decision_readiness: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    company = pursuit.company
    decision_readiness = decision_readiness or {}
    uscis = decision_readiness.get("uscis") or {}
    audit = decision_readiness.get("json_audit") or {}
    profile_fields = [company.website or company.careers_url, company.industry, company.location or company.headquarters_city or company.headquarters_state]
    profile_sources = [item for item in structured.get("evidence", []) if item.kind == "company_profile" and item.url]
    requirements = structured.get("requirements", [])
    imported_jobs = structured.get("job_postings", [])
    snapshots = structured.get("intelligence_snapshots", [])
    contacts = structured.get("contacts", [])
    technologies = structured.get("technologies", [])
    vendors = structured.get("vendors", [])
    managers = structured.get("managers", [])
    notes = structured.get("notes", [])
    evidence = structured.get("evidence", [])
    return [
        _checklist_item("USCIS source", "USCIS approval/denial history is present and anchors the company decision.", int(uscis.get("total_decisions") or 0) > 0, "/intelligence"),
        _checklist_item("Company profile", "Website/careers, industry, location, and one source captured.", all(bool(value) for value in profile_fields) and bool(profile_sources), "/profile"),
        _checklist_item("Application path", "Careers URL, ATS/platform, and portal friction are captured.", bool(company.careers_url and (company.ats_type or company.ats_platform)), "/profile"),
        _checklist_item("OPT and sponsorship read", "OPT, STEM OPT, sponsorship, risk, and verification notes are reviewed.", bool(company.opt_status != "unknown" or company.sponsorship_status != "unknown" or company.opt_notes), "/profile"),
        _checklist_item("Imported intelligence", "OpenAI JSON import created an auditable company intelligence snapshot.", bool(snapshots), "/intelligence"),
        _checklist_item("Import validation", "JSON totals, role counts, USA locations, and official URLs are decision-safe.", bool(audit.get("has_snapshot")) and not decision_readiness.get("blockers") and not [item for item in decision_readiness.get("warnings", []) if "match" in item.lower() or "missing an official" in item.lower()], "/intelligence"),
        _checklist_item("Hiring signal", "At least one recent requirement or imported job posting with a source URL.", any(item.title and item.source_url for item in requirements) or any(item.job_title and item.official_job_url for item in imported_jobs), "/job-postings"),
        _checklist_item("Technology signal", "At least one technology with evidence or confidence.", any(item.name and (item.evidence or item.confidence) for item in technologies), "/technologies"),
        _checklist_item("Contact path", "At least one recruiter, manager, or department contact with source.", any(item.name and (item.source_url or item.linkedin_url or item.email) for item in contacts), "/contacts"),
        _checklist_item("Vendor path", "Prime vendor or C2C manager identified when direct contact is missing.", bool(vendors or managers), "/vendors"),
        _checklist_item("Opportunity record", "A requirement has been converted into an active job.", any(job.active for job in company_jobs), "/requirements"),
        _checklist_item("Follow-up plan", "Next action and follow-up date are scheduled.", bool(pursuit.next_action and pursuit.next_follow_up_date), "/notes"),
        _checklist_item("Decision summary", "Research summary or pinned owner note is ready for handoff.", bool(pursuit.research_summary or any(note.pinned for note in notes)), "/notes"),
        _checklist_item("Staff decision", "Owner selected pursue, watch, or do-not-pursue based on USCIS and job evidence.", bool(pursuit.decision), "/intelligence"),
        _checklist_item("Admin review ready", "No blockers remain and staff decision is ready for admin review.", bool(decision_readiness.get("ready_for_admin_review")), "/intelligence"),
        _checklist_item("Evidence coverage", "At least three source links support the pursuit.", len([item for item in evidence if item.url]) >= 3, "/notes"),
    ]


def _checklist_item(label: str, detail: str, complete: bool, tab_anchor: str) -> dict[str, Any]:
    return {"label": label, "detail": detail, "complete": complete, "tab": tab_anchor.lstrip("/")}


def _checklist_completion(items: list[dict[str, Any]]) -> dict[str, int]:
    total = len(items)
    complete = len([item for item in items if item["complete"]])
    percent = round((complete / total) * 100) if total else 0
    return {"complete": complete, "total": total, "percent": percent}


def _active_region_assignments(staff_member: User) -> list[Region]:
    regions = [assignment.region for assignment in staff_member.region_assignments if assignment.active and assignment.region and assignment.region.active]
    return sorted(regions, key=lambda region: region.name)


def _active_marketing_role_assignments(staff_member: User) -> list[MarketingRole]:
    roles = [
        assignment.marketing_role
        for assignment in staff_member.marketing_role_assignments
        if assignment.active and assignment.marketing_role and assignment.marketing_role.active
    ]
    return sorted(roles, key=lambda role: role.name)


def _decision_expressions(decision_type: str):
    if decision_type == UscisDecisionType.NEW_EMPLOYMENT.value:
        return UscisEmployerYearlyStat.new_employment_approval, UscisEmployerYearlyStat.new_employment_denial
    if decision_type == UscisDecisionType.CONTINUATION.value:
        return UscisEmployerYearlyStat.continuation_approval, UscisEmployerYearlyStat.continuation_denial
    if decision_type == UscisDecisionType.CHANGE_SAME_EMPLOYER.value:
        return UscisEmployerYearlyStat.change_same_employer_approval, UscisEmployerYearlyStat.change_same_employer_denial
    if decision_type == UscisDecisionType.NEW_CONCURRENT.value:
        return UscisEmployerYearlyStat.new_concurrent_approval, UscisEmployerYearlyStat.new_concurrent_denial
    if decision_type == UscisDecisionType.CHANGE_EMPLOYER.value:
        return UscisEmployerYearlyStat.change_employer_approval, UscisEmployerYearlyStat.change_employer_denial
    if decision_type == UscisDecisionType.AMENDED.value:
        return UscisEmployerYearlyStat.amended_approval, UscisEmployerYearlyStat.amended_denial
    return UscisEmployerYearlyStat.total_approvals, UscisEmployerYearlyStat.total_denials


def _company_fit_signal(row: Any, approval_rate: float, profile: str) -> dict[str, Any]:
    new_count = int(row["new_employment_approvals"] or 0)
    transfer_count = int(row["change_employer_approvals"] or 0)
    continuation_count = int(row["continuation_approvals"] or 0)
    states = int(row["states"] or 0)
    cities = int(row["cities"] or 0)
    decision_approvals = int(row["decision_approvals"] or 0)
    score = 0
    notes = []
    if approval_rate >= 95:
        score += 25
        notes.append("very high approval rate")
    elif approval_rate >= 85:
        score += 15
        notes.append("healthy approval rate")
    if new_count >= 50:
        score += 25
        notes.append("strong new employment volume")
    elif new_count >= 10:
        score += 15
        notes.append("some new employment volume")
    if transfer_count >= 25:
        score += 20
        notes.append("strong H1B transfer signal")
    elif transfer_count >= 5:
        score += 10
        notes.append("some H1B transfer signal")
    if continuation_count >= 25:
        score += 15
        notes.append("retains sponsored workers")
    if states >= 3 or cities >= 3:
        score += 10
        notes.append("multi-location footprint")
    if decision_approvals >= 100:
        score += 10
    elif decision_approvals >= 20:
        score += 5

    profile_match = True
    if profile == "opt":
        profile_match = new_count >= 5 and approval_rate >= 80
    elif profile == "h1b":
        profile_match = (new_count + transfer_count + continuation_count) >= 20 and approval_rate >= 80
    elif profile == "transfer":
        profile_match = transfer_count >= 5 and approval_rate >= 80
    elif profile == "consulting":
        profile_match = (new_count + transfer_count) >= 10 and states >= 1 and approval_rate >= 75

    if score >= 70:
        label = "Prime target"
    elif score >= 45:
        label = "Good target"
    elif score >= 25:
        label = "Watchlist"
    else:
        label = "Low signal"
    return {"score": min(score, 100), "label": label, "notes": ", ".join(notes) or "limited USCIS signal", "matches": profile_match}


def _sponsorship_likelihood_signal(row: Any, all_approval_rate: float, annual_approvals: float) -> dict[str, Any]:
    approvals = int(row["approvals"] or 0)
    denials = int(row["denials"] or 0)
    years = int(row["years"] or 0)
    latest_year = int(row["latest_year"] or 0)
    new_count = int(row["new_employment_approvals"] or 0)
    transfer_count = int(row["change_employer_approvals"] or 0)
    continuation_count = int(row["continuation_approvals"] or 0)
    states = int(row["states"] or 0)
    score = 0
    notes = []

    if 51 <= annual_approvals <= 300:
        score += 25
        notes.append("ideal mid-size annual volume")
    elif 51 <= annual_approvals <= 500:
        score += 20
        notes.append("mid-size annual sponsor volume")
    elif 5 <= annual_approvals <= 50:
        score += 10
        notes.append("emerging annual sponsor volume")
    elif annual_approvals > 5000:
        score -= 15
        notes.append("mega annual sponsor saturation")
    elif annual_approvals > 500:
        score += 8
        notes.append("larger annual sponsor volume")

    if all_approval_rate >= 95:
        score += 25
        notes.append("very high approval rate")
    elif all_approval_rate >= 90:
        score += 20
        notes.append("strong approval rate")
    elif all_approval_rate >= 85:
        score += 15
        notes.append("healthy approval rate")

    if latest_year >= 2024:
        score += 15
        notes.append("recent USCIS activity")
    if years >= 5:
        score += 15
        notes.append("consistent multi-year activity")
    elif years >= 3:
        score += 10
        notes.append("repeat activity")

    if new_count >= 10:
        score += 10
        notes.append("new H1B hiring signal")
    if transfer_count >= 5:
        score += 10
        notes.append("transfer-friendly signal")
    if continuation_count >= 5:
        score += 5
        notes.append("retention signal")
    if states >= 2:
        score += 5
        notes.append("multi-state footprint")
    if denials > approvals * 0.2:
        score -= 15
        notes.append("higher denial risk")

    score = max(0, min(score, 100))
    if score >= 75:
        label = "Likely sponsor"
    elif score >= 55:
        label = "Good prospect"
    elif score >= 35:
        label = "Possible sponsor"
    else:
        label = "Low confidence"
    return {"score": score, "label": label, "notes": ", ".join(notes) or "limited sponsorship signal"}


def _target_size_label(annual_approvals: float) -> str:
    if 51 <= annual_approvals <= 300:
        return "Sweet spot"
    if 51 <= annual_approvals <= 500:
        return "Mid-size"
    if 5 <= annual_approvals <= 50:
        return "Emerging"
    if 501 <= annual_approvals <= 5000:
        return "Large"
    if annual_approvals > 5000:
        return "Mega"
    return "Low volume"


def _decision_options() -> list[dict[str, str]]:
    return [
        {"value": UscisDecisionType.ALL.value, "label": "All decisions"},
        {"value": UscisDecisionType.NEW_EMPLOYMENT.value, "label": "New employment"},
        {"value": UscisDecisionType.CHANGE_EMPLOYER.value, "label": "Change of employer"},
        {"value": UscisDecisionType.CONTINUATION.value, "label": "Continuation"},
        {"value": UscisDecisionType.CHANGE_SAME_EMPLOYER.value, "label": "Change same employer"},
        {"value": UscisDecisionType.NEW_CONCURRENT.value, "label": "New concurrent"},
        {"value": UscisDecisionType.AMENDED.value, "label": "Amended"},
    ]


def _profile_options() -> list[dict[str, str]]:
    return [
        {"value": "all", "label": "All companies"},
        {"value": "opt", "label": "OPT / new H1B friendly"},
        {"value": "h1b", "label": "H1B marketing target"},
        {"value": "transfer", "label": "H1B transfer friendly"},
        {"value": "consulting", "label": "Consultant marketing fit"},
    ]


def _target_size_options() -> list[dict[str, str]]:
    return [
        {"value": "all", "label": "All sizes"},
        {"value": "sweet_spot", "label": "Sweet spot: 51-300 approvals/year"},
        {"value": "mid_size", "label": "Mid-size sponsor: 51-500 approvals/year"},
        {"value": "emerging", "label": "Emerging sponsor: 5-50 approvals/year"},
        {"value": "large", "label": "Large: 501-5,000 approvals/year"},
        {"value": "mega", "label": "Mega: 5,000+ approvals/year"},
    ]


def _training_sections() -> list[dict[str, str]]:
    return [
        {"key": "start", "label": "Start Here"},
        {"key": "overview", "label": "Overview"},
        {"key": "assessment", "label": "Onboarding Test"},
        {"key": "architecture", "label": "Architecture"},
        {"key": "usecases", "label": "Use Cases"},
        {"key": "responsibilities", "label": "Responsibilities"},
        {"key": "glossary", "label": "Glossary"},
        {"key": "workflows", "label": "Workflows"},
        {"key": "plan", "label": "Project Plan"},
        {"key": "timeline", "label": "Project Workstreams"},
        {"key": "interview", "label": "Interview Prep"},
        {"key": "resume", "label": "Resume Summary"},
    ]


def _training_role_terms(role_name: str) -> list[dict[str, Any]]:
    return [item for item in MARKETING_ROLE_GLOSSARY if item["roles"] == role_name]


def _training_concept_cards(program: TrainingProgram) -> list[dict[str, str]]:
    role = program.marketing_role
    tools = _split_training_items(role.common_tools)[:8]
    return [
        {
            "title": "Role Workflow",
            "focus": role.description,
            "consultant_angle": f"Explain how a {role.name} receives work, validates requirements, coordinates with teams, implements changes, and supports the result after delivery.",
            "proof": "Use one project story where you owned a task from ticket or requirement through validation and handoff.",
        },
        {
            "title": "Tool Chain",
            "focus": ", ".join(tools[:6]),
            "consultant_angle": "Tool knowledge is strongest when it describes the workflow input, tool output, validation signal, and operational result.",
            "proof": "Prepare a simple diagram or spoken flow connecting at least four tools.",
        },
        {
            "title": "Production Thinking",
            "focus": "Monitoring, troubleshooting, access, rollback, documentation, and support ownership.",
            "consultant_angle": "Clients listen for production maturity. Show that you understand validation, change risk, incident impact, and clean handoff.",
            "proof": "Prepare one incident or production support story with cause, action, prevention, and result.",
        },
        {
            "title": "Client Communication",
            "focus": "Status updates, blockers, tradeoffs, team coordination, and measurable outcomes.",
            "consultant_angle": "My enterprise answer stays calm and specific: what happened, what I checked, who I involved, what changed, and what improved.",
            "proof": "Practice a 60-second and 2-minute version of the same story.",
        },
    ]


def _training_concept_coverage_map(program: TrainingProgram) -> list[dict[str, Any]]:
    def unique_text(items: list[Any]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            text = _pdf_clean_text(item) if not isinstance(item, str) else item.strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    role_name = program.marketing_role.name
    domain = program.industry_domain
    architecture = program.cloud_architecture or {}
    basics_terms: list[str] = []
    for module in _training_basics_preparation_modules():
        basics_terms.extend(_as_list(module.get("concepts")))
    role_terms = [item["term"] for item in _training_role_terms(role_name)[:30]]
    domain_terms = [
        item.get("term", "")
        for item in _as_list(architecture.get("productGlossary"))[50:80]
        if isinstance(item, dict) and item.get("term")
    ]
    team_context = [
        "product owners",
        "application developers",
        "QA",
        "security",
        "platform team",
        "operations",
        "service desk",
        "data team",
        "business stakeholders",
        "vendor or downstream owner",
    ]
    application_context = _as_list(program.application_landscape)[:12]
    implementation_context = unique_text(
        [
            *role_terms[:12],
            *_as_list(program.tools_and_technologies)[:10],
            *_as_list(architecture.get("coreComponents"))[:10],
        ]
    )
    use_case_titles = [
        item.get("title", "")
        for item in _as_list(architecture.get("deliveredUseCases"))[:12]
        if isinstance(item, dict) and item.get("title")
    ]
    enterprise_lifecycle_terms = [
        "application sunrise",
        "application sunset",
        "application modernization",
        "legacy migration",
        "cloud migration",
        "re-platforming",
        "data center exit",
        "environment promotion",
        "release cutover",
        "blue-green deployment",
        "canary rollout",
        "rollback / roll-forward",
        "disaster recovery drill",
        "backup and restore validation",
        "certificate rotation",
        "secrets rotation",
        "IAM access review",
        "firewall / network rule change",
        "DNS cutover",
        "API gateway migration",
        "database migration",
        "schema migration",
        "data archival and retention",
        "observability onboarding",
        "alert tuning / noise reduction",
        "runbook creation",
        "incident simulation",
        "postmortem / RCA",
        "SLO / SLA definition",
        "capacity planning",
        "cost optimization",
        "compliance audit evidence",
        "vendor integration onboarding",
        "third-party API failure handling",
        "CMDB / ServiceNow ownership update",
        "feature flag rollout",
        "tenant onboarding",
        "multi-region readiness",
        "performance testing",
        "production readiness review",
        "change management / CAB approval",
        "operational handoff",
        "knowledge transfer",
        "support model transition",
    ]
    return [
        {
            "area": "Basics Prep Fundamentals",
            "where": "2-week Basics Prep before role/domain training",
            "concepts": unique_text(basics_terms),
            "practice": "Explains the shared technology behavior behind code, APIs, Linux, cloud, containers, CI/CD, monitoring, security, data movement, and agile delivery.",
            "proof": "Command output, flowchart, runbook note, quiz answer, and checkpoint evidence show that the concept is understood.",
        },
        {
            "area": "Company Structure Context",
            "where": f"{domain} enterprise operating model for {role_name}",
            "concepts": team_context,
            "practice": "Shows how product decisions, application code, platform changes, security review, QA validation, operations support, and service desk intake fit together.",
            "proof": "Ownership matrix, escalation path, support handoff, and role-boundary explanation.",
        },
        {
            "area": "Domain Product System Context",
            "where": f"{domain} product/application landscape",
            "concepts": application_context + domain_terms[:12],
            "practice": "Shows user/business flow, connected systems, operational risk, data or dependency path, and support signal for each major product system.",
            "proof": "Product-system notes, workflow diagram, failure-point list, and role-specific product explanation.",
        },
        {
            "area": "Marketing Role Implementation Context",
            "where": f"{role_name} owned activities inside the project",
            "concepts": implementation_context,
            "practice": "Explains the role-owned part of current-state flow, target design, build/configuration change, validation, failure recovery, and support handoff.",
            "proof": "Jira stories, implementation artifact, validation output, runbook, rollback/recovery note, and project narrative.",
        },
        {
            "area": "Use-Case Scenario Practice",
            "where": "10-12 project scenarios with artifacts",
            "concepts": use_case_titles,
            "practice": "Shows every concept through a project scenario with implementation sequence, validation checks, failure behavior, and recovery path.",
            "proof": "Scenario artifact pack, acceptance criteria, replication lab output, and 60-second/5-minute interview answers.",
        },
        {
            "area": "Enterprise Application Lifecycle Concepts",
            "where": "Basics introduces the vocabulary; role/domain training turns it into sunrise, sunset, migration, cutover, governance, and operational transition use cases.",
            "concepts": enterprise_lifecycle_terms,
            "practice": "Explains how enterprise applications are onboarded, modernized, migrated, released, supported, governed, retired, cleaned up, and handed over without losing business continuity.",
            "proof": "Application inventory, dependency map, migration/cutover checklist, CAB approval, rollback plan, backup/restore proof, DR test, access review, CMDB/ServiceNow update, runbook, RCA, and knowledge-transfer note.",
        },
    ]


def _training_usecase_cards(program: TrainingProgram) -> list[dict[str, str]]:
    role = program.marketing_role
    tools = _split_training_items(role.common_tools)
    primary_tools = ", ".join(tools[:4]) or role.name
    scenarios = [
        ("Delivery Automation", "A release or operational process had too many manual steps and needed a repeatable workflow.", "reduced manual effort, improved consistency, and gave the team a clearer promotion path"),
        ("Production Incident", "A production issue required logs, metrics, alerts, ownership checks, and a clean recovery path.", "reduced troubleshooting time and improved the runbook for future incidents"),
        ("Cloud or Platform Migration", "A team was moving workloads or workflows into a new cloud, platform, or managed environment.", "improved scalability, standardization, and operational visibility"),
        ("Security and Access Cleanup", "Access, secrets, permissions, or compliance expectations were unclear or inconsistent.", "improved audit readiness and reduced support risk"),
        ("Monitoring and Observability", "The team could not quickly understand health, failures, latency, cost, data freshness, or user impact.", "created better dashboards, alerts, and ownership signals"),
        ("Performance Optimization", "A service, pipeline, model, database, or platform workflow was slow, unstable, or expensive.", "improved response time, throughput, cost, reliability, or support confidence"),
        ("Environment Standardization", "Dev, test, stage, and production behaved differently and caused repeated release or support issues.", "made environments more consistent and reduced avoidable failures"),
        ("Cross-Team Handoff", "The work required coordination across developers, QA, cloud, security, operations, data, or vendor teams.", "improved accountability and reduced confusion during delivery"),
        ("Documentation and Runbook", "The team had working systems but weak documentation, handoff notes, or repeatable support steps.", "made onboarding, support, and incident response easier"),
        ("Client-Facing Improvement", "A client or manager needed a clear explanation of risk, progress, result, and next action.", "improved trust because the work was explained with facts instead of vague status updates"),
    ]
    cards = []
    for title, situation, result in scenarios:
        cards.append(
            {
                "title": title,
                "short": situation,
                "talk_track": (
                    f"In a {role.name} project, I handled a situation where {situation.lower()} "
                    f"My first step was to understand the current flow, confirm ownership, and identify where the process was failing or creating risk. "
                    f"I worked with the right teams, used tools such as {primary_tools}, validated changes in a lower environment where possible, and documented the final process so it could be repeated. "
                    f"The result was that we {result}. This is the kind of example I use because it shows both technical work and the practical communication expected from a consultant."
                ),
                "evidence": "Mention ticket, dashboard, pipeline run, log review, pull request, runbook, metric, or client update.",
            }
        )
    return cards


def _training_interview_banks(program: TrainingProgram) -> list[dict[str, Any]]:
    role = program.marketing_role
    tools = _split_training_items(role.common_tools)
    tool_one = tools[0] if tools else role.name
    tool_two = tools[1] if len(tools) > 1 else "the main platform"
    return [
        {
            "category": "Screening",
            "questions": [
                f"How would you explain your {role.name} experience in two minutes?",
                f"Which tools have you used most from this role, especially {tool_one} and {tool_two}?",
                "What kind of environments have you supported: development, QA, staging, production, or client-facing?",
                "What is one measurable improvement from your recent project?",
            ],
        },
        {
            "category": "Technical Fundamentals",
            "questions": [
                f"What are the core responsibilities of a {role.name}?",
                f"Walk me through how {tool_one} fits into your day-to-day workflow.",
                "How do you validate that a change is working correctly?",
                "How do you handle rollback, failure, or unexpected behavior?",
            ],
        },
        {
            "category": "Scenario Troubleshooting",
            "questions": [
                "A deployment or workflow failed. What do you check first?",
                "A production alert is noisy and keeps repeating. How do you approach it?",
                "A team says the issue is with your area, but you are not sure. How do you investigate?",
                "How do you communicate risk and status to a manager or client?",
            ],
        },
        {
            "category": "Project Deep-Dive",
            "questions": [
                "Describe one project where you improved reliability, automation, or supportability.",
                "What was your exact responsibility, not just the team's responsibility?",
                "What tradeoff did you make and why?",
                "What would you improve if you did the same project again?",
            ],
        },
    ]


def _training_weekly_plan(program: TrainingProgram) -> list[dict[str, str]]:
    role = program.marketing_role
    return [
        {"week": "Prerequisite Weeks 1-2", "theme": "Basics Prep", "goal": "Complete the 12-day Basics Prep first: terminal, Git, Docker, Kubernetes, cloud, CI/CD, observability, Terraform/IaC, security, Agile, APIs, SQL, Ansible, Shell/Python automation, diagrams, and evidence notes."},
        {"week": "Role Week 1", "theme": "Role Vocabulary, Domain Systems, And JD Language", "goal": f"Learn role vocabulary for {role.name}, map domain/product systems, identify keywords in active JDs, and explain each term in plain English."},
        {"week": "Role Week 2", "theme": "Reference Architecture And Core Workflows", "goal": f"Understand the {role.name} workflow, provider reference architectures, responsibilities, tools, production expectations, and ownership boundaries."},
        {"week": "Role Week 3", "theme": "Hands-On Labs And 10-12 Use Cases", "goal": "Complete role/domain labs and convert each major use case into one diagram, one evidence package, one troubleshooting note, and one resume bullet."},
        {"week": "Role Week 4", "theme": "Interview, Resume, And Submission Readiness", "goal": "Connect screening, technical, scenario, system-design, and project deep-dive answers to resume bullets, JD matching notes, and evidence."},
    ]


def _training_lab_cards(program: TrainingProgram) -> list[dict[str, str]]:
    role = program.marketing_role
    tools = _split_training_items(role.common_tools)
    tool_text = ", ".join(tools[:5]) or role.name
    return [
        {"title": "Build", "task": f"Create a small {role.name} workflow using {tool_text}.", "deliverable": "Screenshot or notes, configuration summary, and one resume bullet."},
        {"title": "Break", "task": "Introduce a realistic failure such as wrong config, missing permission, bad input, failed check, or capacity issue.", "deliverable": "Troubleshooting notes with symptoms, cause, fix, and prevention."},
        {"title": "Operate", "task": "Add monitoring, validation, logs, alerts, documentation, or handoff notes.", "deliverable": "Runbook and production-support story."},
        {"title": "Explain", "task": "Present the lab as if speaking to a client interviewer.", "deliverable": "60-second summary and 5-minute deep-dive answer."},
    ]


def _training_basics_preparation_modules() -> list[dict[str, Any]]:
    modules = [
        {
            "title": "1. Terminal, Linux, Networking, And Troubleshooting",
            "why": "Before Docker, Kubernetes, cloud, or CI/CD, the foundation is file navigation, process checks, basic network terms, terminal evidence, and troubleshooting signals.",
            "concepts": ["current directory", "environment variables", "standard output", "processes", "ports", "logs", "exit code", "permissions", "SSH", "DNS", "HTTP", "HTTPS", "TLS certificate", "private IP", "public IP", "firewall/security group", "load balancer", "latency", "timeout", "connection refused"],
            "commands": [
                "pwd",
                "ls -la",
                "cd ~/project",
                "mkdir -p labs/basics",
                "touch app.log",
                "cat app.log",
                "tail -f app.log",
                "grep -i error app.log",
                "ps -ef | grep python",
                "lsof -i :8080",
                "ssh user@server",
                "nslookup example.com",
                "curl -I https://example.com",
                "chmod +x run.sh",
                "echo $PATH",
            ],
            "command_groups": _training_basics_linux_command_map(),
            "drill": "Create a small log file, add five fake errors, find them with grep, check one port, one DNS lookup, and one HTTP response header, then explain what evidence you would send to a lead.",
            "interview": "When a service fails, say how you checked the process, port, log, DNS, HTTP/HTTPS response, config, and recent change before guessing.",
        },
        {
            "title": "2. Git, Branches, Pull Requests, And Release Evidence",
            "why": "Technical work must connect to reviewable evidence. Git history, pull requests, commits, tags, and release notes prove what changed and who reviewed it.",
            "concepts": ["clone", "init", "add", "branch", "commit", "diff", "status", "log", "pull request", "merge", "rebase", "tag", "fetch", "pull", "push", "rollback", "release notes"],
            "commands": [
                "git clone <repo-url>",
                "git init",
                "git status",
                "git switch -c feature/add-health-check",
                "git diff",
                "git add .",
                "git commit -m \"Add health check\"",
                "git log --oneline -5",
                "git show --stat",
                "git branch",
                "git fetch origin",
                "git pull --rebase",
                "git push origin feature/add-health-check",
                "git tag release-001",
                "git revert <commit>",
            ],
            "command_groups": _training_basics_git_command_map(),
            "drill": "Make one small config change, commit it, explain the diff, and write a two-line release note.",
            "interview": "Use Git as evidence: where the repository started, what changed, what the diff showed, who reviewed it, how history was managed, how the branch was synced, how it was promoted, and how rollback would happen.",
        },
        {
            "title": "3. Docker Images, Containers, Logs, And Debugging",
            "why": "Modern cloud and DevOps roles expect a beginner to understand the difference between source code, image, container, port, volume, and log output.",
            "concepts": ["Dockerfile", "image", "container", "runtime", "registry", "port mapping", "environment variable", "volume", "network", "container log", "health check", "Docker Compose"],
            "commands": [
                "docker --help",
                "docker build -t sample-api:local .",
                "docker pull nginx:latest",
                "docker images",
                "docker run --name sample-api -p 8080:8080 sample-api:local",
                "docker ps",
                "docker ps -a",
                "docker logs sample-api",
                "docker exec -it sample-api sh",
                "docker inspect sample-api",
                "docker stats",
                "docker port sample-api",
                "docker stop sample-api",
                "docker rm sample-api",
                "docker rmi sample-api:local",
                "docker system df",
                "docker compose up -d",
                "docker compose logs -f",
            ],
            "command_groups": _training_basics_docker_command_map(),
            "drill": "Build and run a container, map a port, break one environment variable, inspect logs, check runtime stats, fix it, and write the runbook note.",
            "interview": "Explain image versus container, how traffic reaches the container, what registry actions do, how Docker Compose groups services, and what logs/stats/inspect output prove the app started correctly.",
        },
        {
            "title": "4. Kubernetes Core Objects And Failure Reading",
            "why": "Kubernetes appears in many Cloud Platform, DevOps, SRE, MLOps, and Data Platform postings. The goal is not memorizing YAML; it is knowing how workloads run and how to inspect failures.",
            "concepts": ["cluster", "control plane", "worker node", "namespace", "manifest", "pod", "deployment", "replicaset", "service", "ingress", "configmap", "secret", "persistent volume", "persistent volume claim", "events", "rollout", "readiness probe", "liveness probe", "metrics server", "kubectl context"],
            "commands": [
                "kubectl version",
                "kubectl cluster-info",
                "kubectl config current-context",
                "kubectl get namespaces",
                "kubectl apply -f app.yaml",
                "kubectl get pods -A",
                "kubectl get deploy -n app",
                "kubectl describe pod <pod> -n app",
                "kubectl logs <pod> -n app",
                "kubectl logs <pod> -c <container> -n app",
                "kubectl get events -n app --sort-by=.lastTimestamp",
                "kubectl rollout status deploy/<name> -n app",
                "kubectl rollout undo deploy/<name> -n app",
                "kubectl exec -it <pod> -n app -- sh",
                "kubectl top pods -n app",
                "kubectl get all -n app",
            ],
            "command_groups": _training_basics_kubernetes_command_map(),
            "drill": "Read a failed pod story: image pull failure, crash loop, bad config, missing secret, storage claim issue, scaling issue, or readiness probe failure.",
            "interview": "Walk through cluster, namespace, manifest, deployment, ReplicaSet, pod, service, and ingress, then explain logs, describe, events, rollout, scale, metrics, and rollback.",
        },
        {
            "title": "5. Cloud Basics: Network, Identity, Compute, Storage",
            "why": "Detailed role material assumes cloud building blocks are already recognizable before architecture discussions. This module turns cloud vocabulary into the minimum architecture language needed for screening calls.",
            "concepts": ["account/subscription/project", "region", "availability zone", "VPC/VNet", "public subnet", "private subnet", "route table", "internet gateway", "NAT gateway", "security group/NSG", "firewall rule", "load balancer", "IAM user", "IAM role", "policy", "RBAC", "virtual machine", "container service", "serverless function", "object storage", "block storage", "managed database", "backup", "disaster recovery", "high availability", "failover", "RTO/RPO"],
            "commands": [
                "aws sts get-caller-identity",
                "aws ec2 describe-vpcs",
                "aws s3 ls",
                "az account show",
                "az group list -o table",
                "az storage account list -o table",
                "gcloud auth list",
                "gcloud config list",
                "gcloud compute networks list",
                "gcloud storage buckets list",
            ],
            "drill": "Draw account/subscription/project boundary, network boundary, runtime, storage, identity, logs, backup, and recovery path for one simple app.",
            "interview": "Explain a simple cloud architecture from account to region to network to compute/storage/database to identity and recovery. Backup protects data, high availability keeps service running during component failure, disaster recovery restores service after a larger outage, and failover moves traffic to a healthy target.",
        },
        {
            "title": "6. CI/CD Pipeline Basics And Release Confidence",
            "why": "Marketing interviews often ask about build, test, deploy, approval, rollback, and environment promotion. The release story explains how code safely moves across environments before tool-specific pipelines are introduced.",
            "concepts": ["source trigger", "build", "unit test", "artifact", "artifact repository", "container registry", "image tag", "image scan", "deploy", "approval", "rollback", "pipeline log", "environment promotion"],
            "commands": [
                "gh run list",
                "gh run view <run-id> --log",
                "gitlab-runner --version",
                "jenkins-cli build <job-name>",
                "docker build -t app:${GIT_SHA} .",
                "docker push <registry>/app:${GIT_SHA}",
                "docker pull <registry>/app:${GIT_SHA}",
                "kubectl rollout status deploy/app -n app",
                "kubectl rollout history deploy/app -n app",
            ],
            "drill": "Write the release evidence chain: commit, build run, artifact or image tag, registry location, deployment, health check, rollback command.",
            "interview": "Answer release questions with the actual stages and evidence: plan, code, commit, build, test, artifact, deploy, run, monitor, and rollback.",
        },
        {
            "title": "7. Observability, Logs, Metrics, Traces, And Alerts",
            "why": "Telemetry gives my interview answer production depth. When a user says the system is slow or down, I explain what signal I check and why it matters.",
            "concepts": ["log", "metric", "trace", "dashboard", "alert", "SLO", "latency", "error rate", "saturation", "runbook", "failover"],
            "commands": [
                "kubectl logs deploy/app -n app --since=30m",
                "curl -i https://example.com/health",
                "curl -w '%{http_code} %{time_total}\\n' -o /dev/null -s https://example.com/health",
                "aws logs describe-log-groups",
                "aws logs tail /aws/lambda/app --follow",
                "az monitor metrics list --resource <resource-id>",
                "gcloud logging read 'severity>=ERROR' --limit=20",
            ],
            "drill": "Build a three-signal incident note: what users saw, what metric/log/trace showed, what changed, and how it was resolved.",
            "interview": "Use the golden signals: latency, traffic, errors, and saturation. Then explain alert action, failover option, and runbook ownership.",
        },
        {
            "title": "8. Terraform, Infrastructure As Code, And Change Safety",
            "why": "IaC teaches repeatability, review, drift control, and rollback thinking. It is central to cloud platform and DevOps credibility.",
            "concepts": ["desired state", "state file", "plan", "apply", "module", "variable", "output", "drift", "policy", "reference architecture"],
            "commands": [
                "terraform init",
                "terraform fmt",
                "terraform validate",
                "terraform plan",
                "terraform apply",
                "terraform output",
                "terraform state list",
                "terraform destroy",
                "tofu init",
                "tofu plan",
            ],
            "drill": "Explain a Terraform plan like a change review: what will be created, changed, or destroyed and what risk exists.",
            "interview": "Mention peer review, plan output, state management, environment separation, variables, reference architecture fit, and rollback limitations.",
        },
        {
            "title": "9. Security, Secrets, IAM, And Audit Basics",
            "why": "Even entry-level cloud work touches access and secrets. The material explains practical security failures: missing permission, expired secret, public storage, leaked environment value, weak RBAC, certificate expiry, and missing audit evidence.",
            "concepts": ["least privilege", "role", "policy", "service account", "secret manager", "key vault", "audit log", "rotation", "security scan", "compliance", "expired secret", "certificate expiry", "public storage risk", "leaked environment variable", "RBAC mismatch", "vulnerability scan", "encryption at rest", "encryption in transit"],
            "commands": [
                "aws iam get-user",
                "aws iam list-attached-role-policies --role-name <role>",
                "aws secretsmanager list-secrets",
                "az role assignment list --assignee <principal>",
                "az keyvault secret list --vault-name <vault>",
                "gcloud iam service-accounts list",
                "gcloud secrets list",
                "kubectl get secrets -n app",
            ],
            "drill": "Take one application and list what identities it uses, which secrets it needs, which scan or audit proof should exist, and who can approve access.",
            "interview": "Never say passwords are kept in code. Explain secret manager, RBAC/IAM, audit logs, controlled access, and compliance evidence.",
        },
        {
            "title": "10. Agile, Jira, APIs, JSON/YAML, SQL, Cost, And Evidence",
            "why": "Every marketing role training track uses agile delivery language plus common technical artifacts. Consultants must understand stories, acceptance criteria, sprint updates, blockers, APIs, config files, SQL checks, cost signals, evidence, and handoffs before learning role-specific project work.",
            "concepts": ["sprint", "story", "task", "bug", "epic", "acceptance criteria", "definition of done", "blocker", "standup", "retrospective", "handoff", "API", "HTTP method", "status code", "request", "response", "header", "JSON", "YAML", "environment config", "SQL select", "row count", "null check", "duplicate check", "freshness check", "cost tag", "evidence template"],
            "commands": [
                "Write one user story using: As a / I want / So that",
                "Write three acceptance criteria for the story",
                "Split one large task into design, build, test, deploy, and support subtasks",
                "Write a daily standup update: yesterday, today, blocker",
                "Write a sprint evidence note: ticket, change, validation, screenshot/log, handoff",
                "Write a blocker note with owner, impact, next action, and due date",
                "curl -i https://example.com/health",
                "jq . sample.json",
                "yq . sample.yaml",
                "psql -c \"select count(*) from sample_table;\"",
                "psql -c \"select count(*) from sample_table where important_field is null;\"",
            ],
            "drill": "Take one technical lab from this basics workbook and turn it into a Jira story with acceptance criteria, subtasks, API/config/SQL validation evidence, cost or risk note, and a standup update.",
            "interview": "Explain work as a delivered story: problem, ticket, acceptance criteria, API or config signal, SQL/data check, cost or risk signal, implementation, validation, handoff, and outcome.",
        },
        {
            "title": "11. Ansible, Shell And Python Automation, And Runbooks",
            "why": "The attached automation guide is useful as beginner practice because it shows the kinds of small scripts and Ansible basics consultants are asked about: inventory, SSH, ad-hoc checks, playbooks, roles, monitoring, backups, deployments, API checks, image scans, logs, configuration, and cloud operations.",
            "concepts": ["Ansible", "inventory", "SSH access", "ad-hoc command", "playbook", "role", "Shell scripting", "Python automation", "subprocess", "requests", "JSON", "YAML", "logging", "backup", "health check", "Jenkins trigger", "Kubernetes deployment", "Terraform apply", "image scanning", "disk alert", "load test", "boto3", "runbook"],
            "commands": [
                "ansible all -i inventory.ini -m ping",
                "ansible all -i inventory.ini -a \"uptime\"",
                "ansible-playbook -i inventory.ini playbook.yml",
                "bash scripts/check_health.sh",
                "bash scripts/backup_mysql.sh",
                "bash scripts/rotate_logs.sh",
                "bash scripts/trigger_jenkins.sh",
                "kubectl set image deployment/app app=repo/app:v1",
                "terraform plan",
                "trivy image repo/app:latest",
                "df -h",
                "curl --write-out \"%{http_code}\" --silent --output /dev/null https://example.com/health",
                "python3 scripts/read_json.py",
                "python3 scripts/call_api.py",
                "python3 scripts/check_system_metrics.py",
            ],
            "drill": "Pick five beginner automation examples: Ansible ping/ad-hoc command, health check, log cleanup, backup verification, API test, and image scan. For each one, write what it checks, the expected healthy output, the failure output, and what team owns the next action.",
            "interview": "When asked about automation, explain why Ansible is useful for repeatable remote configuration, Shell is good for quick system commands, and Python is better for APIs, JSON/YAML, reusable logic, cloud SDKs, and multi-step workflows.",
        },
        {
            "title": "12. Enterprise Lifecycle, Cutover, Final Evidence Package, And Readiness Exam",
            "why": "Real enterprise work includes onboarding new applications, retiring old ones, migrating platforms, cutting over traffic, validating recovery, cleaning up access/resources, handing support to the right team, and proving readiness before role/domain training.",
            "concepts": ["application sunrise", "application sunset", "application modernization", "legacy migration", "cloud migration", "re-platforming", "data center exit", "environment promotion", "release cutover", "blue-green deployment", "canary rollout", "rollback / roll-forward", "disaster recovery drill", "backup and restore validation", "certificate rotation", "secrets rotation", "IAM access review", "firewall / network rule change", "DNS cutover", "API gateway migration", "database migration", "schema migration", "data archival and retention", "observability onboarding", "alert tuning / noise reduction", "runbook creation", "incident simulation", "postmortem / RCA", "SLO / SLA definition", "capacity planning", "cost optimization", "compliance audit evidence", "vendor integration onboarding", "third-party API failure handling", "CMDB / ServiceNow ownership update", "feature flag rollout", "tenant onboarding", "multi-region readiness", "performance testing", "production readiness review", "change management / CAB approval", "operational handoff", "knowledge transfer", "support model transition", "role boundary", "domain vocabulary", "evidence package", "mock interview", "submission readiness"],
            "commands": [
                "Create an application inventory row: app, owner, business criticality, environment, dependencies, support group",
                "Draw a sunrise checklist: owner, DNS/API route, IAM, secrets, monitoring, backup, runbook, CMDB, support handoff",
                "Draw a sunset checklist: dependency map, traffic drain, archive/retention, alert removal, access cleanup, cost cleanup, signoff",
                "Write a cutover plan: blue-green/canary, feature flag, CAB approval, rollback/roll-forward, smoke test, monitoring",
                "Write a migration evidence pack: current/target architecture, backup/restore proof, DR test, performance test, access review",
                "Write an operational transition note: runbook, alert tuning, SLO/SLA, RCA, ServiceNow update, knowledge transfer",
                "Draw the DevOps loop: plan -> code -> commit -> build -> test -> deploy -> run -> monitor",
                "Collect one command output per major tool",
                "Practice one 60-second story and one 5-minute deep dive",
            ],
            "drill": "Pick one old and one new application. Write sunrise steps for the new application, sunset steps for the old application, and a cutover evidence pack that proves traffic, data, access, monitoring, support, rollback, and final readiness are controlled.",
            "interview": "My final answer sounds like this: in this client environment, I understood the system, used the right tools, stayed inside my role boundary, delivered this outcome, validated it this way, and documented this evidence.",
        },
    ]
    for module in modules:
        module.update(_training_basics_module_depth(module))
    return modules


def _training_basics_linux_command_map() -> list[dict[str, Any]]:
    return [
        {
            "group": "File And Directory Navigation",
            "context": "Use these commands to understand where you are, move through folders, create basic files/directories, and locate files during troubleshooting.",
            "commands": [
                {"command": "pwd", "meaning": "Show the current working directory."},
                {"command": "ls", "meaning": "List files and folders in the current directory."},
                {"command": "cd <directory>", "meaning": "Change into another directory."},
                {"command": "mkdir <directory>", "meaning": "Create a new directory."},
                {"command": "rmdir <directory>", "meaning": "Remove an empty directory."},
                {"command": "touch <file>", "meaning": "Create an empty file or update a file timestamp."},
                {"command": "cp <source> <target>", "meaning": "Copy files or directories."},
                {"command": "mv <source> <target>", "meaning": "Move or rename files and directories."},
                {"command": "rm <file>", "meaning": "Delete files; use carefully because Linux does not ask twice in many shells."},
                {"command": "find <path> -name <pattern>", "meaning": "Search for files by name or condition."},
            ],
        },
        {
            "group": "File Viewing And Editing",
            "context": "Use these to inspect application files, logs, configuration, scripts, and command output before changing anything.",
            "commands": [
                {"command": "cat <file>", "meaning": "Display file content."},
                {"command": "less <file>", "meaning": "Read large files page by page."},
                {"command": "head <file>", "meaning": "Show the first lines of a file."},
                {"command": "tail <file>", "meaning": "Show the last lines of a file; `tail -f` follows live logs."},
                {"command": "nano <file>", "meaning": "Open a beginner-friendly terminal editor."},
                {"command": "vim <file>", "meaning": "Open an advanced terminal editor used widely on servers."},
                {"command": "echo <text>", "meaning": "Print text or variable output."},
                {"command": "grep <pattern> <file>", "meaning": "Search inside files for matching text."},
                {"command": "sort <file>", "meaning": "Sort lines of text."},
                {"command": "wc <file>", "meaning": "Count lines, words, and bytes."},
            ],
        },
        {
            "group": "System Monitoring",
            "context": "Use these when a server is slow, full, overloaded, recently restarted, or running the wrong process.",
            "commands": [
                {"command": "top", "meaning": "Monitor CPU, memory, and active processes."},
                {"command": "htop", "meaning": "Use an interactive system monitor when installed."},
                {"command": "free -h", "meaning": "Check memory usage in human-readable units."},
                {"command": "df -h", "meaning": "Check filesystem disk usage."},
                {"command": "du -sh <path>", "meaning": "Check the size of a folder or file path."},
                {"command": "uptime", "meaning": "Show how long the system has been running and its load average."},
                {"command": "uname -a", "meaning": "Show kernel and system information."},
                {"command": "hostname", "meaning": "Show the server hostname."},
                {"command": "whoami", "meaning": "Show the current logged-in user."},
                {"command": "ps aux", "meaning": "List running processes with owner and resource details."},
            ],
        },
        {
            "group": "Networking Commands",
            "context": "Use these to prove whether a hostname resolves, a server is reachable, a port is listening, an API responds, or a remote copy/login path works.",
            "commands": [
                {"command": "ping <host>", "meaning": "Test basic network connectivity."},
                {"command": "ip a", "meaning": "Show IP addresses and network interfaces."},
                {"command": "netstat", "meaning": "Show network statistics and connections when available; many newer systems prefer `ss`."},
                {"command": "ss -tulpn", "meaning": "Check listening ports and associated processes."},
                {"command": "curl <url>", "meaning": "Test APIs, websites, health endpoints, and response headers."},
                {"command": "wget <url>", "meaning": "Download files from a URL."},
                {"command": "ssh user@host", "meaning": "Access a remote server securely."},
                {"command": "scp <file> user@host:/path", "meaning": "Securely copy files to or from a remote server."},
                {"command": "traceroute <host>", "meaning": "Trace the network path to a host when the tool is installed."},
                {"command": "nslookup <host>", "meaning": "Look up DNS records for a hostname."},
            ],
        },
        {
            "group": "User And Permission Management",
            "context": "Use these when a script cannot execute, a service cannot read a file, or a user/service account has the wrong ownership or group membership.",
            "commands": [
                {"command": "sudo <command>", "meaning": "Run a command with administrator privileges."},
                {"command": "chmod <mode> <file>", "meaning": "Change file permissions."},
                {"command": "chown <user>:<group> <file>", "meaning": "Change file ownership."},
                {"command": "passwd", "meaning": "Change a user password."},
                {"command": "useradd <user>", "meaning": "Create a new user."},
                {"command": "usermod <options> <user>", "meaning": "Modify an existing user."},
                {"command": "groups <user>", "meaning": "Show the groups a user belongs to."},
                {"command": "id <user>", "meaning": "Show user and group IDs."},
            ],
        },
        {
            "group": "Package Management And Services",
            "context": "Use these to install tools, manage services, and read service logs on common Linux distributions.",
            "commands": [
                {"command": "apt <subcommand>", "meaning": "Use the Ubuntu/Debian package manager."},
                {"command": "dnf <subcommand>", "meaning": "Use the Fedora/RHEL-family package manager."},
                {"command": "systemctl <subcommand> <service>", "meaning": "Start, stop, restart, enable, disable, or inspect services."},
                {"command": "journalctl -u <service>", "meaning": "View systemd service logs."},
            ],
        },
    ]


def _training_basics_git_command_map() -> list[dict[str, Any]]:
    return [
        {
            "group": "Start A Working Area",
            "context": "Use these when beginning work from an existing repository or creating a fresh repository for a lab/project.",
            "commands": [
                {"command": "git clone <repo-url>", "meaning": "Clone a repository into a new directory."},
                {"command": "git init", "meaning": "Create an empty Git repository or reinitialize an existing one."},
            ],
        },
        {
            "group": "Work On The Current Change",
            "context": "Use these while editing files before the change is committed.",
            "commands": [
                {"command": "git add <file>", "meaning": "Add file contents to the index so they are staged for commit."},
                {"command": "git mv <old> <new>", "meaning": "Move or rename a file, directory, or symlink."},
                {"command": "git restore <file>", "meaning": "Restore working tree files when a local edit should be discarded."},
                {"command": "git rm <file>", "meaning": "Remove files from the working tree and from the index."},
            ],
        },
        {
            "group": "Examine History And State",
            "context": "Use these to understand what changed, where a bug appeared, and what the repository currently contains.",
            "commands": [
                {"command": "git bisect", "meaning": "Use binary search to find the commit that introduced a bug."},
                {"command": "git diff", "meaning": "Show changes between commits, or between the working tree and the index."},
                {"command": "git grep <pattern>", "meaning": "Print tracked lines matching a pattern."},
                {"command": "git log", "meaning": "Show commit history."},
                {"command": "git show <object>", "meaning": "Show commits, tags, trees, blobs, or other objects."},
                {"command": "git status", "meaning": "Show working tree status and staged/unstaged changes."},
            ],
        },
        {
            "group": "Grow, Mark, And Tweak Common History",
            "context": "Use these when managing branches, commits, release markers, or history cleanup in a controlled workflow.",
            "commands": [
                {"command": "git branch", "meaning": "List, create, or delete branches."},
                {"command": "git commit", "meaning": "Record staged changes to repository history."},
                {"command": "git merge <branch>", "meaning": "Join two or more development histories together."},
                {"command": "git rebase <base>", "meaning": "Reapply commits on top of another base tip."},
                {"command": "git reset <target>", "meaning": "Reset current HEAD to the specified state; use carefully because it can rewrite local state."},
                {"command": "git switch <branch>", "meaning": "Switch branches."},
                {"command": "git tag <name>", "meaning": "Create, list, delete, or verify a tag object, often used as a release marker."},
            ],
        },
        {
            "group": "Collaborate",
            "context": "Use these when exchanging commits and branch updates with a remote repository.",
            "commands": [
                {"command": "git fetch", "meaning": "Download objects and refs from another repository without integrating them."},
                {"command": "git pull", "meaning": "Fetch from and integrate with another repository or local branch."},
                {"command": "git push", "meaning": "Update remote refs and upload associated objects."},
            ],
        },
    ]


def _training_basics_docker_command_map() -> list[dict[str, Any]]:
    return [
        {
            "group": "Common Container Workflow",
            "context": "Use these commands for the everyday Docker path: build an image, run it, inspect it, authenticate to a registry, and move images between local and remote repositories.",
            "commands": [
                {"command": "docker run <image>", "meaning": "Create and run a new container from an image."},
                {"command": "docker exec -it <container> sh", "meaning": "Execute a command inside a running container for inspection or debugging."},
                {"command": "docker ps", "meaning": "List running containers; add `-a` to include stopped containers."},
                {"command": "docker build -t <name>:<tag> .", "meaning": "Build an image from a Dockerfile."},
                {"command": "docker pull <image>", "meaning": "Download an image from a registry."},
                {"command": "docker push <image>", "meaning": "Upload an image to a registry."},
                {"command": "docker images", "meaning": "List local images."},
                {"command": "docker login", "meaning": "Authenticate to a container registry."},
                {"command": "docker logout", "meaning": "Log out from a container registry."},
                {"command": "docker search <term>", "meaning": "Search Docker Hub for images."},
                {"command": "docker version", "meaning": "Show Docker client/server version information."},
                {"command": "docker info", "meaning": "Display system-wide Docker information."},
            ],
        },
        {
            "group": "Management Areas",
            "context": "Use these command families when the issue is not just one container. They group Docker by builds, Compose projects, containers, contexts, images, networks, plugins, system cleanup, and volumes.",
            "commands": [
                {"command": "docker builder", "meaning": "Manage build cache and builder settings."},
                {"command": "docker buildx", "meaning": "Use Docker Buildx for advanced and multi-platform builds."},
                {"command": "docker compose", "meaning": "Manage multi-container applications defined in Compose files."},
                {"command": "docker container", "meaning": "Manage containers with explicit subcommands."},
                {"command": "docker context", "meaning": "Manage Docker contexts for different daemon targets."},
                {"command": "docker image", "meaning": "Manage images with explicit subcommands."},
                {"command": "docker manifest", "meaning": "Manage image manifests and manifest lists."},
                {"command": "docker network", "meaning": "Manage Docker networks."},
                {"command": "docker plugin", "meaning": "Manage Docker plugins."},
                {"command": "docker system", "meaning": "Inspect or clean Docker disk/resource usage."},
                {"command": "docker volume", "meaning": "Manage persistent Docker volumes."},
            ],
        },
        {
            "group": "Container Lifecycle And Debugging",
            "context": "Use these when a container exists and the work is to inspect, troubleshoot, restart, stop, copy files, or understand runtime behavior.",
            "commands": [
                {"command": "docker attach <container>", "meaning": "Attach local input/output/error streams to a running container."},
                {"command": "docker commit <container> <image>", "meaning": "Create a new image from a container's changes."},
                {"command": "docker cp <container>:/path ./local", "meaning": "Copy files or folders between a container and the local filesystem."},
                {"command": "docker create <image>", "meaning": "Create a container without starting it."},
                {"command": "docker diff <container>", "meaning": "Inspect filesystem changes inside a container."},
                {"command": "docker events", "meaning": "Get real-time Docker daemon events."},
                {"command": "docker export <container>", "meaning": "Export a container filesystem as a tar archive."},
                {"command": "docker history <image>", "meaning": "Show image layer history."},
                {"command": "docker inspect <object>", "meaning": "Return low-level JSON details for Docker objects."},
                {"command": "docker kill <container>", "meaning": "Force kill one or more running containers."},
                {"command": "docker logs <container>", "meaning": "Fetch container logs."},
                {"command": "docker pause <container>", "meaning": "Pause all processes in a container."},
                {"command": "docker port <container>", "meaning": "List port mappings for a container."},
                {"command": "docker restart <container>", "meaning": "Restart one or more containers."},
                {"command": "docker rm <container>", "meaning": "Remove one or more containers."},
                {"command": "docker rmi <image>", "meaning": "Remove one or more images."},
                {"command": "docker start <container>", "meaning": "Start a stopped container."},
                {"command": "docker stats", "meaning": "Display live CPU, memory, network, and block I/O usage."},
                {"command": "docker stop <container>", "meaning": "Stop one or more running containers gracefully."},
                {"command": "docker tag <source> <target>", "meaning": "Create a tag that refers to a source image."},
                {"command": "docker top <container>", "meaning": "Display running processes inside a container."},
                {"command": "docker unpause <container>", "meaning": "Unpause paused container processes."},
                {"command": "docker update <container>", "meaning": "Update container resource configuration."},
                {"command": "docker wait <container>", "meaning": "Block until containers stop and print exit codes."},
            ],
        },
        {
            "group": "Images And Archives",
            "context": "Use these for moving images or filesystems between machines, backup locations, or offline environments.",
            "commands": [
                {"command": "docker save <image> > image.tar", "meaning": "Save one or more images to a tar archive."},
                {"command": "docker load < image.tar", "meaning": "Load an image from a tar archive or STDIN."},
                {"command": "docker import filesystem.tar <image>", "meaning": "Import a tarball as a filesystem image."},
            ],
        },
        {
            "group": "Global Options And Help",
            "context": "Use these before troubleshooting deeper issues, especially when the client, daemon, context, host, or TLS settings might be wrong.",
            "commands": [
                {"command": "docker --help", "meaning": "Show Docker help. `docker -h` may work but is deprecated in favor of `--help`."},
                {"command": "docker <command> --help", "meaning": "Show detailed help for a specific Docker command."},
                {"command": "docker --config <path>", "meaning": "Use a custom Docker client config directory."},
                {"command": "docker --context <name>", "meaning": "Use a named Docker context."},
                {"command": "docker --debug", "meaning": "Enable debug mode."},
                {"command": "docker --host <socket-or-url>", "meaning": "Connect to a specific Docker daemon socket or host."},
                {"command": "docker --log-level debug", "meaning": "Set Docker client log level."},
                {"command": "docker --tlsverify", "meaning": "Use TLS and verify the remote daemon."},
                {"command": "docker --version", "meaning": "Print Docker version information and quit."},
            ],
        },
    ]


def _training_basics_kubernetes_command_map() -> list[dict[str, Any]]:
    return [
        {
            "group": "Cluster And Context",
            "context": "Use these first to prove which cluster kubectl is talking to before reading or changing resources.",
            "commands": [
                {"command": "kubectl version", "meaning": "Display Kubernetes client and server version information."},
                {"command": "kubectl cluster-info", "meaning": "Show basic information about the Kubernetes control plane and core services."},
                {"command": "kubectl config current-context", "meaning": "Display the currently active Kubernetes context used by kubectl."},
                {"command": "kubectl config get-contexts", "meaning": "List all Kubernetes contexts configured on the workstation."},
                {"command": "kubectl get namespaces", "meaning": "Display all namespaces available in the cluster."},
            ],
        },
        {
            "group": "Manifests And Desired State",
            "context": "Use these when a YAML manifest defines the desired state for pods, deployments, services, ingress, ConfigMaps, Secrets, or storage claims.",
            "commands": [
                {"command": "kubectl apply -f <file_name>", "meaning": "Create or update Kubernetes resources from a YAML file."},
                {"command": "kubectl delete -f <file_name>", "meaning": "Delete Kubernetes resources defined in a YAML file."},
                {"command": "kubectl get all -n <namespace>", "meaning": "Show a summary of major resources such as pods, services, and deployments in a namespace."},
            ],
        },
        {
            "group": "Workloads And Pods",
            "context": "Use these to see what is running and to read the controllers that keep the desired number of pods alive.",
            "commands": [
                {"command": "kubectl get deployments -n <namespace>", "meaning": "List deployments inside a specific namespace."},
                {"command": "kubectl get pods -n <namespace>", "meaning": "Display pods running inside a specific namespace."},
                {"command": "kubectl get pods -n <namespace> -w", "meaning": "Watch pod status changes live."},
                {"command": "kubectl get rs -n <namespace>", "meaning": "Display ReplicaSets in a namespace."},
                {"command": "kubectl describe pod <pod_name> -n <namespace>", "meaning": "Show pod details including events, errors, container state, scheduling, and restarts."},
                {"command": "kubectl describe deployment <deployment_name> -n <namespace>", "meaning": "Show deployment details including rollout state, replicas, and selectors."},
            ],
        },
        {
            "group": "Logs, Shell Access, And Troubleshooting",
            "context": "Use these when the pod exists but the application is failing, restarting, returning errors, or needs runtime inspection.",
            "commands": [
                {"command": "kubectl logs <pod_name> -n <namespace>", "meaning": "Display logs generated by a running pod."},
                {"command": "kubectl logs <pod_name> -c <container_name> -n <namespace>", "meaning": "Display logs for a specific container inside a multi-container pod."},
                {"command": "kubectl exec -it <pod_name> -n <namespace> -- bash", "meaning": "Open an interactive shell inside a running pod when bash exists."},
                {"command": "kubectl exec -it <pod_name> -n <namespace> -- sh", "meaning": "Open an interactive shell inside a running pod when only sh exists."},
                {"command": "kubectl get events -n <namespace> --sort-by=.lastTimestamp", "meaning": "Read recent Kubernetes events in time order for scheduling, image, probe, and mount failures."},
            ],
        },
        {
            "group": "Scaling, Rollouts, And Image Updates",
            "context": "Use these to explain production changes: scale capacity, restart a deployment, watch rollout progress, update an image, or recover from a bad release.",
            "commands": [
                {"command": "kubectl scale deployment <deployment_name> --replicas=<number> -n <namespace>", "meaning": "Scale a deployment up or down by changing pod replica count."},
                {"command": "kubectl rollout restart deployment <deployment_name> -n <namespace>", "meaning": "Restart pods in a deployment without deleting the deployment."},
                {"command": "kubectl rollout status deployment <deployment_name> -n <namespace>", "meaning": "Show live progress of a deployment rollout."},
                {"command": "kubectl rollout history deployment <deployment_name> -n <namespace>", "meaning": "Show previous deployment rollout revisions."},
                {"command": "kubectl rollout undo deployment <deployment_name> -n <namespace>", "meaning": "Roll back a deployment to the previous revision."},
                {"command": "kubectl set image deployment/<deployment_name> <container_name>=<image>:<tag> -n <namespace>", "meaning": "Update the container image used by a deployment."},
            ],
        },
        {
            "group": "Networking, Config, Secrets, And Storage",
            "context": "Use these to prove how traffic enters the workload and how configuration, secrets, and persistent storage are attached.",
            "commands": [
                {"command": "kubectl get svc -n <namespace>", "meaning": "Display services inside a namespace."},
                {"command": "kubectl get ingress -n <namespace>", "meaning": "Display ingress resources inside a namespace."},
                {"command": "kubectl get configmap -n <namespace>", "meaning": "Display ConfigMaps in a namespace."},
                {"command": "kubectl get secret -n <namespace>", "meaning": "Display Secrets in a namespace."},
                {"command": "kubectl get pvc -n <namespace>", "meaning": "List PersistentVolumeClaims in a namespace."},
            ],
        },
        {
            "group": "Metrics And Controlled Deletes",
            "context": "Use these carefully when checking capacity or forcing Kubernetes controllers to recreate unhealthy pods.",
            "commands": [
                {"command": "kubectl top pods -n <namespace>", "meaning": "Show CPU and memory usage for pods when metrics server is installed."},
                {"command": "kubectl top nodes", "meaning": "Show CPU and memory usage for Kubernetes nodes."},
                {"command": "kubectl delete pod <pod_name> -n <namespace>", "meaning": "Delete one pod; a Deployment-managed pod is recreated by its controller."},
                {"command": "kubectl delete pods --all -n <namespace>", "meaning": "Delete all pods in a namespace; ReplicaSets recreate managed pods."},
                {"command": "kubectl delete deployment <deployment_name> -n <namespace>", "meaning": "Delete an entire deployment and the pods it manages."},
            ],
        },
        {
            "group": "Core Concepts From The Reference",
            "context": "These terms are the vocabulary a consultant needs before explaining Kubernetes in interviews.",
            "commands": [
                {"command": "Cluster", "meaning": "The full Kubernetes environment: control plane plus worker nodes where applications run."},
                {"command": "Manifest", "meaning": "A YAML file that describes the desired state of a Kubernetes resource."},
                {"command": "Pod", "meaning": "The smallest deployable unit; one or more containers sharing network and storage."},
                {"command": "Deployment", "meaning": "A controller that manages rollout, replicas, and pod replacement for an application."},
                {"command": "ReplicaSet", "meaning": "Keeps a specific number of pod replicas running."},
                {"command": "ConfigMap", "meaning": "Stores non-sensitive configuration such as environment names, regions, flags, and app settings."},
                {"command": "Secret", "meaning": "Stores sensitive values such as passwords, API keys, usernames, and tokens."},
                {"command": "Persistent Volume", "meaning": "Storage that survives pod restart, rescheduling, or container deletion."},
                {"command": "kubectl", "meaning": "The command-line interface used to deploy, inspect, troubleshoot, scale, and manage Kubernetes resources."},
            ],
        },
    ]


def _training_basics_devops_visual_reference() -> dict[str, Any]:
    return {
        "title": "DevOps Visual Reference: Development To Operations Loop",
        "summary": "This image-style reference shows DevOps as one continuous flow: code is built and tested, released through CI/CD, deployed to a production environment, monitored in operations, and improved through feedback.",
        "loop": ["Code", "Build", "Test", "Release", "Deploy", "Operate", "Monitor"],
        "pipeline": ["Source control", "CI", "CD", "Deployment", "Monitoring"],
        "platform": ["Cloud: AWS / Azure / GCP", "Containers: Docker / Kubernetes", "Servers: scalable and reliable", "Database: secure and backed up", "Security: safe and compliant"],
        "image_panels": [
            {"title": "Develop", "caption": "Code, build, test, and review application changes before release."},
            {"title": "Release", "caption": "Move validated artifacts through CI/CD with approvals and rollback readiness."},
            {"title": "Operate", "caption": "Run, monitor, secure, recover, and improve production systems."},
        ],
        "notes": [
            "DevOps bridges development and operations. Development builds reliable applications; operations keeps systems running smoothly.",
            "CI means every code change is integrated and validated with build, test, scan, and artifact steps.",
            "CD means the validated artifact can be promoted through environments with approvals, deployment evidence, and rollback readiness.",
            "Monitoring is not only dashboards. It includes logs, metrics, alerts, health checks, incidents, and proof that the system is stable after release.",
            "Cloud, containers, servers, databases, and security are the production environment. The learner needs to know where each one fits before learning role-specific depth.",
            "The business value is faster delivery, higher reliability, better collaboration, and lower cost when automation and evidence are used correctly.",
        ],
        "interview_notes": [
            "When explaining DevOps, start with the flow: source control, CI, artifact, CD, deployment, monitoring, and feedback.",
            "When asked about production issues, explain how you check recent releases, container health, logs, metrics, database connectivity, secrets, permissions, and cloud resource status.",
            "DevOps is broader than Docker and Kubernetes: it covers safe delivery, reliable operations, observability, security, and recovery.",
            "The visual acts as a memory map for DevOps, Cloud Platform, SRE/AIOps, Data Platform, and MLOps role paths.",
        ],
    }


def _training_cicd_security_pipeline_reference() -> dict[str, Any]:
    return {
        "title": "CI/CD Security Pipeline Visual: Jenkins, OWASP, SonarQube, Trivy, Docker, Argo CD, Kubernetes",
        "imageUrl": "/static/training/devops-cicd-security-pipeline.gif",
        "whereItFits": "This fits Basics CI/CD and DevOps context by showing how code moves from GitHub through CI validation, dependency checks, quality gates, image scanning, Docker build/push, GitOps deployment, Kubernetes rollout, monitoring, and notification.",
        "flow": [
            "Developer pushes code to GitHub",
            "Jenkins CI pulls code and runs dependency/security checks",
            "OWASP dependency check and SonarQube quality gate validate risk",
            "Trivy scans filesystem/container risk before image promotion",
            "Docker image is built and pushed",
            "Jenkins CD updates image version in GitHub",
            "Argo CD pulls the desired state and deploys to Kubernetes",
            "Prometheus and Grafana monitor the running workload",
            "Email notification closes the feedback loop",
        ],
        "interviewNotes": [
            "Explain CI as validation before deployment: dependency check, code quality, scan, build, and artifact/image creation.",
            "Explain CD as controlled promotion: update image version, GitOps sync, Kubernetes deploy, monitoring, and notification.",
            "Mention evidence: Jenkins logs, SonarQube report, OWASP report, Trivy scan result, Docker image tag, Argo sync status, Kubernetes rollout, Grafana/Prometheus dashboard, and email notification.",
        ],
    }


def _training_basics_course_overview() -> dict[str, Any]:
    return {
        "title": "DevOps For Beginners Course Alignment",
        "courseTitle": "DevOps for beginners: Docker, K8s, AWS & Azure + 4 Projects",
        "summary": "The Basics Prep path starts with this beginner-friendly DevOps course and uses Mintel to convert the learning into project evidence, diagrams, troubleshooting notes, and screening answers.",
        "projects": [
            {"name": "Book Review App", "purpose": "End-to-end deployment story with application, infrastructure, and release flow."},
            {"name": "The Epic Book", "purpose": "Real application practice focused on CI/CD, automation, and deployment confidence."},
            {"name": "My React App", "purpose": "Frontend deployment practice through cloud VM, Nginx, storage, pipeline, and container paths."},
            {"name": "Personal Portfolio Template", "purpose": "Portfolio asset for demonstrating hands-on work and GitHub-ready evidence."},
        ],
        "learning": [
            "42 hands-on assignments tied to Linux, Git, Agile/Jira, AWS, Azure, Terraform, Ansible, Azure DevOps, Docker, and Kubernetes.",
            "Practical use of Docker, Kubernetes, AWS, and Azure through beginner projects.",
            "No prior DevOps or programming experience is required; Linux, networking, and cloud basics are introduced before role/domain training.",
            "Mintel adds interview framing: project explanation, architecture sketch, failure story, command proof, and evidence package.",
        ],
        "audience": [
            "Beginners entering DevOps, Cloud Platform, SRE/AIOps, Data Platform, or MLOps preparation.",
            "Software, IT, cloud, and system engineers who need practical project language before marketing-role training.",
            "Consultants who need to explain what they implemented, how it worked, what failed, and what evidence proved the result.",
        ],
    }


def _training_basics_14_day_plan() -> list[dict[str, Any]]:
    rows = [
        {
            "focus": "Terminal, Linux, Files, And Troubleshooting",
            "learn": "Folders, files, permissions, processes, ports, DNS, HTTP status, and service-down triage.",
            "scenario": "A web service is reported down. The learner checks whether the process is running, port is listening, DNS resolves, and the health endpoint responds.",
            "practice": ["Navigate and inspect files with `pwd`, `ls`, `cat`, `tail`, and `find`.", "Check process and port state with `ps`, `lsof`, `netstat`, or `ss`.", "Use `curl`, `ping`, `dig`, and `nslookup` to separate network, DNS, and application symptoms.", "Write a short note with symptom, command, output, meaning, and next owner route."],
            "commands": ["pwd", "ls -la", "tail -n 50 app.log", "ps aux | grep app", "lsof -i :8080", "curl -i http://localhost:8080/health"],
            "output": "Terminal evidence note with service-down triage.",
            "readiness": "Can explain the first 10 minutes of troubleshooting without guessing.",
        },
        {
            "focus": "Git, Branches, Pull Requests, And Release Evidence",
            "learn": "Branching, commits, diffs, pull requests, merge history, rollback thinking, and release traceability.",
            "scenario": "A release introduced a bad configuration. The learner finds the change, explains the diff, and writes the rollback note.",
            "practice": ["Create a branch and make a small controlled change.", "Inspect changes with `git status`, `git diff`, and `git log`.", "Write a PR summary with what changed, why, validation, and rollback.", "Explain how a commit, tag, or reverted change becomes release evidence."],
            "commands": ["git status", "git checkout -b basics/change-note", "git diff", "git add .", "git commit -m \"Add basics note\"", "git log --oneline -5"],
            "output": "PR summary, diff explanation, and rollback note.",
            "readiness": "Can describe exactly what changed and how the change would be reviewed.",
        },
        {
            "focus": "Docker Images, Containers, Logs, And Debugging",
            "learn": "Image versus container, Dockerfile, registry, environment variables, port mapping, logs, and container failure signals.",
            "scenario": "A container starts locally but the application is unreachable. The learner checks image tag, port mapping, environment variables, and logs.",
            "practice": ["Build and run a simple container.", "Map a host port to a container port and verify with `curl`.", "Break one environment variable and inspect the failure log.", "Explain image, container, registry, tag, and runtime config."],
            "commands": ["docker build -t basics-app:local .", "docker run --rm -p 8080:8080 basics-app:local", "docker ps", "docker logs <container>", "docker inspect <container>", "docker images"],
            "output": "Container troubleshooting note with image, run command, logs, and fix.",
            "readiness": "Can explain why a container is running but the app is still failing.",
        },
        {
            "focus": "Kubernetes Core Objects",
            "learn": "Pods, deployments, services, namespaces, config, secrets, events, logs, rollout status, and CrashLoopBackOff.",
            "scenario": "A rollout finishes but pods keep restarting. The learner reads events, logs, probes, image, config, and rollout history.",
            "practice": ["Read deployment, pod, service, events, and logs.", "Compare desired replicas, ready replicas, image tag, and restart count.", "Use rollout status/history to explain release state.", "Write a CrashLoopBackOff triage note."],
            "commands": ["kubectl get deploy,pod,svc -n app", "kubectl describe pod <pod> -n app", "kubectl logs <pod> -n app", "kubectl get events -n app --sort-by=.lastTimestamp", "kubectl rollout status deploy/app -n app", "kubectl rollout history deploy/app -n app"],
            "output": "Kubernetes flowchart and failed-rollout note.",
            "readiness": "Can separate image, config, probe, resource, and dependency failures.",
        },
        {
            "focus": "Cloud Foundation For Project Interviews",
            "learn": "Account/subscription/project, region, availability zone, IAM, CLI, object storage, VM/compute, budget guardrails, and basic network vocabulary.",
            "scenario": "A simple website or application needs to run in cloud safely. The learner explains account setup, MFA, budget alert, IAM access, storage, compute, and the first network boundary.",
            "practice": ["Explain cloud account/project structure and why MFA/budget alerts matter.", "Map IAM user/group/role/policy to safe access.", "Host or inspect a simple static website/storage project.", "Draw where compute, storage, network, identity, and cost guardrails sit in the project."],
            "commands": ["aws sts get-caller-identity", "aws s3 ls", "az account show", "az storage account list", "gcloud config list", "gcloud compute networks list"],
            "output": "Cloud foundation sketch with account, identity, storage, compute, network, and cost controls.",
            "readiness": "Can answer cloud recruiter questions without jumping into advanced architecture too early.",
        },
        {
            "focus": "CI/CD, Artifacts, Release, Rollback, And Checkpoint Test",
            "learn": "Commit, build, test, scan, artifact/image, registry, deployment, approval, health check, monitoring, and rollback.",
            "scenario": "A pipeline failed after tests passed but before deployment. The learner identifies the failed stage, explains what evidence proves it, and completes a larger checkpoint test covering Days 1-6.",
            "practice": ["Trace commit to build run, test result, scan, artifact, deploy, health check, and rollback.", "Read a pipeline log and name the failed stage.", "Explain artifact/image tag and registry location.", "Write a failed-pipeline explanation with next action.", "Complete a 30-question checkpoint test covering terminal/Linux, Git, Docker, Kubernetes, cloud, and CI/CD."],
            "commands": ["gh run list", "gh run view <run-id> --log", "docker build -t app:${GIT_SHA} .", "docker push <registry>/app:${GIT_SHA}", "kubectl rollout status deploy/app -n app", "kubectl rollout undo deploy/app -n app"],
            "output": "Release evidence chain with rollback trigger and checkpoint score review.",
            "readiness": "Can explain CI/CD as evidence flow and identify weak areas from the first larger test.",
        },
        {
            "focus": "Observability, Logs, Metrics, Traces, Alerts",
            "learn": "Golden signals, logs, metrics, traces, dashboards, alerts, SLO/SLA, incident timeline, and runbook action.",
            "scenario": "Users report slowness but there is no obvious error. The learner checks latency, traffic, errors, saturation, recent change, and dependency health.",
            "practice": ["Use a health endpoint and log query.", "Read one metric and identify whether it is latency, traffic, error, or saturation.", "Explain how trace spans identify a slow dependency.", "Write an incident timeline with suspected layer and recovery validation."],
            "commands": ["curl -w '%{http_code} %{time_total}\\n' -o /dev/null -s https://example.com/health", "kubectl logs deploy/app -n app --since=30m", "aws logs tail /aws/lambda/app --follow", "az monitor metrics list --resource <resource-id>", "gcloud logging read 'severity>=ERROR' --limit=20"],
            "output": "Incident timeline note with before/after signal.",
            "readiness": "Can convert telemetry into incident action and owner routing.",
        },
        {
            "focus": "Terraform, Infrastructure As Code, And Change Safety",
            "learn": "Desired state, state file, plan, apply, modules, variables, drift, policy, reference architecture, and rollback limits.",
            "scenario": "A Terraform plan wants to destroy a shared resource. The learner reviews risk before apply and explains the decision.",
            "practice": ["Run format, validate, and plan checks.", "Classify create, update, and destroy actions.", "Explain state, drift, variables, and outputs.", "Write a change review note with risk and approval needs."],
            "commands": ["terraform init", "terraform fmt", "terraform validate", "terraform plan", "terraform output", "terraform state list"],
            "output": "IaC change review note.",
            "readiness": "Can read plan output and explain production risk before a change.",
        },
        {
            "focus": "Security, Secrets, IAM, Audit",
            "learn": "Least privilege, role, policy, service account, secret manager, key vault, audit log, rotation, scan, and compliance evidence.",
            "scenario": "A deployment fails because a service account cannot access a secret. The learner checks IAM/RBAC, secret reference, and audit logs.",
            "practice": ["Map identity, permission, secret, and approval.", "Explain why secrets do not belong in code, logs, screenshots, or tickets.", "Read one IAM/RBAC or key vault command output.", "Write an access-denied evidence note."],
            "commands": ["aws iam get-user", "aws iam list-attached-role-policies --role-name <role>", "aws secretsmanager list-secrets", "az role assignment list --assignee <principal>", "az keyvault secret list --vault-name <vault>", "gcloud secrets list", "kubectl get secrets -n app"],
            "output": "Access/security evidence note.",
            "readiness": "Can explain access failure without exposing sensitive values.",
        },
        {
            "focus": "Agile, Jira, APIs, JSON/YAML, SQL, Cost, And Evidence",
            "learn": "Sprint delivery, stories, acceptance criteria, handoff, HTTP methods/status codes, JSON/YAML config, SQL row checks, cost tags, and evidence templates.",
            "scenario": "A vague ticket says 'fix deployment issue' and an API/data/config signal is unclear. The learner rewrites the work into a clear story with acceptance criteria, validation checks, cost/risk note, and owner route.",
            "practice": ["Write one user story using As a / I want / So that.", "Create acceptance criteria and subtasks for design, build, test, deploy, and support.", "Check an API response and status code.", "Read JSON/YAML config safely.", "Run row-count and null-check SQL examples.", "Convert the technical work into sprint evidence."],
            "commands": ["Story: As a support engineer, I want deployment status visible, so that failed releases route correctly", "curl -i https://example.com/health", "jq . sample.json", "yq . sample.yaml", "psql -c \"select count(*) from sample_table;\"", "Handoff: ticket, change, validation, screenshot/log, next owner"],
            "output": "Jira story package with API/config/SQL validation and cost or risk evidence.",
            "readiness": "Can turn technical checks into professional sprint communication and evidence.",
        },
        {
            "focus": "Ansible, Shell And Python Automation, And Runbooks",
            "learn": "Ansible inventory, SSH access, ad-hoc commands, playbooks, roles, Shell scripts, Python API/JSON automation, health checks, backups, log cleanup, image scans, scheduled jobs, and cloud SDKs.",
            "scenario": "A team asks for a small automation path to check remote host health, verify backup output, call an API, and summarize failures.",
            "practice": ["Explain Ansible inventory, SSH access, ad-hoc commands, playbooks, and roles.", "Compare Shell for quick commands versus Python for APIs and structured data.", "Explain a health-check script and expected output.", "Explain backup verification and log rotation basics.", "Create an automation matrix with healthy output, failed output, and next owner."],
            "commands": ["ansible all -i inventory.ini -m ping", "ansible all -i inventory.ini -a \"uptime\"", "ansible-playbook -i inventory.ini playbook.yml", "bash scripts/check_health.sh", "bash scripts/backup_mysql.sh", "trivy image repo/app:latest", "python3 scripts/call_api.py"],
            "output": "Automation example matrix.",
            "readiness": "Can explain Ansible, Shell, and Python automation without pretending to own a full platform.",
        },
        {
            "focus": "Enterprise Lifecycle, Cutover, Final Evidence Package, And Final Exam",
            "learn": "Application onboarding, retirement, modernization, migration, DNS/API cutover, backup/restore, DR drill, access cleanup, CMDB/ServiceNow, support transition, and final role-readiness evidence.",
            "scenario": "A legacy application is being replaced. The learner maps sunrise for the new app, sunset for the old app, cutover controls, rollback, support transition, and completes the final readiness exam.",
            "practice": ["Create sunrise and sunset checklists.", "Write a cutover plan with blue-green/canary, CAB approval, rollback, and smoke test.", "Map backup/restore, data retention, monitoring, and access cleanup.", "Prepare one architecture diagram, one workflow diagram, and one runbook.", "Complete a 45-question final readiness exam covering Days 1-11."],
            "commands": ["Application inventory: app, owner, criticality, environments, dependencies, support group", "Sunrise: DNS/API route, IAM, secrets, monitoring, backup, runbook", "Sunset: traffic drain, archive, alert removal, access cleanup, cost cleanup", "Cutover: approval, smoke test, rollback, dashboard, incident contact", "Artifact set: diagram, command output, log/dashboard, runbook, ticket note"],
            "output": "Lifecycle map, cutover readiness map, basics completion package, and final exam review.",
            "readiness": "Ready to enter role/domain training without basics being re-taught.",
        },
    ]
    course_alignment = [
        {
            "courseSections": ["Introduction", "Internet and Networking", "App Architecture & Stack", "Domain & DNS", "Linux OS introduction", "file navigation", "permissions", "processes", "networking commands", "Nginx service checks"],
            "dailyPlan": ["Course study: internet, DNS, HTTP, and application stack", "Course study: Linux files, permissions, processes, and networking", "Lab: inspect a simple Nginx or app service with file/process/port/log evidence", "Review: DNS versus network versus application failure", "Evidence and rehearsal: service-down screening answer"],
            "labFocus": "Explain how browser, DNS, HTTP, server, process, port, file permissions, and logs connect when a website is unavailable.",
        },
        {
            "courseSections": ["Git introduction", "repository", "status/add/commit/log", "branches", "merge", "GitHub", "pull request", "Git interview simulation"],
            "dailyPlan": ["Course study: Git and GitHub workflow", "Lab: create branch, change, commit, and diff", "Lab: write PR summary and rollback note", "Review: release traceability from commit to deployment", "Evidence and rehearsal: Git screening questions"],
            "labFocus": "Create a visible code change, trace it through branch, commit, PR, merge, release note, and rollback explanation.",
        },
        {
            "courseSections": ["Docker problem statement", "container architecture", "Dockerfile", "multi-stage builds", "Docker networking", "storage", "Compose", "Dockerized capstone"],
            "dailyPlan": ["Course study: Docker image/container/runtime model", "Lab: build and run a container with port mapping", "Lab: inspect logs, environment values, and container metadata", "Review: volume, network, registry, and restart failure patterns", "Evidence and rehearsal: container troubleshooting answer"],
            "labFocus": "Explain image, container, registry, port mapping, environment variables, volumes, Compose, logs, and container restart failures.",
        },
        {
            "courseSections": ["Kubernetes limitations of Docker", "Kubernetes architecture", "kubectl/minikube/kind", "pods", "replicasets", "deployments", "HPA", "readiness/liveness probes", "services"],
            "dailyPlan": ["Course study: Kubernetes architecture and core objects", "Lab: inspect pods, deployments, services, events, and logs", "Lab: read rollout status and failure signals", "Review: CrashLoopBackOff, ImagePullBackOff, probe, config, and secret failures", "Evidence and rehearsal: Kubernetes screening answer"],
            "labFocus": "Explain a Kubernetes workload from deployment to pod to service to probe to rollout status, with CrashLoopBackOff evidence.",
        },
        {
            "courseSections": ["Cloud computing", "AWS account/free tier", "MFA and budget alerts", "AWS CLI", "regions and availability zones", "IAM", "S3 static website"],
            "dailyPlan": ["Course study: cloud meaning, account setup, and provider basics", "Hands-on: IAM, CLI, and storage website path", "Project note: account, region, IAM, storage, compute, and network boundary", "Troubleshooting: access denied, wrong region, missing object, or budget risk", "Evidence: cloud foundation sketch and short screening answers"],
            "labFocus": "Use a simple storage/static-site or VM example to explain account, region, IAM, storage, compute, first network boundary, budget alert, and proof evidence.",
        },
        {
            "courseSections": ["Azure DevOps", "self-hosted Linux agent", "Azure pipelines", "continuous delivery", "React deployment", "dual pipelines capstone", "CI/CD concepts", "checkpoint test covering Days 1-6"],
            "dailyPlan": ["Course study: CI/CD and Azure DevOps pipeline flow", "Lab: map source, build agent, build, test, artifact, and deploy stages", "Lab: read failed pipeline output and rollout status", "Review: approval, image tag, smoke test, monitoring, and rollback", "Assessment: 30-question checkpoint test across Days 1-6", "Evidence and rehearsal: release confidence answer"],
            "labFocus": "Trace one release through source, build agent, build, artifact, deployment, validation, rollback, and notification evidence, then review weak areas from the checkpoint test.",
        },
        {
            "courseSections": ["Production maintenance drill", "health endpoint checks", "application logs", "cloud logs", "metrics", "alerts", "runbook evidence"],
            "dailyPlan": ["Study: logs, metrics, traces, alerts, and golden signals", "Lab: compare HTTP health, logs, and metric signal", "Lab: write incident timeline with recent change and user impact", "Review: rollback versus scale versus restart versus escalation", "Evidence and rehearsal: observability answer"],
            "labFocus": "Turn a user symptom into telemetry evidence: latency, traffic, errors, saturation, logs, trace/dependency clue, alert, runbook, and recovery validation.",
        },
        {
            "courseSections": ["Terraform IaC", "first script", "Azure setup", "resources", "state file", "providers", "variables", "modules", "multi-cloud", "debugging"],
            "dailyPlan": ["Course study: Terraform videos", "Course study: init/fmt/validate/plan/apply practice", "Course study: state/variables/modules review", "Review: plan-risk and drift examples", "Evidence and rehearsal: IaC change-review answer practice"],
            "labFocus": "Read a Terraform plan like a production change request: create/update/destroy risk, state, variables, provider, module, and approval evidence.",
        },
        {
            "courseSections": ["IAM", "RBAC", "Secrets Manager / Key Vault", "service accounts", "security scanning", "audit logs", "certificate and secret rotation"],
            "dailyPlan": ["Study: identity, policy, secret storage, and audit evidence", "Lab: explain access denied through identity, role, policy, and secret reference", "Lab: map expired secret, leaked env value, public storage, and certificate expiry scenarios", "Review: least privilege and safe evidence handling", "Evidence and rehearsal: security screening answer"],
            "labFocus": "Explain security through practical failure modes: denied access, expired secret, public storage, weak RBAC, certificate expiry, scan finding, and audit trail.",
        },
        {
            "courseSections": ["SDLC", "Agile", "Scrum", "Jira setup", "backlog refinement", "5-day mini-sprint", "APIs and HTTP status codes", "JSON/YAML configuration", "SQL row-count checks", "data quality basics", "cost and tagging basics", "evidence note format"],
            "dailyPlan": ["Course study: SDLC/Agile/Scrum and Jira basics", "Course study: API request/response, JSON/YAML config, SQL checks, and cost tags", "Lab: write story, acceptance criteria, subtasks, and blocker update", "Lab: validate API/config/SQL evidence for the story", "Review: demo, validation, release note, and support handoff", "Evidence and rehearsal: delivery communication answer"],
            "labFocus": "Convert technical work into sprint evidence: story, acceptance criteria, tasks, blocker, API/config/SQL validation, cost or risk note, demo, release note, and handoff.",
        },
        {
            "courseSections": ["Ansible essentials", "inventory", "SSH access", "ad-hoc commands", "playbooks", "roles", "Shell scripts", "Python API/JSON automation"],
            "dailyPlan": ["Course study: Ansible and automation basics", "Lab: compare Shell for system commands and Python for API/JSON tasks", "Lab: explain health check, backup verification, log cleanup, and image scan scripts", "Review: exit codes, permissions, credentials, and scheduled job failures", "Evidence and rehearsal: automation screening answer"],
            "labFocus": "Explain small automation using input, command/script, output, failure signal, validation, owner handoff, and repeatability.",
        },
        {
            "courseSections": ["Book Review app", "Mini Finance", "EpicBook", "two-tier architecture", "three-tier architecture", "capstone projects across AWS/Azure/Terraform/Ansible/Docker/Kubernetes", "All course quizzes", "knowledge tests", "DevOps screening simulations", "Git interview simulation", "section capstone assignments", "final readiness exam"],
            "dailyPlan": ["Course study: capstone review and architecture diagram rebuild", "Course study: deployment, lifecycle, cutover, and support story writing", "Review: backup/DR/cutover mapping", "Course study: quiz and weak-area review", "Assessment: 45-question final readiness exam across Days 1-11", "Evidence and rehearsal: final mock and evidence review"],
            "labFocus": "Convert course projects into one enterprise-style project story with architecture, release flow, support flow, failure handling, evidence, and final readiness review.",
        },
    ]
    for index, row in enumerate(rows, start=1):
        alignment = course_alignment[index - 1]
        row["day"] = str(index)
        row["studyMode"] = "Course + lab"
        row["courseTitle"] = "DevOps for Beginners: Docker, Kubernetes, Cloud, CI/CD - 4 Projects"
        row["courseUrl"] = "https://healthpartners.udemy.com/course/devops-for-beginners-docker-k8s-cloud-cicd-4-projects/"
        row["courseSections"] = alignment["courseSections"]
        row["dailyPlan"] = alignment["dailyPlan"]
        row["labFocus"] = alignment["labFocus"]
        row["moduleAnchor"] = f"basics-{index}"
        row["detailUrl"] = f"/training-basics/topics/{index}"
        row["linkLabel"] = "Open topic page"
        row["screeningQuestions"] = [
            f"What is the purpose of {row['focus']} in a real project?",
            "How do you know the system is healthy or failing?",
            "What evidence would you show to support your answer?",
        ]
        row["screeningAnswer"] = (
            f"{row['learn']} In the sample scenario, {row['scenario']} "
            f"The proof artifact is: {row['output']}"
        )
    return rows


def _training_basics_five_six_year_interview_questions() -> list[dict[str, Any]]:
    return [
        {
            "category": "Project And Ownership Screen",
            "why": "5-6 years JD screens usually start by checking whether project scale, role boundary, and real ownership sound specific instead of generic.",
            "questions": [
                "Walk me through your recent project architecture and where your role fit.",
                "What did you own directly, what did you contribute to, and what was owned by another team?",
                "Describe one production issue you handled from symptom to validation.",
                "How did you document your work so another engineer or support team could maintain it?",
            ],
            "answer_model": "Answer with context, responsibility, action, evidence, and handoff. Mention scale through systems, teams, environments, applications, tickets, pipelines, dashboards, or data flows instead of vague tool lists.",
        },
        {
            "category": "DevOps And Release Engineering Screen",
            "why": "Most 5-6 years DevOps-style JDs expect the candidate to explain CI/CD, artifacts, deployment safety, rollback, and release evidence clearly.",
            "questions": [
                "Explain your CI/CD pipeline from code commit to production validation.",
                "A deployment failed after the image was pushed. What do you check first?",
                "How do you handle rollback, hotfix, and post-release validation?",
                "What evidence proves a release was successful?",
            ],
            "answer_model": "Use commit, branch, PR, build, test, scan, artifact, image tag, deployment, rollout status, health check, dashboard, alert, rollback, and incident note as the core vocabulary.",
        },
        {
            "category": "Cloud Platform And Infrastructure Screen",
            "why": "Cloud, platform, and infrastructure JDs test whether the consultant understands networking, identity, compute, storage, IaC, backup, DR, and support boundaries.",
            "questions": [
                "Explain VPC/VNet, subnet, route table, security group/NSG, load balancer, and private endpoint in one flow.",
                "What is the difference between high availability, backup, disaster recovery, and failover?",
                "How do you review a Terraform plan before applying it?",
                "How do you approach cost, capacity, and resource cleanup in cloud environments?",
            ],
            "answer_model": "Start from business/application need, then map network path, identity, runtime, storage, monitoring, backup/DR, IaC change, owner team, and validation evidence.",
        },
        {
            "category": "Kubernetes, Containers, And Runtime Screen",
            "why": "For 5-6 years JDs, container questions usually test runtime troubleshooting, not just definitions.",
            "questions": [
                "A pod is in CrashLoopBackOff. What commands do you run and what output matters?",
                "Explain deployment, replica set, pod, service, ingress, configmap, secret, probe, and namespace.",
                "How do resource limits, readiness probes, environment variables, and image tags affect production stability?",
                "How do you validate a rollout and decide whether rollback is needed?",
            ],
            "answer_model": "Use kubectl get/describe/logs/events/rollout, then explain image, command, env, secret, config, probe, resource, node, service route, dependency, and recent change.",
        },
        {
            "category": "Observability, Incident, And SRE Screen",
            "why": "Senior screening looks for signal-based troubleshooting: logs, metrics, traces, alerts, SLOs, timeline, root cause, prevention, and communication.",
            "questions": [
                "Users report slowness. How do you investigate using logs, metrics, traces, and dashboards?",
                "What are latency, traffic, errors, and saturation? Give an incident example.",
                "How do you validate an API integration issue?",
                "When would you write Shell versus Python automation?",
            ],
            "answer_model": "Frame the answer as symptom, impact, recent change, golden signals, dependency trace, owner route, mitigation, validation, runbook update, and prevention.",
        },
    ]


def _training_basics_module_depth(module: dict[str, Any]) -> dict[str, Any]:
    title = str(module.get("title", ""))
    concepts = _as_list(module.get("concepts"))
    commands = _as_list(module.get("commands"))
    primary = concepts[0] if concepts else "the topic"
    command_one = commands[0] if commands else "run the first command"
    command_two = commands[1] if len(commands) > 1 else command_one
    command_three = commands[2] if len(commands) > 2 else command_two

    common = {
        "fundamentals": [
            f"Define {primary} in one sentence.",
            f"Identify where {primary} appears in a real application environment.",
            f"Run `{command_one}` or the closest status check and read the output.",
            f"Separate healthy, warning, and failed states for {primary}.",
            f"Save one {primary} proof artifact with secrets removed.",
            f"Explain the next action or escalation path when {primary} is not the failing layer.",
        ],
        "lab_steps": [
            f"Run `{command_one}` and record the exact output.",
            f"Run `{command_two}` and compare what changed.",
            f"Run `{command_three}` to inspect a second layer or resource.",
            "Create one failure condition in a safe lab or explain a simulated failure.",
            "Write a runbook note with command, output, meaning, and next action.",
        ],
        "quiz": [
            f"What does `{command_one}` prove?",
            "What output means healthy?",
            "What output means failed?",
            "What is the safest next check?",
            "What evidence should be saved?",
        ],
        "skill_gate": [
            "I can read the key command output and explain what changed.",
            "I can describe healthy and failed signals in plain technical language.",
            "I can connect one common failure path to the system layer it affects.",
            "I can produce one sanitized evidence note with symptom, signal, action, and validation.",
            "I can answer one screening question with a project scenario and evidence.",
        ],
        "theory": [
            f"{primary} is the foundation idea for this section. Learn what it means, where it appears in real systems, and what evidence proves it is working.",
            "Concept understanding comes before tool memory: input, action, output, healthy state, failed state, and owner path.",
            "The practical pattern is always the same: identify context, inspect output, compare healthy versus failed behavior, validate the result, and document the handoff.",
        ],
        "story": [
            f"{primary} in plain English comes before tool names.",
            "Describe the input, the action, the output, and how a support person would know whether it worked.",
            "Connect every command to a visible proof item: status output, log line, file change, dashboard signal, or runbook note.",
            "A clear explanation names the first check, the changed signal, and the owner route when the result stays unclear.",
        ],
        "mental_model": [
            "What exists before the command runs?",
            "What does the command read or change?",
            "What output should a healthy system show?",
            "What output suggests a warning or failure?",
            "Which output, screenshot, log line, or ticket note proves the result?",
        ],
        "guided_lab": [
            f"Run `{command_one}` and write what the output tells you.",
            f"Run `{command_two}` and compare the output with the first command.",
            f"Run `{command_three}` after making a small safe change or checking a second resource.",
            "Save one output snippet and write a two-sentence explanation below it.",
            "Turn the explanation into one interview sentence using situation, action, evidence, and result.",
        ],
        "failure_scenarios": [
            "Command returns no data because the wrong folder, namespace, account, or context is selected.",
            "Command returns an error because permissions, credentials, file path, or service state is wrong.",
            "Output looks healthy but the user-facing problem remains, which means the failing layer is somewhere else.",
            "If the output cannot be explained, the topic needs another lab before marketing.",
        ],
        "evidence_checklist": [
            f"`{command_one}` output with timestamp or lab context.",
            f"Healthy and failed output examples for {primary}.",
            f"One sanitized screenshot or copied output tied to {primary}.",
            f"Runbook note for {primary}: symptom, check, meaning, action, escalation.",
            f"One resume-safe sentence and one interview-safe story using {primary}.",
        ],
        "practice_questions": [
            f"What does `{command_one}` prove?",
            "What would you check if the command fails?",
            "Which output would you save as evidence?",
            "Which owner should receive the issue if this check is healthy?",
            "How would you explain this topic to a non-technical manager?",
        ],
        "staff_gate": [
            "Run the commands without reading every step.",
            "Explain healthy output and failed output.",
            "Write a short runbook note from the lab.",
            "Answer two troubleshooting questions by naming signal, meaning, next check, and owner route.",
        ],
        "common_mistakes": [
            "Memorizing commands without knowing what the output means.",
            "Skipping context checks before diagnosing a failure.",
            "Using production-like examples without removing sensitive values.",
            "Turning a lab into a resume bullet before the evidence is understandable.",
        ],
        "interview_examples": _training_basics_interview_examples(title),
        "flowchart": _training_basics_flowchart(title),
    }

    if "Terminal" in title:
        common["mini_project"] = [
            "Create a lab folder, write a sample app log, add success and error lines, and search for the error pattern.",
            "Find one running process and one listening port, then explain whether they prove the application is healthy.",
            "Write a runbook note for a service that fails to start because a file permission or port is wrong.",
        ]
    elif "Git" in title:
        common["mini_project"] = [
            "Create a branch, make a small README or config change, commit it, and inspect the diff.",
            "Write a pull-request summary with purpose, risk, validation, and rollback note.",
            "Practice explaining why commit history is evidence and why unreviewed changes are risky.",
        ]
    elif "Docker" in title:
        common["mini_project"] = [
            "Build a small image, run a container, map a port, check logs, and stop/remove the container cleanly.",
            "Break an environment variable or port mapping and capture the failed log.",
            "Write the difference between image, container, registry, and runtime in beginner language.",
        ]
    elif "Kubernetes" in title:
        common["mini_project"] = [
            "Inspect namespace, pods, deployment, service, events, logs, and rollout status for a sample workload.",
            "Simulate a failed deployment story: wrong image, missing secret, crash loop, or readiness failure.",
            "Write a triage path from pod status to logs to events to rollback.",
        ]
    elif "Cloud Basics" in title:
        common["mini_project"] = [
            "Draw account/subscription/project, region, network, subnet, compute, storage, identity, and logs.",
            "Run one identity command, one network command, and one storage command for AWS, Azure, or GCP.",
            "Explain what belongs to the cloud/platform team versus the application team.",
        ]
    elif "CI/CD" in title:
        common["mini_project"] = [
            "Trace one release from commit to build to artifact/image to deploy to health check.",
            "Capture a failed pipeline log and identify whether the failure is build, test, scan, deploy, or approval.",
            "Write a release confidence note with validation and rollback steps.",
        ]
    elif "Observability" in title:
        common["mini_project"] = [
            "Use logs, metrics, traces, and health endpoint checks to describe one incident.",
            "Write a timeline with symptom, first signal, suspected layer, validation, recovery, and prevention.",
            "Practice explaining latency, traffic, errors, and saturation using one example.",
        ]
    elif "Infrastructure As Code" in title:
        common["mini_project"] = [
            "Run format, validate, and plan against a small IaC example.",
            "Read a plan output and classify create, update, replace, and destroy risk.",
            "Write a change review note that explains state, variables, environment, and rollback limits.",
        ]
    elif "Security" in title:
        common["mini_project"] = [
            "List one identity, one permission, one secret location, and one audit signal.",
            "Explain why secrets do not belong in source code, screenshots, tickets, or chat messages.",
            "Write a least-privilege review note for one application workflow.",
        ]
    elif "Shell And Python Automation" in title:
        common["mini_project"] = [
            "Write a tiny shell health check that calls an endpoint, prints the HTTP status, and exits with success or failure.",
            "Write a tiny Python script that reads JSON or YAML config, calls one API, logs the response, and handles one error.",
            "Create a table of script examples from the PDF: provisioning, monitoring, backup, log cleanup, Jenkins trigger, Kubernetes deploy, Terraform, DB migration, API test, image scan, disk alert, and load test.",
        ]
    else:
        common["mini_project"] = [
            "Pick one role-specific use case and map the prerequisite commands to it.",
            "Collect one command output, one diagram, one runbook note, and one interview story.",
            "Confirm readiness for detailed role/domain training with one project story, one troubleshooting story, and one evidence note.",
        ]
    return common


def _training_basics_topic_sections(module: dict[str, Any], topic_number: int) -> list[dict[str, Any]]:
    section_specs = [
        {
            "key": "core-concept-model",
            "label": "Core Concept Model",
            "summary": "Explains the concept, vocabulary, system boundary, healthy state, failed state, and interview framing.",
            "items": _as_list(module.get("theory")) + _as_list(module.get("story")) + _as_list(module.get("mental_model")),
            "detail_kind": "concept",
        },
        {
            "key": "scenario-practice",
            "label": "Scenario Practice",
            "summary": "Turns the topic into a realistic project-style scenario with steps, expected observations, and answer wording.",
            "items": _as_list(module.get("mini_project")) + _as_list(module.get("guided_lab")) + _as_list(module.get("lab_steps")),
            "detail_kind": "scenario",
        },
        {
            "key": "failure-patterns",
            "label": "Failure Patterns",
            "summary": "Shows common failures, their meaning, and the evidence that separates diagnosis from guessing.",
            "items": _as_list(module.get("failure_scenarios")) + _as_list(module.get("common_mistakes")),
            "detail_kind": "failure",
        },
        {
            "key": "evidence-package",
            "label": "Evidence Package",
            "summary": "Defines what proof to save: commands, screenshots, logs, diagrams, validation notes, and handoff language.",
            "items": _as_list(module.get("evidence_checklist")) + _as_list(module.get("commands")),
            "detail_kind": "evidence",
        },
        {
            "key": "mostly-asked-interview-examples",
            "label": "Mostly Asked Interview Examples",
            "summary": "Gives screening and technical interview questions with complete answer models for 5-6 year consultant expectations.",
            "items": _as_list(module.get("interview_examples")) + _as_list(module.get("practice_questions")),
            "detail_kind": "interview",
        },
        {
            "key": "checkpoint-questions",
            "label": "Checkpoint Questions",
            "summary": "Connects the topic to functionality, evidence, and project context instead of loose definitions or disconnected tools.",
            "items": _as_list(module.get("quiz")) + _as_list(module.get("practice_questions")),
            "detail_kind": "checkpoint",
        },
        {
            "key": "skill-gate",
            "label": "Readiness Check",
            "summary": "Defines the minimum practical standard before moving from basics into role/domain training.",
            "items": _as_list(module.get("skill_gate")) + _as_list(module.get("staff_gate")),
            "detail_kind": "skill",
        },
    ]
    sections: list[dict[str, Any]] = []
    for index, spec in enumerate(section_specs, start=1):
        raw_items = [_pdf_clean_text(item) for item in spec["items"] if _pdf_clean_text(item)]
        if not raw_items:
            raw_items = [_pdf_clean_text(module.get("interview")) or "Explain the topic with command evidence and a realistic troubleshooting path."]
        items = _dedupe_preserve_order(raw_items)
        section = {
            "index": index,
            "key": spec["key"],
            "label": spec["label"],
            "summary": spec["summary"],
            "items": items,
            "preview": items[:4],
            "detail_url": f"/training-basics/topics/{topic_number}/sections/{spec['key']}",
            "expanded_material": _training_basics_expanded_material(module, spec["label"], spec["detail_kind"], items),
            "qa": _training_basics_section_questions(module, spec["label"], spec["detail_kind"], items),
        }
        sections.append(section)
    return sections


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _training_basics_expanded_material(module: dict[str, Any], section_label: str, detail_kind: str, items: list[str]) -> list[dict[str, Any]]:
    title = _pdf_clean_text(module.get("title"))
    concepts = _as_list(module.get("concepts"))
    commands = _as_list(module.get("commands"))
    concept_text = ", ".join(_pdf_clean_text(item) for item in concepts[:8] if _pdf_clean_text(item)) or "the listed fundamentals"
    command_text = ", ".join(_pdf_clean_text(item) for item in commands[:5] if _pdf_clean_text(item)) or "the listed commands"
    explanatory_items = _training_basics_explanatory_items(module, detail_kind, items)
    base = {
        "concept": [
            {
                "heading": "Enterprise Concept",
                "body": f"{section_label} for {title} starts with system behavior, not tool names. In an interview response, I explain what exists, what changes, what output proves health, and what output proves risk. The important vocabulary is {concept_text}.",
                "bullets": explanatory_items[:8],
            },
            {
                "heading": "Screening Response",
                "body": f"I connect plain English, project context, command evidence, and ownership. I name the first check, the signal I found, the likely layer, and the next check without overclaiming production ownership.",
                "bullets": [
                    "I start with the business or user symptom.",
                    f"I name the relevant layer inside {title}.",
                    f"I support the response with evidence such as {command_text}.",
                    "I explain healthy, warning, and failed states.",
                    "I close with validation, handoff, or escalation.",
                ],
            },
        ],
        "scenario": [
            {
                "heading": "Project Functionality Scenario",
                "body": f"In a project, {title} is usually visible as a small but important operating flow: a request enters the system, one layer is inspected or changed, the output confirms the state, and the result becomes evidence for the next owner.",
                "bullets": explanatory_items[:10],
            },
            {
                "heading": "Project Scenario Response",
                "body": "I explain the scenario as a short project situation: the system condition, the layer involved, the signal observed, and the validation proof.",
                "bullets": [
                    "Starting condition: the user symptom, release change, support ticket, or environment state.",
                    "System layer: file, process, port, service, container, cluster, cloud resource, pipeline, data, or access control.",
                    "Evidence source: command output, log line, dashboard signal, deployment status, diagram, or ticket note.",
                    "Interpretation: what the evidence says about healthy, warning, or failed behavior.",
                    "Outcome: validated recovery, clean handoff, rollback decision, or escalation boundary.",
                ],
            },
        ],
        "failure": [
            {
                "heading": "Failure Meaning",
                "body": f"Failure patterns in {title} help me avoid guessing. I translate symptoms into likely layers: context, permission, configuration, runtime, network, storage, dependency, release, or ownership boundary.",
                "bullets": explanatory_items[:10],
            },
            {
                "heading": "Failure Triage Pattern",
                "body": "My triage response moves from low-risk inspection to higher-impact action. I do not jump to restart, rollback, scale, or delete before reading evidence.",
                "bullets": [
                    "Confirm the correct account, context, folder, namespace, or environment.",
                    "Read status output before changing anything.",
                    "Read logs and events when status is not enough.",
                    "Compare recent change against the failure time.",
                    "Validate recovery before closing the issue.",
                ],
            },
        ],
        "evidence": [
            {
                "heading": "Evidence Standard",
                "body": f"Evidence for {title} proves what was checked and what the output meant. I keep it sanitized, specific, and tied to a conclusion. The strongest evidence connects a command, screenshot, log line, dashboard signal, diagram, or ticket note to a clear decision.",
                "bullets": explanatory_items[:10],
            },
            {
                "heading": "Evidence Wording",
                "body": "I avoid vague phrases like 'I checked everything.' Good evidence says exactly what I checked, what changed, and why the next action was correct.",
                "bullets": [
                    "Command or artifact name.",
                    "Observed output or signal.",
                    "Healthy versus failed interpretation.",
                    "Action taken or owner handoff.",
                    "Validation proof after the action.",
                ],
            },
        ],
        "interview": [
            {
                "heading": "Interview Answer Standard",
                "body": f"For {title}, I answer concisely first and expand only when the interviewer asks for depth. In the first minute I cover the system layer, the evidence, and the result; in the deeper round I add commands, architecture, failure signals, and validation.",
                "bullets": explanatory_items[:10],
            },
            {
                "heading": "Enterprise Response Shape",
                "body": "My answer uses the same pattern repeatedly: situation, layer, evidence, interpretation, action, validation, and boundary. That keeps the response practical instead of theoretical.",
                "bullets": [
                    "Situation: what was happening.",
                    "Layer: where the issue or work lived.",
                    "Evidence: what output proved it.",
                    "Action: what changed or who owned it.",
                    "Validation: how the result was confirmed.",
                ],
            },
        ],
        "checkpoint": [
            {
                "heading": "Checkpoint Standard",
                "body": f"My checkpoint response for {title} covers the concept, the relevant checks, the output meaning, and follow-up questions without reading from a script.",
                "bullets": explanatory_items[:10],
            },
            {
                "heading": "Stable Interview Response",
                "body": "My response stays specific enough to prove understanding, but natural enough to handle a different wording of the same question.",
                "bullets": [
                    "Can explain the concept in plain language.",
                    "Can name the command or artifact that proves it.",
                    "Can recognize at least one healthy signal.",
                    "Can recognize at least one failed signal.",
                    "Can explain the next check or owner handoff.",
                ],
            },
        ],
        "skill": [
            {
                "heading": "Readiness Check",
                "body": f"For {title}, I demonstrate readiness through clear explanation, command evidence, troubleshooting, and role/domain connection.",
                "bullets": explanatory_items[:10],
            },
            {
                "heading": "Completion Signal",
                "body": "My response is ready when it stays stable across multiple question styles: definition, scenario, failure, evidence, and project story.",
                "bullets": [
                    "Explains without overclaiming.",
                    "Uses evidence instead of generic confidence.",
                    "Keeps role boundaries clear.",
                    "Answers follow-up questions with the same logic.",
                    "Can connect basics to a role/domain use case.",
                ],
            },
        ],
    }
    return base.get(detail_kind, base["concept"])


def _training_basics_explanatory_items(module: dict[str, Any], detail_kind: str, items: list[str]) -> list[str]:
    title = _pdf_clean_text(module.get("title"))
    concepts = [_pdf_clean_text(item) for item in _as_list(module.get("concepts")) if _pdf_clean_text(item)]
    commands = [_pdf_clean_text(item) for item in _as_list(module.get("commands")) if _pdf_clean_text(item)]
    primary = concepts[0] if concepts else "the topic"
    secondary = concepts[1] if len(concepts) > 1 else "the related system layer"
    command_one = commands[0] if commands else "the first relevant command"
    command_two = commands[1] if len(commands) > 1 else "a second confirming command"
    raw_context = [_pdf_clean_text(item) for item in items[:4] if _pdf_clean_text(item)]

    def context_sentence(index: int, fallback: str) -> str:
        if index >= len(raw_context):
            return fallback
        value = raw_context[index]
        value = re.sub(r"^(Run|Create|Write|Find|Save|Turn|Ask|Pick|Capture|Draw|List|Practice|Read|Use|Explain)\s+", "", value, flags=re.IGNORECASE)
        value = value.rstrip(".")
        return value[:1].upper() + value[1:] + "."

    if detail_kind == "scenario":
        return [
            f"The project scenario begins with a visible operating need in {title}: a service, file, process, resource, release, or integration needs to be understood from evidence.",
            f"The main system layer is {primary}; the supporting layer is {secondary}. Together they explain where the symptom starts and where validation should happen.",
            f"Typical evidence comes from {command_one} first and {command_two} second, because one check gives context and the other confirms state or behavior.",
            f"A realistic explanation is: {context_sentence(0, 'A small application or service produced a signal that had to be interpreted before handoff.')}",
            f"The scenario is ready for an enterprise interview when I can say what changed, what output I observed, why it mattered, and what result I validated.",
            "My project story describes functionality: how the system is supposed to work, where the failure or change appears, and how the evidence proves the final state.",
            "I sound like a participant in delivery or support, not like someone reading a checklist.",
            "A good scenario can be reused in interviews because it has a symptom, a system layer, an evidence source, a decision, and an outcome.",
        ]
    if detail_kind == "failure":
        return [
            f"A common failure in {title} happens when the visible symptom is checked in the wrong context, such as the wrong folder, account, namespace, branch, environment, or service.",
            f"Permission and credential failures usually appear as denied access, missing resource output, unauthorized responses, or commands that work for one identity but not another.",
            f"Configuration failures usually appear when {primary} exists, but {secondary} points to the wrong value, path, port, secret, dependency, or environment setting.",
            f"Runtime failures usually show up after the resource starts: logs contain errors, health checks fail, processes restart, pods crash, or users still see impact.",
            f"Misleading health is possible: one command can look clean while the user-facing flow still fails, so the next layer must be checked.",
            "My safest failure explanation names the symptom first, then the evidence, then the likely layer, then the next validation step.",
            "I avoid saying the system is fixed until I can show the post-change signal.",
            "The interview value is the reasoning path: symptom, signal, layer, decision, validation.",
        ]
    if detail_kind == "evidence":
        return [
            f"My evidence package for {title} connects the checked layer to a conclusion, not just a command output.",
            f"`{command_one}` is useful when it is paired with meaning: what was expected, what appeared, and what decision it supported.",
            f"`{command_two}` provides a second signal so I do not rely on one incomplete output.",
            "A complete evidence note includes context, command or artifact, observed signal, interpretation, action, validation, and owner boundary.",
            "I remove sensitive values from screenshots, logs, tickets, URLs, tokens, usernames, customer data, and secrets.",
            "The evidence is specific enough to be credible and sanitized enough to avoid exposing production details.",
            "The evidence supports both technical review and resume/interview storytelling.",
            "The strongest proof shows before-state, action or decision, and after-state validation.",
        ]
    if detail_kind == "interview":
        return [
            f"My answer for {title} starts with system behavior, then mentions tools as evidence sources.",
            f"In an initial screen, I use plain language: {primary} is the layer being checked, and the output proves whether the flow is healthy or blocked.",
            f"In a technical round, I add command depth using {command_one}, {command_two}, logs, status output, or diagrams.",
            "In the scenario, I focus on what the system was supposed to do, what signal I checked, and what changed.",
            "When the first signal is inconclusive, I name the next diagnostic layer instead of guessing.",
            "For a 5-6 year JD, I include business impact, technical layer, evidence, decision, validation, and role boundary.",
            "I do not claim ownership of unrelated teams; I explain handoff clearly.",
            "My best project stories are reusable because they explain a real flow, a failure path, and proof of resolution.",
        ]
    if detail_kind == "checkpoint":
        return [
            f"The checkpoint for {title} is understanding, not memorization. I explain {primary}, read evidence, and connect it to a project scenario.",
            f"The concept is clear when {command_one} and {command_two} can be described by purpose, output, and decision value.",
            "My passing response separates definition, functionality, failure signal, evidence, and handoff boundary.",
            "I can answer the same concept as a definition, a scenario, a failure, and an interview story.",
            "A weak response lists commands without explaining why they matter.",
            "My answer explains what the command proves and what it does not prove.",
            "The response focuses on clarity, evidence discipline, and role-boundary awareness.",
            "My checkpoint is complete when I can handle follow-up questions without changing the story.",
        ]
    if detail_kind == "skill":
        return [
            f"Readiness for {title} means I can explain the concept, use evidence, and connect it to a role/domain use case.",
            f"The skill is not just knowing {primary}; it is knowing where {primary} appears in an enterprise workflow and what signal proves it works.",
            f"I am comfortable describing evidence from {command_one}, {command_two}, diagrams, logs, dashboards, tickets, or validation notes.",
            "I explain what I owned, what another team owned, and how handoff worked.",
            "I keep the same facts consistent across recruiter screens, technical rounds, architecture discussions, BA reviews, PM updates, and support handoffs.",
            "I convert the concept into one project story and one troubleshooting story.",
            "The skill gate is passed when I can explain the topic without reading the page.",
            "The goal is durable project readiness: understanding the system enough to communicate, troubleshoot, validate, and collaborate in delivery.",
        ]
    return [
        f"{title} is understood when I can explain {primary}, the related system flow, and the evidence that proves the flow is healthy.",
        f"The key vocabulary includes {', '.join(concepts[:5]) or primary}. I explain these terms through functionality, not memorized definitions.",
        f"Useful evidence includes {command_one}, {command_two}, logs, status output, diagrams, screenshots, or ticket notes depending on the topic.",
        "My explanation connects system behavior, failure signals, validation, and ownership boundary.",
        "I make the same point in simple recruiter language and deeper technical language.",
        "A good answer avoids vague phrases and names the signal that proves the conclusion.",
    ]


def _training_basics_section_questions(module: dict[str, Any], section_label: str, detail_kind: str, items: list[str]) -> list[dict[str, str]]:
    title = _pdf_clean_text(module.get("title"))
    interview_line = _pdf_clean_text(module.get("interview"))
    concepts = [_pdf_clean_text(item) for item in _as_list(module.get("concepts")) if _pdf_clean_text(item)]
    commands = [_pdf_clean_text(item) for item in _as_list(module.get("commands")) if _pdf_clean_text(item)]
    primary = concepts[0] if concepts else "this topic"
    secondary = concepts[1] if len(concepts) > 1 else primary
    command_one = commands[0] if commands else "the first relevant command"
    command_two = commands[1] if len(commands) > 1 else command_one
    primary_sentence = primary[:1].upper() + primary[1:]
    secondary_sentence = secondary[:1].upper() + secondary[1:]
    topic_label = re.sub(r"^\d+\.\s*", "", title)
    example_item = items[0] if items else "the system had a visible signal that needed to be inspected and validated"
    section_questions: dict[str, list[tuple[str, str]]] = {
        "concept": [
            (
                f"What is {primary} in {topic_label}?",
                f"I explain {primary} as the starting point for understanding the system state. In {topic_label}, I connect the concept to what exists, what changed, what evidence confirms the state, and which owner or layer comes next.",
            ),
            (
                f"Where does {primary} appear in an enterprise environment?",
                f"{primary_sentence} appears in the day-to-day path of deployments, support tickets, environment checks, access issues, and operational handoffs. I treat it as part of the system flow, not as an isolated definition.",
            ),
            (
                f"What does `{command_one}` prove at the concept level?",
                f"`{command_one}` proves the immediate context before I go deeper. It helps confirm whether I am looking at the right location, account, namespace, branch, service, or environment before drawing a conclusion.",
            ),
            (
                f"How does {secondary} relate to {primary}?",
                f"{secondary_sentence} is the supporting layer that gives {primary} meaning in a real system. I connect the two by showing how one signal confirms context and the other confirms state, behavior, or failure.",
            ),
            (
                "What is the healthy state for this concept?",
                "The healthy state is visible through expected output, stable status, correct permissions, reachable endpoints, clean logs, or a successful validation signal. I do not call it healthy until the observed behavior matches the expected behavior.",
            ),
            (
                "What is the failed state for this concept?",
                "The failed state shows up as missing output, denied access, incorrect context, failed status, unresolved user impact, or an error that points to the next diagnostic layer.",
            ),
            (
                "How do you explain this in an initial recruiter screen?",
                f"I keep it outcome-focused: {interview_line or 'I checked the relevant system layer, reviewed the evidence, identified the signal, and validated the result before handoff.'}",
            ),
            (
                "How do you explain this in a technical round?",
                f"I start with the system flow, then mention {', '.join(concepts[:5]) or primary}, and then explain how `{command_one}` or another artifact changed my next step.",
            ),
            (
                "What ownership boundary matters here?",
                "My ownership is the investigation, evidence, validation, and handoff. If the evidence points to application code, security approval, platform ownership, data ownership, or business signoff, I route it to the right owner with context.",
            ),
            (
                "What makes this concept useful after placement?",
                "It helps me avoid guessing, ask better questions, read system behavior, and communicate clearly with developers, QA, platform, security, operations, and support teams.",
            ),
        ],
        "scenario": [
            (
                f"Give me a project scenario for {topic_label}.",
                f"In one project scenario, {example_item.rstrip('.')}. I start with the expected behavior, check the affected layer, read the output, and validate whether the issue needs a fix, rollback, or handoff.",
            ),
            (
                "What was the business or user symptom?",
                "The symptom was visible before the technical detail mattered: a service was unavailable, a release did not behave correctly, a workflow was blocked, or a user-facing check did not match the expected result.",
            ),
            (
                "Which technical layer did you inspect first?",
                f"I inspected {primary} first because it gave me the first reliable signal. If that was inconclusive, I moved to {secondary} or another status, log, configuration, or dependency check.",
            ),
            (
                f"How did `{command_one}` help in the scenario?",
                f"`{command_one}` gave me a concrete signal instead of a guess. I used it to confirm context, compare expected and observed behavior, and decide whether the next check belonged in the same layer or a downstream layer.",
            ),
            (
                f"What did `{command_two}` add?",
                f"`{command_two}` acted as the second signal. It helped confirm what existed, what changed, or why the first result was not enough to close the issue.",
            ),
            (
                "How did you validate the result?",
                "I validated the result with a clean command output, expected response, stable log pattern, dashboard signal, ticket note, or handoff confirmation from the owning team.",
            ),
            (
                "What did you communicate to the team?",
                "I communicated the symptom, checked layer, evidence, interpretation, action taken, and remaining owner path. That kept the update useful without adding noise.",
            ),
            (
                "What was out of scope for you?",
                "I did not claim ownership of business decisions, security exceptions, production approvals, or application code changes unless those were explicitly assigned to my role.",
            ),
            (
                "How do you make the scenario credible?",
                "I include a real system path, one or two evidence points, a decision, and a validated outcome. That makes the scenario practical instead of tool-heavy.",
            ),
            (
                "What did this scenario improve?",
                "It improved troubleshooting clarity, handoff quality, recovery confidence, and the team’s ability to repeat the same check the next time the symptom appeared.",
            ),
        ],
        "failure": [
            (
                f"What failure pattern do you commonly see with {primary}?",
                f"A common failure is checking the right symptom in the wrong context. With {primary}, that can mean the wrong folder, account, namespace, branch, environment, endpoint, or owner boundary.",
            ),
            (
                "How do you separate symptom from root cause?",
                "I treat the user-visible issue as the symptom and the evidence-backed failing layer as the likely cause. I do not call something root cause until the signal and recovery path support it.",
            ),
            (
                f"What failed output from `{command_one}` matters?",
                f"Failed output from `{command_one}` matters when it shows missing context, denied access, unexpected state, empty results, wrong target, or a signal that contradicts the expected behavior.",
            ),
            (
                "What is a misleading healthy signal?",
                "A misleading healthy signal happens when one layer looks clean but the user-facing flow still fails. In that case I move to the next layer instead of closing the issue.",
            ),
            (
                "When do you stop troubleshooting and escalate?",
                "I escalate when the evidence points outside my access, approval, ownership, or business-policy boundary. I include the symptom, checks already completed, observed output, and suspected owner path.",
            ),
            (
                "How do you avoid making the failure worse?",
                "I begin with read-only checks, confirm context, avoid destructive commands, review recent changes, and validate impact before restart, rollback, scale, delete, or configuration changes.",
            ),
            (
                "How do you document the failure?",
                "I document the symptom, timestamp or lab context, checked layer, failed signal, likely cause, action taken, recovery signal, and prevention or handoff note.",
            ),
            (
                "What does recovery look like?",
                "Recovery means the failed signal is gone and the original workflow behaves as expected. I validate through output, logs, health checks, dashboard signals, or owner confirmation.",
            ),
            (
                "How do you explain repeated failures?",
                "Repeated failures usually mean the fix handled the symptom but not the underlying layer. I look for recurring patterns in logs, metrics, recent changes, dependencies, capacity, access, or configuration.",
            ),
            (
                "What prevention action follows this failure?",
                "Prevention can be a runbook update, clearer alert, validation check, automation guardrail, access correction, configuration standard, or better handoff note.",
            ),
        ],
        "evidence": [
            (
                "What evidence did you use to validate the issue or change?",
                "I used the command output or system artifact, the sanitized log or dashboard signal, and a short interpretation of what changed. I compared the failed signal with the healthy signal and used that comparison to decide the next action.",
            ),
            (
                f"Why is `{command_one}` useful as evidence?",
                f"`{command_one}` is useful because it gives a direct signal about the layer I am checking. I pair it with context and interpretation so it is not just a copied command output.",
            ),
            (
                f"Why do you need `{command_two}` or a second signal?",
                f"`{command_two}` helps confirm the first result. A second signal reduces false confidence when one output is incomplete, stale, or pointing at the wrong context.",
            ),
            (
                "What evidence is safe to share?",
                "Safe evidence removes secrets, tokens, customer data, internal-only URLs, usernames, account numbers, PHI, PII, and any production value that does not belong in a resume, ticket excerpt, or mock discussion.",
            ),
            (
                "What does a good evidence note include?",
                "A good evidence note includes the symptom, checked layer, command or artifact, observed output, interpretation, action, validation, and owner boundary.",
            ),
            (
                "How do you prove the before and after state?",
                "I show the failed signal before the action and the healthy signal after the action. That comparison proves the change was validated, not just attempted.",
            ),
            (
                "What evidence supports a handoff?",
                "A handoff is supported by the symptom, checked layer, output, suspected owner, severity, impact, and a clear next action for the receiving team.",
            ),
            (
                "What evidence supports a rollback decision?",
                "Rollback evidence includes the release marker, failed validation, affected workflow, recent change, error signal, rollback step, and post-rollback health check.",
            ),
            (
                "What evidence supports a runbook update?",
                "A runbook update is supported by the repeatable symptom, exact check, expected output, failed output, action path, escalation boundary, and validation step.",
            ),
            (
                "How do you keep evidence useful after the incident?",
                "I keep evidence short, searchable, sanitized, and tied to the system layer so another engineer can repeat the check without rebuilding the whole investigation.",
            ),
        ],
        "interview": [
            (
                "Walk me through this in 60 seconds.",
                "The situation was tied to a visible system symptom, so I first identified the affected layer, checked the relevant evidence, and then decided whether the issue needed a fix, rollback, or handoff. I validated the result with command output, logs, a dashboard signal, or a diagram, and I kept the ownership boundary clear.",
            ),
            (
                f"How do you connect {secondary} to a real project scenario?",
                f"I connect {secondary} to project work through delivery, troubleshooting, validation, and handoff. In a real project, I tie it to the business symptom first, then identify the technical layer, show the evidence I checked, and close with the result or owner handoff.",
            ),
            (
                "What follow-up question do you usually expect?",
                "I expect a follow-up on the exact command, output, failure signal, recent change, validation step, and owner boundary. I answer with one concrete example instead of adding more tools.",
            ),
            (
                "How do you keep the answer practical?",
                "I keep it practical by using one scenario, one or two evidence points, a decision, and a validated result. I mention tools only when I can explain what each tool proved.",
            ),
            (
                "What makes this credible for a 5-6 year JD screening?",
                "For a 5-6 year JD, my response needs ownership language, troubleshooting order, evidence discipline, role boundary, and enough depth to survive follow-up questions. I describe production support behavior, not classroom definitions.",
            ),
            (
                "How do you avoid overclaiming?",
                "I separate what I owned from what I supported. I own the investigation, evidence, validation, automation, or handoff only when that matches the role boundary.",
            ),
            (
                "How do you handle a command you have not used directly?",
                "I stay honest and connect the command to the surrounding concept. I explain what the command is normally used for, what output matters, and how I validate the same layer with adjacent evidence.",
            ),
            (
                "How do you answer when the first signal is inconclusive?",
                f"I explain that `{command_one}` gave partial context, then I moved to `{command_two}` or another confirming signal before deciding on recovery or handoff.",
            ),
            (
                "How do you explain impact?",
                "I explain impact through the affected workflow, blocked user or team, failed release path, delayed job, degraded service, or support risk, then connect the evidence to recovery.",
            ),
            (
                "How do you close the answer?",
                "I close with validation: what changed, what signal became healthy, what owner accepted the handoff, or what runbook/update reduced repeat risk.",
            ),
        ],
        "checkpoint": [
            (
                f"Define {primary} without reading notes.",
                f"{primary_sentence} is the layer I use to understand context, state, and next action in {topic_label}. I explain it through system behavior and evidence, not a memorized definition.",
            ),
            (
                f"What does `{command_one}` prove and not prove?",
                f"`{command_one}` proves the immediate signal for its layer, but it does not prove the whole system is healthy. I still compare it with `{command_two}`, logs, status, or the user-facing workflow.",
            ),
            (
                "What is the first healthy signal?",
                "The first healthy signal is expected output from the checked layer. I still need a second signal or workflow validation before closing the issue.",
            ),
            (
                "What is the first failed signal?",
                "The first failed signal is output that contradicts expected behavior: missing resource, denied permission, bad status, empty result, error log, failed response, or continued user impact.",
            ),
            (
                "What is the next check after an unclear result?",
                f"After an unclear result, I move from {primary} to {secondary} or to a related status, log, config, dependency, or owner check.",
            ),
            (
                "What artifact proves this topic?",
                "The artifact can be command output, a log excerpt, dashboard signal, diagram, ticket note, pipeline result, rollback note, or sanitized screenshot tied to a clear interpretation.",
            ),
            (
                "What boundary do you state?",
                "I state whether the issue belongs to my layer, application code, security, platform, data, network, release management, or business approval.",
            ),
            (
                "What is the recovery validation?",
                "Recovery validation means the original symptom no longer appears and the checked layer shows the expected healthy signal.",
            ),
            (
                "What is the common beginner mistake?",
                "The common mistake is stopping after one command. I avoid that by checking context, confirming state, and connecting the output to the affected workflow.",
            ),
            (
                "What makes the checkpoint complete?",
                "The checkpoint is complete when I can explain the concept, run or describe the check, interpret the output, name a failure path, and state the handoff boundary.",
            ),
        ],
        "skill": [
            (
                f"What is your readiness statement for {topic_label}?",
                f"I can explain {primary}, connect it to {secondary}, read the output, identify healthy and failed states, and use the evidence in a project scenario.",
            ),
            (
                "How do you connect this skill to role/domain training?",
                "I connect it to role/domain training by using the same evidence discipline inside cloud, DevOps, SRE, data platform, and MLOps scenarios.",
            ),
            (
                "How do you use this skill during support?",
                "During support, I use the skill to confirm context, read system behavior, identify the failing layer, communicate impact, and hand off with enough evidence.",
            ),
            (
                "How do you use this skill during delivery?",
                "During delivery, I use the skill to validate changes, compare before and after state, support release confidence, and document recovery or rollback paths.",
            ),
            (
                "How do you explain this to an architect?",
                "To an architect, I explain the layer, dependency, boundary, evidence, non-functional risk, and how the signal fits into the larger system flow.",
            ),
            (
                "How do you explain this to a business analyst?",
                "To a business analyst, I connect the technical signal to workflow impact, exception path, validation proof, and the business process that was protected.",
            ),
            (
                "How do you explain this to a project manager?",
                "To a project manager, I summarize impact, owner, status, risk, next step, and whether the item blocks delivery or needs escalation.",
            ),
            (
                "What shows the skill is durable?",
                "The skill is durable when I can apply the same reasoning to a new tool, environment, or domain without losing the investigation structure.",
            ),
            (
                "What evidence proves readiness?",
                "Readiness is proven by a clean explanation, a command or artifact, a failure example, a validation result, and a handoff note.",
            ),
            (
                "What is the final enterprise-level takeaway?",
                "The takeaway is that I understand the system well enough to communicate, troubleshoot, validate, and collaborate after placement, not just repeat definitions.",
            ),
        ],
    }
    return [
        {
            "question": question,
            "answer": answer,
            "faangDepth": _training_basics_faang_depth(
                title=topic_label,
                detail_kind=detail_kind,
                primary=primary,
                secondary=secondary,
                command_one=command_one,
                command_two=command_two,
                question=question,
            ),
        }
        for question, answer in section_questions.get(detail_kind, section_questions["concept"])[:15]
    ]


def _training_basics_faang_depth(
    *,
    title: str,
    detail_kind: str,
    primary: str,
    secondary: str,
    command_one: str,
    command_two: str,
    question: str,
) -> dict[str, Any]:
    command_label = command_one if command_one != "the first relevant command" else "the most relevant status command"
    confirm_label = command_two if command_two != command_one else "a second confirming log, status, or health check"
    question_focus = question.rstrip("?").lower()
    if "Terminal, Linux, Networking" in title:
        return {
            "heading": "FAANG-style technical follow-up",
            "followUp": (
                f"For {question_focus}, I use a service-down or bad-deploy scenario and first prove the host, path, user, and runtime context before blaming the application. "
                f"For {question_focus}, the important detail is how {primary} can look correct while the active service is reading a different config, port, secret, or release path."
            ),
            "commands": [
                f"For {question_focus}, `pwd && whoami && hostname` confirms shell context, user, and host.",
                f"For {question_focus}, `ls -la` and `find ./ -maxdepth 2 -type f` confirm the release path and file ownership before I move deeper into {secondary}.",
                f"For {question_focus}, `printenv | sort`, `ss -tulpn`, `curl -i http://localhost:<port>/health`, and `journalctl -u <service> --since '15 min ago'` connect environment, port, HTTP health, and service logs.",
            ],
            "expectedSignal": f"For {question_focus}, the clean signal is that the deployed path, service user, environment values, listening port, health endpoint, and recent logs all point to the same active release.",
            "failureSignal": f"For {question_focus}, the failure signal is a mismatch: wrong directory, wrong user, missing file, absent variable, closed port, 5xx health response, or logs showing permission denied, address already in use, DNS failure, or database timeout.",
            "reasoning": (
                f"I rule out context first for {question_focus} because a clean command from the wrong host or folder creates false confidence. "
                f"For {question_focus}, I then connect process, port, HTTP response, logs, and recent deployment changes to decide whether the issue is config, runtime, network, dependency, or ownership."
            ),
            "tradeoff": f"For {question_focus}, read-only checks and targeted mitigation come before restart or rollback because restart can hide evidence and rollback can widen impact when the real issue is config, dependency, DNS, or credentials.",
            "prevention": f"The prevention step for {question_focus} is a runbook and pre-deploy validation check covering host, path, user, env, port, health endpoint, permissions, and deploy marker.",
            "sampleClose": f"I close the response to {question_focus} by naming the failing layer, the command that proved it, the validated recovery signal, and the prevention step.",
        }
    return {
        "heading": "FAANG-style technical follow-up",
        "followUp": f"For {question_focus}, I connect {title} to a concrete service, deployment, data, or platform path, then explain how {primary} and {secondary} affected the observed behavior.",
        "commands": [
            f"For {question_focus}, `{command_label}` confirms context and first signal.",
            f"For {question_focus}, `{confirm_label}` or the nearest log, event, health, metric, trace, or config check confirms the layer behind {secondary}.",
            f"For {question_focus}, correlate the {primary} signal with recent deploy, config, access, dependency, or infrastructure change.",
        ],
        "expectedSignal": f"For {question_focus}, the expected signal is the correct environment, healthy status, expected response, stable log pattern, or successful validation path.",
        "failureSignal": f"For {question_focus}, the failure signal is wrong context, empty result, denied permission, unhealthy status, unexpected response, restart loop, timeout, failed validation, or continued user impact.",
        "reasoning": (
            f"I answer {question_focus} by ruling out layers in order: context first, then {primary}, then {secondary}, then downstream dependency or owner boundary. "
            f"For {question_focus}, I do not stop at one clean signal if the user-facing workflow is still failing."
        ),
        "tradeoff": f"For {question_focus}, read-only diagnosis and mitigation come before high-impact actions such as restart, rollback, scaling, deletion, permission change, or production configuration update.",
        "prevention": f"The prevention step for {question_focus} is a clearer runbook step, alert label, validation check, owner handoff note, rollout guardrail, or automation check.",
        "sampleClose": f"I close the response to {question_focus} with the exact signal that changed, the validation result, the ownership boundary, and the prevention step.",
    }


def _training_basics_flowchart(title: str) -> list[str]:
    if "Terminal" in title:
        return ["Symptom", "Folder/context", "Process", "Port", "Logs", "Evidence note"]
    if "Git" in title:
        return ["Requirement", "Branch", "Change", "Diff", "Pull request", "Merge or rollback"]
    if "Docker" in title:
        return ["Dockerfile", "Image", "Container", "Port/env", "Logs", "Fix/rebuild"]
    if "Kubernetes" in title:
        return ["Deployment", "Pod", "Service", "Events/logs", "Rollout status", "Rollback or escalate"]
    if "Cloud Basics" in title:
        return ["Account/project", "Region", "Network", "Identity", "Compute/storage", "Backup/recovery"]
    if "CI/CD" in title:
        return ["Commit", "Build", "Test/scan", "Artifact", "Deploy", "Monitor/rollback"]
    if "Observability" in title:
        return ["User symptom", "Metric", "Log", "Trace", "Alert/runbook", "Recovery validation"]
    if "Infrastructure As Code" in title:
        return ["Requirement", "Module/config", "Validate", "Plan", "Review", "Apply or stop"]
    if "Security" in title:
        return ["Identity", "Policy", "Secret", "Access test", "Audit log", "Approval/rotation"]
    if "Agile" in title:
        return ["Story", "Acceptance criteria", "Task", "Blocker", "Validation", "Handoff"]
    if "APIs" in title:
        return ["Endpoint/config", "Request", "Response", "Data check", "Script output", "Evidence note"]
    if "Shell And Python Automation" in title:
        return ["Manual task", "Shell/Python script", "Run output", "Alert/log", "Validation", "Runbook/evidence"]
    return ["Use case", "Diagram", "Command evidence", "Runbook", "Mock answer", "Staff approval"]


def _training_basics_interview_examples(title: str) -> list[str]:
    if "Terminal" in title:
        return [
            "A service is down. What first five checks do you run from the terminal?",
            "The application works locally but fails on the server. How do you check path, process, port, env variable, and logs?",
            "You see permission denied in a startup log. What does it mean and what evidence do you collect?",
        ]
    if "Git" in title:
        return [
            "How do you explain the difference between commit, branch, pull request, merge, tag, and rollback?",
            "A bad change reached staging. How do you find the commit, review the diff, and plan rollback?",
            "What should a good pull request description include for a production change?",
        ]
    if "Docker" in title:
        return [
            "Explain image versus container versus registry in simple words.",
            "A container keeps restarting. What do you check first: logs, env variables, port mapping, image tag, or health check?",
            "The app runs in Docker locally but not in Kubernetes. What container evidence do you collect?",
        ]
    if "Kubernetes" in title:
        return [
            "A pod is in CrashLoopBackOff. What commands do you run and what do you look for?",
            "Explain deployment, pod, service, ingress, configmap, secret, and readiness probe in one flow.",
            "A rollout failed after a new image. How do you check events, logs, rollout status, and rollback?",
        ]
    if "Cloud Basics" in title:
        return [
            "Explain account or subscription, region, VPC/VNet, subnet, compute, storage, and IAM in a simple architecture.",
            "An application cannot reach storage or a database. What cloud network and identity checks do you start with?",
            "What is the difference between backup, disaster recovery, high availability, and failover at a beginner level?",
        ]
    if "CI/CD" in title:
        return [
            "Walk me through a release pipeline from commit to production monitoring.",
            "A pipeline failed. How do you identify whether it failed in build, test, scan, artifact, approval, deploy, or smoke test?",
            "How do artifact version, image tag, approval, and rollback evidence make a release safer?",
        ]
    if "Observability" in title:
        return [
            "Users say the app is slow. How do you use logs, metrics, traces, dashboard, and alert evidence?",
            "Explain latency, traffic, errors, and saturation with one incident example.",
            "How do you decide whether to rollback, scale, restart, or escalate during an incident?",
        ]
    if "Infrastructure As Code" in title:
        return [
            "What is Terraform plan and why should it be reviewed before apply?",
            "Explain desired state, state file, module, variable, output, drift, and policy in beginner language.",
            "A plan wants to destroy a resource. What do you check before approving the change?",
        ]
    if "Security" in title:
        return [
            "Where should secrets be stored, and why should they not be in code, logs, screenshots, or tickets?",
            "Explain least privilege, IAM/RBAC, service account, secret manager, audit log, and rotation.",
            "An application gets access denied. How do you check identity, role, policy, secret, and audit evidence?",
        ]
    if "Agile" in title:
        return [
            "How do you write a Jira story with acceptance criteria and definition of done?",
            "What do you say in standup when a technical task is blocked?",
            "An API returns 500 during a sprint task. How do you capture endpoint, config, SQL row count, data check, cost or risk signal, and handoff evidence?",
        ]
    if "APIs" in title:
        return [
            "An API returns 500. What do you check: endpoint, method, headers, payload, logs, timeout, dependency, or recent change?",
            "How do you validate data quality with row count, null check, duplicate check, freshness check, and group by?",
            "You are given a YAML or JSON config file. What do you inspect before saying the deployment or job is configured correctly?",
        ]
    if "Shell And Python Automation" in title:
        return [
            "When would you choose Shell scripting instead of Python for DevOps automation?",
            "Explain a health-check script, backup script, log-rotation script, API test, image scan, and rollback script at a beginner level.",
            "A script failed in production. How do you check exit code, logs, credentials, permissions, input file, API response, and rollback path?",
        ]
    return [
        "Tell me about a real use case you supported. What was the problem, your role, evidence, result, and boundary?",
        "How do you explain what you owned versus what product, developers, QA, security, data, or operations owned?",
        "Give a 60-second project story and then a 5-minute technical deep dive with diagrams and evidence.",
    ]


def _training_basics_pdf_blocks() -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []

    def add(text: Any = "", style: str = "body", *, page_break: bool = False, box: str = "") -> None:
        if page_break:
            blocks.append({"text": "", "style": "page_break"})
        value = _pdf_clean_text(text)
        if value or style == "space":
            block = {"text": value, "style": style}
            if box:
                block["box"] = box
            blocks.append(block)

    def add_list(items: Any, *, prefix: str = "-", style: str = "body") -> None:
        for item in _as_list(items):
            add(f"{prefix} {_pdf_clean_text(item)}", style)

    def add_callout(title: str, body: Any, kind: str = "key") -> None:
        add(title, "callout_title", box=kind)
        if isinstance(body, (list, tuple, set)):
            add_list(body, prefix="-", style="callout")
        else:
            add(body, "callout", box=kind)
        add("", "space")

    def add_basics_practice_appendix() -> None:
        add("Basics Interview Response Bank", "section", page_break=True)
        add(
            "This section replaces repetitive worksheet pages with speakable interview material. Each topic has one project scenario, one natural screening answer, one deeper follow-up answer, and the evidence that makes the answer credible.",
            "chapter_intro",
        )
        add_callout(
            "Master evidence template",
            [
                "Symptom or change: what happened and who was affected.",
                "System layer: file, process, network, container, cluster, cloud, pipeline, security, data, or handoff.",
                "Evidence checked: command output, log, metric, dashboard, pipeline run, config, ticket, or diagram.",
                "Interpretation: what the signal proved and what it did not prove.",
                "Result: validation, recovery, rollback, prevention, or owner handoff.",
            ],
            "key",
        )
        for module_index, module in enumerate(_training_basics_preparation_modules(), start=1):
            module_title = module["title"]
            commands = _as_list(module.get("commands"))
            examples = _as_list(module.get("interview_examples"))
            flow = _as_list(module.get("flowchart"))
            add(f"Response Bank {module_index}: {module_title}", "section", page_break=True)
            add_callout("Project scenario", module.get("drill"), "practice")
            add_callout("System flow", " -> ".join(flow[:6]), "diagram")
            if commands:
                add_callout("Evidence commands", commands[:5], "learn")
            if examples:
                add_callout("Likely interview questions", examples, "interview")
            add_callout("Natural screening answer", module.get("interview"), "interview")
            add_callout(
                "Deeper follow-up answer",
                (
                    f"In {module_title}, I start from the affected workflow, identify the system layer, and use evidence before making a change. "
                    f"The important flow is {' -> '.join(flow[:6])}. "
                    f"If the first signal is unclear, I compare it with a second signal such as logs, status, metrics, configuration, deployment output, or owner confirmation. "
                    "I close by explaining the validation result and the boundary of what I owned or handed off."
                ),
                "interview",
            )
            add_callout("Evidence that makes the answer credible", module.get("evidence_checklist"), "key")

    add("Mintel Basics Preparation", "cover")
    add("2-Week Fundamentals Bootcamp", "title")
    add("Concentrated fundamentals course for consultants before role-specific and domain-specific training.", "cover_summary")
    add("Focus: commands, core concepts, labs, failure drills, quizzes, and proof artifacts.", "cover_meta")
    add_callout(
        "Course rule",
        [
            "Concepts are learned through system behavior, not definitions alone.",
            "Commands matter when their output is interpreted correctly.",
            "Every topic connects to a scenario, a failure signal, and a validation signal.",
            "Interview answers should sound like project work: symptom, layer, evidence, result, boundary.",
        ],
        "learn",
    )
    visual = _training_basics_devops_visual_reference()
    add(visual["title"], "section", page_break=True)
    add(visual["summary"], "chapter_intro")
    add_callout("Visual flow", " -> ".join(_as_list(visual["loop"])), "key")
    add_callout("Pipeline path", " -> ".join(_as_list(visual["pipeline"])), "learn")
    add_callout("Production environment", visual["platform"], "learn")
    add_callout("Image panels", [f"{item['title']}: {item['caption']}" for item in _as_list(visual["image_panels"])], "learn")
    add_callout("Beginner notes", visual["notes"], "practice")
    add_callout("Interview notes", visual["interview_notes"], "interview")
    cicd_reference = _training_cicd_security_pipeline_reference()
    add(cicd_reference["title"], "section", page_break=True)
    add_callout("Visible image in web training", cicd_reference["imageUrl"], "diagram")
    add_callout("Where it fits", cicd_reference["whereItFits"], "key")
    add_callout("Flow to explain", cicd_reference["flow"], "learn")
    add_callout("Interview notes", cicd_reference["interviewNotes"], "interview")
    course_overview = _training_basics_course_overview()
    add(course_overview["title"], "section", page_break=True)
    add(course_overview["courseTitle"], "chapter_intro")
    add_callout("Overview", course_overview["summary"], "key")
    add_callout("Four hands-on projects", [f"{item['name']}: {item['purpose']}" for item in course_overview["projects"]], "learn")
    add_callout("What Mintel adds", course_overview["learning"], "practice")
    add_callout("Who this prepares", course_overview["audience"], "interview")
    add("12-Day Basics Prep Schedule", "section", page_break=True)
    add("This condensed schedule is designed for complete beginners. The full path is 12 days of Basics Prep followed by 4 weeks of role and domain training. Each day maps the DevOps beginner course to one practical outcome: a lab, diagram, troubleshooting note, project explanation, recruiter-screen answer, or assessment review.", "chapter_intro")
    for item in _training_basics_14_day_plan():
        add(f"Day {item['day']}: {item['focus']}", "subsection")
        add_callout("Course spine", [item["courseTitle"]] + _as_list(item["courseSections"]), "key")
        add_callout("Daily study structure", item["dailyPlan"], "practice")
        add_callout("Lab focus", item["labFocus"], "learn")
        add_callout("What this day teaches", [item["learn"], item["scenario"]], "learn")
        add_callout("Practice", item["practice"], "practice")
        add_callout("Commands and artifacts", _as_list(item["commands"])[:4], "diagram")
        add_callout("Output and readiness", [item["output"], item["readiness"]], "key")
    add("Expected Interview Questions For 5-6 Years JDs", "section", page_break=True)
    add(
        "These 20 questions cover recruiter screens and first technical screens before deeper role/domain interview rounds.",
        "chapter_intro",
    )
    for group in _training_basics_five_six_year_interview_questions():
        add(group["category"], "subsection")
        add_callout("Why this appears in screening", group["why"], "key")
        add_callout("Expected questions", group["questions"], "interview")
        add_callout("Answer model", group["answer_model"], "interview")
    add("Table Of Contents", "section")
    for module in _training_basics_preparation_modules():
        add(module["title"], "toc")
    for module in _training_basics_preparation_modules():
        add(module["title"], "section", page_break=True)
        add_callout("Fundamentals checklist", module["fundamentals"], "key")
        add_callout("Must-know terms", module["concepts"], "learn")
        add_callout("Flowchart", " -> ".join(_as_list(module["flowchart"])), "key")
        command_groups = _as_list(module.get("command_groups"))
        if command_groups:
            add("Command map", "subsection")
            for group in command_groups:
                if isinstance(group, dict):
                    add_callout(group.get("group", "Command group"), group.get("context", ""), "learn")
                    for command in _as_list(group.get("commands")):
                        if isinstance(command, dict):
                            add(f"$ {command.get('command', '')} - {command.get('meaning', '')}", "code")
        add_callout("Project scenario", module["drill"], "practice")
        add_callout("Interview examples", module["interview_examples"], "interview")
        add("Command practice page", "section", page_break=True)
        add("These commands are useful only when the output is interpreted. For each command, connect the output to context, health, failure, or next owner path.", "chapter_intro")
        add("Commands to practice", "subsection")
        for command in _as_list(module.get("commands")):
            add(f"$ {command}", "code")
        add("Failure simulation page", "section", page_break=True)
        add_callout("Failure meaning", f"In {module['title']}, a useful failure answer names the symptom, the checked layer, the signal that failed, and the validation that proved recovery or handoff.", "warning")
        add_callout("Troubleshooting rule", "Context -> command -> output -> interpretation -> validation -> owner boundary.", "key")
        add("Evidence and course gate", "section", page_break=True)
        add_callout("Evidence checklist", module["evidence_checklist"], "key")
        add_callout("Interview bridge", module["interview"], "interview")
        add("Reference notes", "section", page_break=True)
        add_callout("Theory reference", module["theory"], "learn")
        add_callout("Mental model reference", module["mental_model"], "key")
        add_callout("Mini project", module["mini_project"], "learn")
    add_basics_practice_appendix()
    return blocks


def _training_beginner_cards(program: TrainingProgram) -> list[dict[str, Any]]:
    role = program.marketing_role
    tools = _split_training_items(role.common_tools)[:5]
    tool_text = ", ".join(tools) or role.name
    domain = program.industry_domain
    return [
        {
            "step": "1",
            "title": "See the movie first",
            "subtitle": "Before memorizing terms, understand the story.",
            "bullets": [
                f"You are learning how a {role.name} helps a {domain} team deliver and support real systems.",
                "Start with users, applications, failures, releases, and business impact.",
                "Then place tools inside that story, one tool at a time.",
            ],
            "try_this": "Explain the whole project in 45 seconds without using tool names.",
            "href": f"/training-programs/{program.id}?section=overview",
        },
        {
            "step": "2",
            "title": "Draw it badly, then improve it",
            "subtitle": "A rough diagram beats a perfect paragraph.",
            "bullets": [
                "Draw user or trigger, application, platform, data, deployment, monitoring, and support boundary.",
                f"Mark exactly where the {role.name} contributes.",
                "Circle the top two places where things can fail.",
            ],
            "try_this": "Redraw the same diagram from memory after reading one use case.",
            "href": f"/training-programs/{program.id}?section=architecture",
        },
        {
            "step": "3",
            "title": "Touch the workflow",
            "subtitle": "Every concept needs a small action.",
            "bullets": [
                f"Use {tool_text} in a small lab or simulation.",
                "Capture one screenshot, command output, dashboard, runbook note, or validation result.",
                "Write what broke, how you checked it, and how you proved the fix.",
            ],
            "try_this": "Create one Build-Break-Fix note for a small failure.",
            "href": "/training-basics",
        },
        {
            "step": "4",
            "title": "Talk like a consultant",
            "subtitle": "Interview answers sound human and specific.",
            "bullets": [
                "Use short sentences: situation, action, evidence, result.",
                "Say what you owned and what another team owned.",
                "Practice both a 60-second answer and a 5-minute deep dive.",
            ],
            "try_this": "Record one answer, then remove vague phrases like 'worked on' and 'helped with'.",
            "href": f"/training-programs/{program.id}?section=interview",
        },
        {
            "step": "5",
            "title": "Prove before marketing",
            "subtitle": "Resume bullets need evidence behind them.",
            "bullets": [
                "Connect each resume bullet to a diagram, workflow, screenshot, command output, or runbook.",
                "The final evidence package becomes the readiness proof.",
                "If you cannot explain it, do not market it heavily yet.",
            ],
            "try_this": "Pick one resume bullet and name the artifact that proves it.",
            "href": f"/training-programs/{program.id}?section=resume",
        },
    ]


def _training_beginner_story_steps(program: TrainingProgram) -> list[dict[str, str]]:
    role = program.marketing_role.name
    domain = program.industry_domain
    return [
        {"label": "The business wants something", "text": f"A {domain} team needs a release, platform improvement, automation, data flow, reliability fix, or AI/ML capability."},
        {"label": "The system has moving parts", "text": "Applications, cloud services, pipelines, databases, monitoring, access, tickets, and people all interact."},
        {"label": "Something can go wrong", "text": "A deployment fails, an alert fires, data is late, access is missing, cost rises, performance drops, or validation is unclear."},
        {"label": f"The {role} has a lane", "text": "I investigate, implement, validate, document, communicate, and escalate within a clear ownership boundary."},
        {"label": "Evidence makes it real", "text": "The final story is backed by diagrams, outputs, screenshots, runbooks, Jira stories, and concise explanations."},
    ]


def _training_beginner_quiz(program: TrainingProgram) -> list[dict[str, str]]:
    role = program.marketing_role.name
    return [
        {
            "question": f"If someone asks 'What did you actually do as a {role}?', what should your answer include?",
            "answer": "Business context, exact ownership, tools used, validation evidence, support boundary, and measurable or practical outcome.",
        },
        {
            "question": "Why draw workflow and architecture diagrams before writing resume bullets?",
            "answer": "Diagrams expose the real system flow and prevent vague or inflated claims.",
        },
        {
            "question": "What is a weak interview answer signal?",
            "answer": "Only naming tools without explaining input, output, failure handling, validation, or team ownership.",
        },
        {
            "question": "What makes a consultant market-ready in Mintel?",
            "answer": "Role/domain work is explainable with evidence, scenario answers, and resume bullets connected to artifacts.",
        },
    ]


def _training_provider_diagram_cards(program: TrainingProgram) -> list[dict[str, Any]]:
    role_name = program.marketing_role.name
    domain_name = program.industry_domain
    role_diagrams: dict[str, list[dict[str, Any]]] = {
        "Cloud Platform Engineer": [
            {
                "provider": "AWS",
                "title": "AWS Landing Zone And Private Application Path",
                "source": "AWS architecture learning path: account/VPC boundary, private subnets, endpoint access, load balancing, managed compute, observability, and cost controls.",
                "nodes": [
                    {"label": "Users / partners", "items": [domain_name, "DNS", "WAF"], "kind": "problem"},
                    {"label": "Edge and ingress", "items": ["Route 53", "CloudFront", "ALB / API Gateway"]},
                    {"label": "Private VPC", "items": ["Public/private subnets", "Security groups", "VPC endpoints"], "kind": "role"},
                    {"label": "Runtime", "items": ["EKS / ECS / Lambda", "EC2 workers", "Auto Scaling"]},
                    {"label": "Data and async", "items": ["RDS / DynamoDB", "S3", "SQS / SNS"]},
                    {"label": "Operations", "items": ["IAM", "CloudWatch", "Config / Cost Explorer"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Network path", "text": "Explain public entry, private workload access, route tables, endpoints, and security group boundaries."},
                    {"label": "Resilience", "text": "Explain multi-AZ placement, target health, scaling policy, and dependency failure behavior."},
                    {"label": "Governance", "text": "Explain IAM roles, tags, Config rules, approved regions, cost owner, and evidence review."},
                ],
                "evidence": ["VPC diagram", "endpoint connectivity test", "load balancer health", "IAM policy", "cost/tag report"],
                "interview": "This diagram explains how a platform engineer makes cloud access private, resilient, governed, and supportable.",
            },
            {
                "provider": "Azure",
                "title": "Azure Hub-Spoke Platform And Private Endpoint Pattern",
                "source": "AZ-305 architecture path: subscription structure, hub-spoke VNet, Private Link, managed identity, Azure Policy, Monitor, and migration connectivity.",
                "nodes": [
                    {"label": "Enterprise tenant", "items": ["Management group", "Subscription", "Resource groups"], "kind": "problem"},
                    {"label": "Hub VNet", "items": ["Firewall", "VPN / ExpressRoute", "DNS"]},
                    {"label": "Spoke VNet", "items": ["App subnet", "Data subnet", "Private endpoints"], "kind": "role"},
                    {"label": "Workloads", "items": ["AKS / App Service", "Functions", "VM scale sets"]},
                    {"label": "Managed services", "items": ["Storage", "SQL / Cosmos DB", "Key Vault"]},
                    {"label": "Governance", "items": ["Managed identity", "Azure Policy", "Azure Monitor"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Landing zone", "text": "Explain subscription/resource group design, environment separation, and ownership tagging."},
                    {"label": "Private access", "text": "Explain Private Link, DNS, firewall, route table, and hybrid connectivity evidence."},
                    {"label": "Controls", "text": "Explain managed identity, Key Vault access, policy compliance, and monitor alerts."},
                ],
                "evidence": ["hub-spoke diagram", "Private Link DNS check", "managed identity access test", "policy compliance", "Monitor dashboard"],
                "interview": "This diagram explains Azure enterprise platform work as platform ownership rather than feature development.",
            },
            {
                "provider": "GCP",
                "title": "GCP Project, VPC, Private Service Access Pattern",
                "source": "Google Cloud architecture path: folders/projects, IAM/service accounts, VPC, private access, load balancing, managed compute, logging, and SRE evidence.",
                "nodes": [
                    {"label": "Organization", "items": ["Folder", "Project", "Billing"], "kind": "problem"},
                    {"label": "Global access", "items": ["Cloud DNS", "Cloud CDN", "Load Balancer"]},
                    {"label": "Private VPC", "items": ["Subnets", "Firewall rules", "Private Service Connect"], "kind": "role"},
                    {"label": "Runtime", "items": ["GKE / Cloud Run", "Compute Engine", "Cloud Functions"]},
                    {"label": "Data and events", "items": ["Cloud SQL / Spanner", "Cloud Storage", "Pub/Sub"]},
                    {"label": "Operations", "items": ["IAM", "Cloud Logging", "Monitoring / SLO"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Project model", "text": "Explain project isolation, service accounts, APIs, billing, and ownership boundary."},
                    {"label": "Connectivity", "text": "Explain private service access, firewall rules, load balancer path, and validation."},
                    {"label": "Reliability", "text": "Explain logs, metrics, SLOs, and operational handoff."},
                ],
                "evidence": ["project/VPC diagram", "firewall or PSC test", "service account policy", "Logging query", "SLO dashboard"],
                "interview": "This diagram supports platform questions around GCP project setup, private access, and support evidence.",
            },
        ],
        "Data Platform Engineer": [
            {
                "provider": "AWS",
                "title": "AWS Data Lake And Analytics Pipeline",
                "source": "AWS Data Engineer path: S3 landing zones, Glue crawlers/catalog, Lake Formation governance, Athena/Redshift query, orchestration, and data quality evidence.",
                "nodes": [
                    {"label": "Sources", "items": ["Application DB", "Files", "Events"], "kind": "problem"},
                    {"label": "Ingest", "items": ["DMS / DataSync", "Kinesis", "S3 raw"]},
                    {"label": "Catalog and govern", "items": ["Glue Crawler", "Data Catalog", "Lake Formation"], "kind": "role"},
                    {"label": "Transform", "items": ["Glue ETL", "Step Functions", "Lambda"]},
                    {"label": "Serve", "items": ["Athena", "Redshift", "QuickSight / BI"]},
                    {"label": "Operate", "items": ["CloudWatch", "DQ checks", "Reconciliation"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Source-to-consumer", "text": "Explain raw, curated, and serving zones; schema, partitioning, and downstream consumers."},
                    {"label": "Governance", "text": "Explain Lake Formation permissions, row/column controls, audit, and exception handling."},
                    {"label": "Reliability", "text": "Explain freshness, row count, failed-job recovery, replay/backfill, and cost/runtime improvement."},
                ],
                "evidence": ["Glue crawler table", "partition/schema note", "Athena query result", "row-count reconciliation", "failed-run recovery"],
                "interview": "This diagram explains a data platform story from source to report, not only tool names.",
            },
            {
                "provider": "Azure",
                "title": "Azure Data Factory, Lake, Synapse Analytics Pattern",
                "source": "Azure data architecture path: Data Factory orchestration, ADLS Gen2 zones, Databricks/Synapse transform, Purview/governance, and Monitor evidence.",
                "nodes": [
                    {"label": "Sources", "items": ["SQL / APIs", "Files", "SaaS"], "kind": "problem"},
                    {"label": "Orchestrate", "items": ["Data Factory", "Triggers", "Integration Runtime"]},
                    {"label": "Lake zones", "items": ["ADLS raw", "curated", "published"], "kind": "role"},
                    {"label": "Transform", "items": ["Databricks", "Synapse Spark", "Mapping Data Flow"]},
                    {"label": "Serve", "items": ["Synapse SQL", "Power BI", "Data marts"]},
                    {"label": "Operate", "items": ["Purview", "Key Vault", "Azure Monitor"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Pipeline control", "text": "Explain trigger, activity, dependency, retry, parameter, and failed-run recovery."},
                    {"label": "Security", "text": "Explain managed identity, Key Vault, storage access, and data classification."},
                    {"label": "Analytics", "text": "Explain lake-to-warehouse movement, query consumers, and validation evidence."},
                ],
                "evidence": ["ADF run", "ADLS folder layout", "Synapse query", "Purview lineage", "Monitor alert"],
                "interview": "This diagram supports Azure data pipeline and warehouse modernization questions.",
            },
            {
                "provider": "GCP",
                "title": "GCP Pub/Sub, Dataflow, BigQuery Analytics Pattern",
                "source": "GCP Data Engineer path: Pub/Sub ingestion, Dataflow batch/stream processing, Composer orchestration, BigQuery serving, and pipeline monitoring.",
                "nodes": [
                    {"label": "Sources", "items": ["Events", "Cloud Storage", "Databases"], "kind": "problem"},
                    {"label": "Ingest", "items": ["Pub/Sub", "Storage Transfer", "Datastream"]},
                    {"label": "Process", "items": ["Dataflow", "Apache Beam", "Dataproc"], "kind": "role"},
                    {"label": "Orchestrate", "items": ["Cloud Composer", "Scheduler", "Workflows"]},
                    {"label": "Serve", "items": ["BigQuery", "Looker", "Data marts"]},
                    {"label": "Operate", "items": ["Cloud Logging", "Data quality", "Replay/backfill"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Streaming", "text": "Explain Pub/Sub topic/subscription, watermarking, dedupe, replay, and latency."},
                    {"label": "Warehouse", "text": "Explain BigQuery partitioning, clustering, scheduled queries, and cost-aware query design."},
                    {"label": "Operations", "text": "Explain failed step, bad record sample, corrected run, and stakeholder update."},
                ],
                "evidence": ["Dataflow job graph", "Pub/Sub backlog", "BigQuery table/query", "Composer DAG run", "freshness dashboard"],
                "interview": "This diagram supports GCP data platform questions with a source-to-consumer flow.",
            },
        ],
        "DevOps Engineer": [
            {
                "provider": "AWS",
                "title": "AWS Event-Driven Release And Worker Pattern",
                "source": "AWS SA/SysOps path: S3 events, Lambda, SQS/DLQ, API Gateway, CI/CD, CloudWatch, IAM, and rollback evidence.",
                "nodes": [
                    {"label": "Trigger", "items": ["Git push", "API call", "S3 upload"], "kind": "problem"},
                    {"label": "Build/release", "items": ["CodePipeline", "CodeBuild", "Artifact", "Approval"]},
                    {"label": "Event path", "items": ["S3 Event", "EventBridge", "SQS / DLQ"], "kind": "role"},
                    {"label": "Runtime", "items": ["Lambda", "ECS/EKS", "API Gateway"]},
                    {"label": "Config/security", "items": ["IAM role", "Secrets Manager", "Parameter Store"]},
                    {"label": "Operate", "items": ["CloudWatch logs", "alarms", "rollback runbook"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Release", "text": "Explain source, build, artifact, approval, deployment, smoke check, and rollback."},
                    {"label": "Async safety", "text": "Explain queue depth, retry, DLQ, idempotency, and failed-event recovery."},
                    {"label": "Evidence", "text": "Explain pipeline output, event invocation log, alarm, and change ticket."},
                ],
                "evidence": ["pipeline run", "Lambda invocation log", "SQS/DLQ dashboard", "smoke test", "rollback note"],
                "interview": "This diagram supports CI/CD, serverless, queue, and production change questions.",
            },
            {
                "provider": "Azure",
                "title": "Azure DevOps Release, Secrets, And App Runtime Pattern",
                "source": "Microsoft Learn DevOps/Azure architecture: Azure Repos/Pipelines, artifacts, App Service/AKS/Functions, Key Vault, managed identity, Monitor.",
                "nodes": [
                    {"label": "Source", "items": ["Azure Repos / GitHub", "Pull request", "Branch policy"], "kind": "problem"},
                    {"label": "Pipeline", "items": ["Build", "Test", "Artifact", "Approval"]},
                    {"label": "Deploy", "items": ["AKS", "App Service", "Functions"], "kind": "role"},
                    {"label": "Config", "items": ["Key Vault", "Managed identity", "App settings"]},
                    {"label": "Traffic", "items": ["App Gateway", "API Management", "Private endpoint"]},
                    {"label": "Operate", "items": ["Azure Monitor", "Log Analytics", "release notes"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Controls", "text": "Explain branch policy, approval, secret access, and environment promotion."},
                    {"label": "Deployment", "text": "Explain artifact version, Helm/app config, rollout status, and rollback path."},
                    {"label": "Validation", "text": "Explain smoke test, Monitor query, incident note, and support handoff."},
                ],
                "evidence": ["pipeline run", "artifact version", "Key Vault access note", "Monitor query", "deployment history"],
                "interview": "This diagram explains Azure release work with secret handling and production validation.",
            },
            {
                "provider": "GCP",
                "title": "GCP Cloud Build, Artifact Registry, Cloud Run/GKE Pattern",
                "source": "Google Cloud DevOps path: Cloud Build, Artifact Registry, IAM/service accounts, Cloud Run or GKE, Cloud Deploy, logging, and rollback.",
                "nodes": [
                    {"label": "Source", "items": ["GitHub / CSR", "Pull request", "Trigger"], "kind": "problem"},
                    {"label": "Build", "items": ["Cloud Build", "Tests", "Container image"]},
                    {"label": "Registry", "items": ["Artifact Registry", "Vulnerability scan", "Version tag"], "kind": "role"},
                    {"label": "Deploy", "items": ["Cloud Run", "GKE", "Cloud Deploy"]},
                    {"label": "Config", "items": ["Secret Manager", "Service account", "IAM"]},
                    {"label": "Operate", "items": ["Cloud Logging", "Monitoring", "rollback"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Supply chain", "text": "Explain source trigger, image build, scan, tag, and promotion."},
                    {"label": "Runtime", "text": "Explain Cloud Run/GKE rollout, traffic split, health, and rollback."},
                    {"label": "Support", "text": "Explain logs, metrics, service account boundary, and incident handoff."},
                ],
                "evidence": ["Cloud Build run", "image tag", "deployment revision", "Logging query", "rollback validation"],
                "interview": "This diagram supports GCP CI/CD and container deployment questions.",
            },
        ],
        "MLOps / AI Platform Engineer": [
            {
                "provider": "AWS",
                "title": "AWS SageMaker Model Lifecycle Pattern",
                "source": "AWS ML Specialty path: feature/data prep, SageMaker training, evaluation, registry, endpoint/batch inference, monitoring, and retraining.",
                "nodes": [
                    {"label": "Data", "items": ["S3", "Glue Catalog", "Feature pipeline"], "kind": "problem"},
                    {"label": "Train", "items": ["SageMaker Training", "Experiments", "Metrics"]},
                    {"label": "Approve", "items": ["Model Registry", "Evaluation report", "Stage gate"], "kind": "role"},
                    {"label": "Deploy", "items": ["Endpoint", "Batch Transform", "Canary/rollback"]},
                    {"label": "Monitor", "items": ["Model Monitor", "CloudWatch", "Drift/quality"]},
                    {"label": "Improve", "items": ["Retraining trigger", "RCA", "version notes"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Lifecycle", "text": "Explain data, training, evaluation, registry, deployment, monitoring, and retraining."},
                    {"label": "Production risk", "text": "Explain stale features, drift, endpoint latency, wrong predictions, and rollback."},
                    {"label": "Evidence", "text": "Explain model version, metrics, approval, endpoint health, and monitor output."},
                ],
                "evidence": ["training job", "metric report", "registry version", "endpoint latency", "drift dashboard"],
                "interview": "This diagram explains MLOps as a production system, not a notebook exercise.",
            },
            {
                "provider": "Azure",
                "title": "Azure ML Pipeline, Registry, Endpoint Pattern",
                "source": "Azure AI architecture path: data assets, ML pipeline, experiment tracking, model registry, managed online/batch endpoints, Monitor, and responsible controls.",
                "nodes": [
                    {"label": "Data assets", "items": ["ADLS / SQL", "Feature data", "Data version"], "kind": "problem"},
                    {"label": "Pipeline", "items": ["Azure ML job", "Training", "Evaluation"]},
                    {"label": "Registry", "items": ["Model registry", "Approval", "Environment"], "kind": "role"},
                    {"label": "Deploy", "items": ["Managed endpoint", "Batch endpoint", "Blue/green"]},
                    {"label": "Observe", "items": ["Azure Monitor", "App Insights", "data/model drift"]},
                    {"label": "Govern", "items": ["Managed identity", "Key Vault", "audit"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Repeatability", "text": "Explain pipeline components, environments, model version, and deployment stage."},
                    {"label": "Endpoint ops", "text": "Explain latency, error rate, traffic split, rollback, and batch output validation."},
                    {"label": "Governance", "text": "Explain identity, secrets, audit, and responsible monitoring controls."},
                ],
                "evidence": ["Azure ML pipeline run", "model version", "endpoint metrics", "batch output count", "approval/audit note"],
                "interview": "This diagram supports Azure ML platform and endpoint operations questions.",
            },
            {
                "provider": "GCP",
                "title": "GCP Vertex AI Pipeline And Prediction Pattern",
                "source": "Vertex AI learning path: pipelines, custom training, model registry, endpoints, batch prediction, model monitoring, and BigQuery/Feature Store integration.",
                "nodes": [
                    {"label": "Data", "items": ["BigQuery", "Cloud Storage", "features"], "kind": "problem"},
                    {"label": "Pipeline", "items": ["Vertex AI Pipelines", "Custom training", "Evaluation"]},
                    {"label": "Registry", "items": ["Model Registry", "Version", "Approval"], "kind": "role"},
                    {"label": "Prediction", "items": ["Endpoint", "Batch prediction", "traffic split"]},
                    {"label": "Monitor", "items": ["Model Monitoring", "Logging", "drift/skew"]},
                    {"label": "Retrain", "items": ["Trigger", "RCA", "new candidate"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Data-to-model", "text": "Explain BigQuery/features, pipeline steps, training artifacts, and evaluation metrics."},
                    {"label": "Deploy", "text": "Explain endpoint configuration, traffic split, canary, rollback, and batch validation."},
                    {"label": "Monitor", "text": "Explain skew, drift, logging, alerts, and retraining decision."},
                ],
                "evidence": ["Vertex pipeline run", "model registry entry", "endpoint traffic split", "monitoring alert", "retraining note"],
                "interview": "This diagram supports Vertex AI lifecycle, deployment, and monitoring questions.",
            },
        ],
        "Site Reliability / AIOps Engineer": [
            {
                "provider": "AWS",
                "title": "AWS Observability, Incident, And Recovery Pattern",
                "source": "AWS SysOps path: CloudWatch metrics/logs/alarms, X-Ray traces, EventBridge, Auto Scaling, load balancer health, and RCA evidence.",
                "nodes": [
                    {"label": "User impact", "items": ["5xx", "latency", "failed job"], "kind": "problem"},
                    {"label": "Signals", "items": ["CloudWatch metrics", "logs", "X-Ray trace"]},
                    {"label": "Correlation", "items": ["Recent deploy", "ALB target health", "Auto Scaling"], "kind": "role"},
                    {"label": "Mitigation", "items": ["rollback", "scale", "reroute / retry"]},
                    {"label": "Validate", "items": ["error rate", "latency", "synthetic test"]},
                    {"label": "Prevent", "items": ["RCA", "alarm tuning", "runbook"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Impact first", "text": "Explain who is affected and which user journey or SLO is broken."},
                    {"label": "Evidence", "text": "Explain logs, metrics, traces, deployment markers, target health, and scaling signals."},
                    {"label": "Recovery", "text": "Explain mitigation, validation, RCA, owner, and prevention follow-up."},
                ],
                "evidence": ["CloudWatch alarm", "log query", "trace sample", "target health", "RCA/runbook update"],
                "interview": "This diagram supports production incident, SLO, and troubleshooting simulations.",
            },
            {
                "provider": "Azure",
                "title": "Azure Monitor And Network Watcher Troubleshooting Pattern",
                "source": "Microsoft Learn operations path: Azure Monitor, Log Analytics, Application Insights, Network Watcher, NSG flow/IP checks, Action Groups, and incident response.",
                "nodes": [
                    {"label": "Symptom", "items": ["timeout", "failed dependency", "high latency"], "kind": "problem"},
                    {"label": "Application signals", "items": ["App Insights", "Log Analytics", "metrics"]},
                    {"label": "Network checks", "items": ["Network Watcher", "NSG flow", "route / next hop"], "kind": "role"},
                    {"label": "Alert route", "items": ["Azure Monitor", "Action Group", "ticket"]},
                    {"label": "Recovery", "items": ["config fix", "rollback", "owner escalation"]},
                    {"label": "RCA", "items": ["timeline", "prevention", "runbook"], "kind": "support"},
                ],
                "layers": [
                    {"label": "Application", "text": "Explain request failures, dependency map, logs, metrics, and user impact."},
                    {"label": "Network", "text": "Explain IP flow verify, next hop, route table, NSG, firewall, and private endpoint checks."},
                    {"label": "Operations", "text": "Explain alert routing, owner escalation, recovery validation, and post-incident action."},
                ],
                "evidence": ["Log Analytics query", "App Insights dependency map", "Network Watcher result", "Action Group route", "RCA"],
                "interview": "This diagram supports Azure production troubleshooting and network-path questions.",
            },
            {
                "provider": "GCP",
                "title": "GCP SLO, Trace, Logging, And Incident Pattern",
                "source": "Google Cloud operations/SRE path: Cloud Monitoring, Logging, Trace, Error Reporting, SLO dashboards, alert policies, and incident review.",
                "nodes": [
                    {"label": "Service journey", "items": ["availability", "latency", "error budget"], "kind": "problem"},
                    {"label": "Telemetry", "items": ["Cloud Monitoring", "Logging", "Trace"]},
                    {"label": "Diagnosis", "items": ["dependency latency", "error group", "recent deploy"], "kind": "role"},
                    {"label": "Incident", "items": ["alert policy", "notification", "owner"]},
                    {"label": "Recovery", "items": ["rollback", "scale", "dependency fix"]},
                    {"label": "Learning", "items": ["postmortem", "SLO review", "runbook"], "kind": "support"},
                ],
                "layers": [
                    {"label": "SLO", "text": "Explain SLI, target, error budget, burn rate, and user journey."},
                    {"label": "Traceability", "text": "Explain request trace, logs, error groups, and dependency bottleneck."},
                    {"label": "Improvement", "text": "Explain prevention task, alert tuning, toil reduction, and support handoff."},
                ],
                "evidence": ["SLO dashboard", "trace waterfall", "log query", "alert policy", "postmortem/runbook"],
                "interview": "This diagram supports SRE/AIOps questions with SLO and trace evidence.",
            },
        ],
    }
    diagrams = role_diagrams.get(role_name, role_diagrams["Cloud Platform Engineer"])
    for diagram in diagrams:
        provider = str(diagram.get("provider", ""))
        title = str(diagram.get("title", ""))
        diagram["sourceUrl"] = _official_provider_docs_url(provider)
        diagram["sourceLinks"] = _official_provider_doc_links(provider, title)
    return diagrams


def _official_provider_docs_url(provider: str) -> str:
    return {
        "AWS": "https://docs.aws.amazon.com/",
        "Azure": "https://learn.microsoft.com/en-us/azure/",
        "GCP": "https://docs.cloud.google.com/docs",
    }.get(provider, "")


def _official_provider_doc_links(provider: str, title: str) -> list[dict[str, str]]:
    lower = title.lower()
    if provider == "AWS":
        links = [{"label": "AWS Documentation", "url": "https://docs.aws.amazon.com/"}]
        if "data lake" in lower:
            links.extend(
                [
                    {"label": "AWS Glue", "url": "https://docs.aws.amazon.com/glue/"},
                    {"label": "Amazon Athena", "url": "https://docs.aws.amazon.com/athena/"},
                    {"label": "AWS Lake Formation", "url": "https://docs.aws.amazon.com/lake-formation/"},
                ]
            )
        elif "sagemaker" in lower:
            links.append({"label": "Amazon SageMaker", "url": "https://docs.aws.amazon.com/sagemaker/"})
        elif "observability" in lower:
            links.extend(
                [
                    {"label": "Amazon CloudWatch", "url": "https://docs.aws.amazon.com/cloudwatch/"},
                    {"label": "AWS X-Ray", "url": "https://docs.aws.amazon.com/xray/"},
                ]
            )
        elif "event-driven" in lower:
            links.extend(
                [
                    {"label": "AWS Lambda", "url": "https://docs.aws.amazon.com/lambda/"},
                    {"label": "Amazon SQS", "url": "https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/"},
                    {"label": "Amazon EventBridge", "url": "https://docs.aws.amazon.com/eventbridge/"},
                ]
            )
        else:
            links.extend(
                [
                    {"label": "Amazon VPC", "url": "https://docs.aws.amazon.com/vpc/"},
                    {"label": "Elastic Load Balancing", "url": "https://docs.aws.amazon.com/elasticloadbalancing/"},
                    {"label": "AWS IAM", "url": "https://docs.aws.amazon.com/iam/"},
                ]
            )
        return links
    if provider == "GCP":
        links = [{"label": "Google Cloud Documentation", "url": "https://docs.cloud.google.com/docs"}]
        if "pub/sub" in lower or "bigquery" in lower:
            links.extend(
                [
                    {"label": "Pub/Sub", "url": "https://cloud.google.com/pubsub/docs"},
                    {"label": "Dataflow", "url": "https://cloud.google.com/dataflow/docs"},
                    {"label": "BigQuery", "url": "https://cloud.google.com/bigquery/docs"},
                ]
            )
        elif "vertex" in lower:
            links.append({"label": "Vertex AI", "url": "https://cloud.google.com/vertex-ai/docs"})
        elif "slo" in lower or "trace" in lower:
            links.extend(
                [
                    {"label": "Cloud Monitoring", "url": "https://cloud.google.com/monitoring/docs"},
                    {"label": "Cloud Logging", "url": "https://cloud.google.com/logging/docs"},
                    {"label": "Cloud Trace", "url": "https://cloud.google.com/trace/docs"},
                ]
            )
        elif "cloud build" in lower:
            links.extend(
                [
                    {"label": "Cloud Build", "url": "https://cloud.google.com/build/docs"},
                    {"label": "Artifact Registry", "url": "https://cloud.google.com/artifact-registry/docs"},
                    {"label": "Cloud Deploy", "url": "https://cloud.google.com/deploy/docs"},
                ]
            )
        else:
            links.extend(
                [
                    {"label": "Google Kubernetes Engine", "url": "https://cloud.google.com/kubernetes-engine/docs"},
                    {"label": "Cloud Load Balancing", "url": "https://cloud.google.com/load-balancing/docs"},
                    {"label": "Private Service Connect", "url": "https://cloud.google.com/vpc/docs/private-service-connect"},
                ]
            )
        return links
    if provider == "Azure":
        links = [{"label": "Azure documentation", "url": "https://learn.microsoft.com/en-us/azure/"}]
        if "data factory" in lower or "synapse" in lower:
            links.extend(
                [
                    {"label": "Azure Data Factory", "url": "https://learn.microsoft.com/en-us/azure/data-factory/"},
                    {"label": "Azure Synapse Analytics", "url": "https://learn.microsoft.com/en-us/azure/synapse-analytics/"},
                    {"label": "Azure Data Lake Storage", "url": "https://learn.microsoft.com/en-us/azure/storage/blobs/data-lake-storage-introduction"},
                ]
            )
        elif "azure ml" in lower:
            links.append({"label": "Azure Machine Learning", "url": "https://learn.microsoft.com/en-us/azure/machine-learning/"})
        elif "monitor" in lower or "network watcher" in lower:
            links.extend(
                [
                    {"label": "Azure Monitor", "url": "https://learn.microsoft.com/en-us/azure/azure-monitor/"},
                    {"label": "Application Insights", "url": "https://learn.microsoft.com/en-us/azure/azure-monitor/app/app-insights-overview"},
                    {"label": "Network Watcher", "url": "https://learn.microsoft.com/en-us/azure/network-watcher/"},
                ]
            )
        elif "devops" in lower:
            links.extend(
                [
                    {"label": "Azure Pipelines", "url": "https://learn.microsoft.com/en-us/azure/devops/pipelines/"},
                    {"label": "Azure Key Vault", "url": "https://learn.microsoft.com/en-us/azure/key-vault/"},
                ]
            )
        else:
            links.extend(
                [
                    {"label": "Azure landing zones", "url": "https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ready/landing-zone/"},
                    {"label": "Azure Virtual Network", "url": "https://learn.microsoft.com/en-us/azure/virtual-network/"},
                    {"label": "Azure Private Link", "url": "https://learn.microsoft.com/en-us/azure/private-link/"},
                ]
            )
        return links
    return []


def _training_architecture_diagram_cards(program: TrainingProgram) -> list[dict[str, Any]]:
    role_name = program.marketing_role.name
    domain_name = program.industry_domain
    architecture = program.cloud_architecture or {}
    systems = _as_list(architecture.get("applicationLandscape") or program.application_landscape)[:5]
    systems_text = ", ".join(str(item) for item in systems) or "domain systems"
    tools = _split_training_items(program.marketing_role.common_tools)[:6]
    tools_text = ", ".join(tools) or role_name
    return [
        {
            "title": f"{role_name} Operating Architecture",
            "purpose": "Shows where the role fits inside business, application, platform, data, security, and support boundaries.",
            "nodes": [
                {"label": "Business need", "items": [domain_name, "Requirement", "User/business impact"], "kind": "problem"},
                {"label": "Product systems", "items": systems[:3] or ["Application", "API", "Data flow"]},
                {"label": role_name, "items": ["Own lane", "Coordinate", "Validate", "Document"], "kind": "role"},
                {"label": "Support proof", "items": ["Monitoring", "Logs", "Runbook", "Evidence"], "kind": "support"},
            ],
            "layers": [
                {"label": "Business", "text": f"{domain_name} process, users, SLA, compliance, and delivery priority."},
                {"label": "Application", "text": f"Main systems: {systems_text}."},
                {"label": "Platform", "text": f"Role tools and services: {tools_text}."},
                {"label": "Controls", "text": "Security, access, approvals, data handling, release controls, and ownership boundaries."},
                {"label": "Operations", "text": "Monitoring, troubleshooting, rollback, handoff, RCA, and prevention notes."},
            ],
            "evidence": ["Architecture diagram", "Ownership boundary", "System list", "Monitoring or validation proof"],
            "interview": "This diagram supports questions about where the work fit, what the role owned, which teams were involved, and how the outcome was validated.",
        },
        {
            "title": f"{role_name} Delivery Workflow",
            "purpose": "Shows how work moves from requirement to implementation, validation, release, and review evidence.",
            "nodes": [
                {"label": "Requirement", "items": ["JD signal", "Ticket", "Acceptance criteria"], "kind": "problem"},
                {"label": "Build/change", "items": ["Config", "Code", "Pipeline", "Automation"]},
                {"label": "Validate", "items": ["Test", "Log", "Dashboard", "Peer review"], "kind": "role"},
                {"label": "Release evidence", "items": ["Output", "Runbook", "Project story", "Interview answer"], "kind": "support"},
            ],
            "layers": [
                {"label": "Input", "text": "Requirement, ticket, environment, access, dependency, and expected result."},
                {"label": "Execution", "text": "Implementation steps, tool configuration, pipeline or workflow run."},
                {"label": "Validation", "text": "Test result, monitoring signal, log check, data quality check, or deployment status."},
                {"label": "Handoff", "text": "Runbook, release note, known issue, escalation owner, and next action."},
                {"label": "Positioning", "text": "Resume bullet and interview story stay tied to visible evidence."},
            ],
            "evidence": ["Ticket/Jira story", "Pipeline output", "Validation screenshot", "Release or handoff note"],
            "interview": "This diagram supports project deep-dive questions by showing the engineering process behind the work.",
        },
        {
            "title": f"{role_name} Incident And Troubleshooting Flow",
            "purpose": "Shows how to diagnose a failure without jumping to random fixes.",
            "nodes": [
                {"label": "Symptom", "items": ["Alert", "User issue", "Failed job", "Slow system"], "kind": "problem"},
                {"label": "Evidence check", "items": ["Logs", "Metrics", "Trace", "Pipeline status"]},
                {"label": "Triage lane", "items": ["Role owner", "Escalation", "Fix path", "Rollback"], "kind": "role"},
                {"label": "Prevention", "items": ["RCA", "Runbook", "Monitor", "Follow-up story"], "kind": "support"},
            ],
            "layers": [
                {"label": "Detect", "text": "What changed, who is impacted, and how urgent is it?"},
                {"label": "Isolate", "text": "Which layer failed: app, network, data, identity, pipeline, platform, or external dependency?"},
                {"label": "Act", "text": "Apply fix, rollback, restart, rerun, change config, or route to correct owner with evidence."},
                {"label": "Validate", "text": "Confirm recovery using metric, log, test, data freshness, deployment status, or user confirmation."},
                {"label": "Prevent", "text": "Update alert, runbook, monitoring, guardrail, automation, or handoff note."},
            ],
            "evidence": ["Incident timeline", "Before/after signal", "RCA note", "Runbook update"],
            "interview": "This diagram supports scenario interviews: failed deployment, broken pipeline, noisy alert, stale data, access issue, or production incident.",
        },
        {
            "title": "Final Evidence Package Map",
            "purpose": "Shows how evidence artifacts become resume bullets, submission notes, and interview stories.",
            "nodes": [
                {"label": "Evidence artifact", "items": ["Diagram", "Workflow", "Lab", "Runbook"], "kind": "problem"},
                {"label": "Review", "items": ["Boundary", "Proof", "Risk", "Readiness"]},
                {"label": "Project story", "items": ["Short answer", "Deep dive", "Troubleshooting", "Outcome"], "kind": "role"},
                {"label": "Positioning use", "items": ["Resume", "Submission", "Interview"], "kind": "support"},
            ],
            "layers": [
                {"label": "Diagram", "text": "Architecture and workflow are visible enough for a beginner to retell."},
                {"label": "Proof", "text": "Screenshots, command outputs, logs, dashboards, tickets, and validation notes prove the work."},
                {"label": "Boundary", "text": "Owned, contributed, supported, and non-owned areas are separated."},
                {"label": "Story", "text": "Answers connect short story, deep-dive story, and troubleshooting story."},
                {"label": "Decision", "text": "Evidence clarifies whether the target role and domain fit the candidate story."},
            ],
            "evidence": ["Architecture diagram", "Workflow diagram", "Runbook", "Interview story bank"],
            "interview": "This map links each claim to one supporting evidence artifact.",
        },
    ]


def _training_document_diagram_workbook(program: TrainingProgram) -> list[dict[str, Any]]:
    role_name = program.marketing_role.name
    domain_name = program.industry_domain
    architecture = program.cloud_architecture or {}
    systems = [str(item) for item in _as_list(program.application_landscape)[:5]]
    primary_system = systems[0] if systems else "Primary business system"
    secondary_system = systems[1] if len(systems) > 1 else "Connected customer system"
    tools = _split_training_items(program.marketing_role.common_tools)[:5]
    tool = tools[0] if tools else role_name
    lob_names = [
        str(item.get("name"))
        for item in _as_list(architecture.get("linesOfBusiness"))
        if isinstance(item, dict) and item.get("name")
    ][:3]
    if not lob_names:
        lob_names = ["Marketing", "Sales", "Service"]
    delivered = [
        str(item.get("title"))
        for item in _as_list(architecture.get("deliveredUseCases"))
        if isinstance(item, dict) and item.get("title")
    ][:4]
    if not delivered:
        delivered = ["Discovery", "Build", "Validate", "Handoff"]
    if role_name == "Site Reliability / AIOps Engineer":
        return _training_sre_document_diagram_workbook(domain_name, primary_system, secondary_system, lob_names, delivered)
    role_workbook = _training_role_specific_document_diagram_workbook(role_name, domain_name, primary_system, secondary_system, lob_names, delivered, tools)
    if role_workbook:
        return role_workbook
    base_diagrams = [
        ("01. Domain Operating Model", [domain_name, *lob_names, "Shared data and reporting", "Executive outcome"], "Shows how line-of-business context frames every role story."),
        ("02. Customer Journey To Business Outcome", ["Audience or user", primary_system, "Engagement", "Conversion or service action", "Measured outcome"], "Connects marketing and business activity to measurable value."),
        ("03. Customer 360 Data Flow", ["Source systems", "Identity/profile match", "Unified profile", "Segment or dashboard", "Activation"], "Explains customer data unification and profile activation."),
        ("04. Campaign Planning Flow", ["Business goal", "Audience", "Offer/content", "Channel mix", "Launch checklist", "Performance review"], "Makes campaign work visible as a managed operating process."),
        ("05. Lead Lifecycle Flow", ["Inquiry", "MQL", "Sales handoff", "SQL/opportunity", "Outcome", "Feedback loop"], "Shows how marketing, sales, and CRM teams share ownership."),
        ("06. Journey Automation Flow", ["Trigger", "Eligibility", "Message", "Decision branch", "CRM update", "Suppression"], "Explains automated journey logic and compliance checks."),
        ("07. Website Agent Conversion Flow", ["Visitor question", "AI/web agent", "Answer or qualification", "Sales route", "Conversion signal"], "Uses the Microsoft Copilot Studio pattern for digital engagement."),
        ("08. CRM Modernization Flow", ["Current process", "Data model", "Workflow", "UAT", "Go-live", "Hypercare"], "Shows how CRM changes become adopted business process."),
        ("09. Sales And Marketing Handoff", ["Campaign response", "Lead score", "Owner assignment", "Seller action", "Follow-up SLA", "Closed-loop report"], "Clarifies shared revenue-process boundaries."),
        ("10. Service Feedback Loop", ["Customer case", "Reason code", "Insight", "Campaign or product action", "Retention metric"], "Connects customer service data to marketing decisions."),
        ("11. Analytics Dashboard Flow", ["Raw data", "Model/transform", "Metric definition", "Dashboard", "Readout", "Decision"], "Shows how reporting becomes decision support."),
        ("12. Experiment Lifecycle", ["Hypothesis", "Audience split", "Launch", "Measure", "Learn", "Scale or stop"], "Gives growth and analyst roles a clean testing narrative."),
        ("13. Segmentation Model", ["Business rule", "Data attributes", "Segment build", "QA", "Activation", "Performance"], "Explains audience segmentation without vague tool talk."),
        ("14. Personalization Flow", ["Customer signal", "Segment", "Message variant", "Channel", "Response", "Optimization"], "Shows how customer insight turns into relevant engagement."),
        ("15. Consent And Compliance Flow", ["Consent source", "Preference check", "Suppression", "Audit trail", "Approved send"], "Keeps privacy and governance visible in marketing workflows."),
        ("16. Product Launch Enablement", ["Positioning", "Persona", "Sales collateral", "Campaign", "Feedback", "Adoption"], "Maps product marketing work to launch readiness."),
        ("17. Proposal And Content Copilot Flow", ["Customer need", "Source material", "Copilot draft", "Human review", "Final asset", "Reuse"], "Uses Microsoft 365 Copilot productivity patterns safely."),
        ("18. Contact Center Modernization", ["Customer request", "Case intake", "Knowledge response", "Escalation", "Resolution", "Insight"], "Supports customer experience and service modernization stories."),
        ("19. Industry Use Case Mapping", [domain_name, primary_system, secondary_system, role_name, "Microsoft reference story", "Interview example"], "Turns official stories into domain-specific examples."),
        ("20. Line-Of-Business Map", [*lob_names, "Shared CRM/CDP", "Reporting", "Leadership"], "Shows how one program spans multiple business functions."),
        ("21. Microsoft Customer Story Pattern", ["Business problem", "Microsoft solution", "Implementation motion", "Metric", "Reusable talk track"], "Standardizes how official reference stories become training material."),
        ("22. Interview STAR Story Flow", ["Situation", "Task", "Action", "Result", "Evidence", "Boundary"], "Turns project work into structured interview answers."),
        ("23. Resume Bullet Evidence Flow", ["Use case", "Role action", tool, "Metric/output", "Resume bullet", "Mock answer"], "Keeps resume language tied to proof."),
        ("24. Capstone Presentation Flow", ["Scenario", "Architecture", "LOB process", "Security/governance", "Outcome", "Q&A"], "Frames final evaluation for staff and consultants."),
        ("25. Final Readiness Gate", ["Diagram pack", "Use cases", "Mock interview", "Resume", "Submission notes", "Approved target"], "Makes training completion measurable."),
    ]
    for index, title in enumerate(delivered, start=26):
        base_diagrams.append(
            (
                f"{index:02d}. Delivered Use Case Flow",
                ["Business problem", title, f"{role_name} action", "Validation", "Outcome", "Interview story"],
                "Adds a diagram for a program-specific delivered use case.",
            )
        )
    return [
        {
            "title": title,
            "steps": steps[:6],
            "purpose": purpose,
            "provider": "Microsoft Cloud",
            "nodes": _diagram_nodes_from_steps(steps[:6], role_name),
            "evidence": _diagram_evidence_explanations(title, steps[:6], purpose, role_name, domain_name),
        }
        for title, steps, purpose in base_diagrams[:25]
    ]


def _training_role_specific_document_diagram_workbook(
    role_name: str,
    domain_name: str,
    primary_system: str,
    secondary_system: str,
    lob_names: list[str],
    delivered: list[str],
    tools: list[str],
) -> list[dict[str, Any]]:
    lob_label = " / ".join(lob_names[:2]) if lob_names else domain_name
    main_tool = tools[0] if tools else role_name
    app_pair = f"{primary_system} and {secondary_system}" if secondary_system != primary_system else primary_system

    def n(label: str, items: list[str], kind: str = "") -> dict[str, Any]:
        return {"label": label, "items": items[:3], "kind": kind}

    def d(
        title: str,
        purpose: str,
        provider: str,
        nodes: list[dict[str, Any]],
        evidence: list[str],
        layout: str,
    ) -> dict[str, Any]:
        first_label = str(nodes[0]["label"]) if nodes else "source"
        last_label = str(nodes[-1]["label"]) if nodes else "proof"
        role_label = str(nodes[2]["label"]) if len(nodes) > 2 else role_name
        evidence_items = [
            f"Technical flow from {first_label} to {last_label} shows the business outcome for {domain_name} without turning the answer into a tool list.",
            f"{role_name} boundary is centered on {role_label}; application, data, security, and operations ownership remain separately explainable.",
            f"validation proof includes {', '.join(evidence[:3])}, tied to the diagram and the exact system behavior.",
            *[f"{item} is kept as supporting proof for the technical decision and handoff." for item in evidence],
        ]
        return {
            "title": title,
            "steps": [str(node["label"]) for node in nodes[:6]],
            "purpose": purpose,
            "provider": provider,
            "nodes": nodes[:6],
            "evidence": evidence_items[:5],
            "layout": layout,
            "entryLabel": {
                "network": "Caller / source",
                "pipeline": "Change / input",
                "security": "Actor / risk",
                "resilience": "Critical service",
                "data": "Data source",
                "mlops": "Model input",
                "ops": "Signal / incident",
                "governance": "Control need",
            }.get(layout, "Entry / request"),
            "boundaryLabel": {
                "network": "Network and private access boundary",
                "pipeline": "Build, promote, and release boundary",
                "security": "Security control boundary",
                "resilience": "Availability and recovery boundary",
                "data": "Pipeline and serving boundary",
                "mlops": "ML lifecycle boundary",
                "ops": "Operational response boundary",
                "governance": "Governance boundary",
            }.get(layout, "Provider cloud boundary"),
            "proofLabel": {
                "network": "Connectivity proof",
                "pipeline": "Release proof",
                "security": "Control proof",
                "resilience": "Recovery proof",
                "data": "Data quality proof",
                "mlops": "Model evidence",
                "ops": "Incident proof",
                "governance": "Audit proof",
            }.get(layout, "Operational proof"),
            "entryCaption": {
                "network": "caller, source network, DNS name, or partner system.",
                "pipeline": "code, configuration, data, model, or infrastructure change.",
                "security": "identity, access request, vulnerability, or policy risk.",
                "resilience": "business-critical service and its availability expectation.",
                "data": "source system, file, stream, API, or database feed.",
                "mlops": "training data, feature data, prompt, or prediction request.",
                "ops": "alert, user symptom, failed job, or performance signal.",
                "governance": "standard, compliance rule, platform policy, or exception request.",
            }.get(layout, "user, business, data, or event trigger."),
            "boundaryCaption": {
                "network": "routing, firewall, subnet, endpoint, DNS, and private service controls.",
                "pipeline": "review gates, artifact movement, deployment controls, and rollback path.",
                "security": "least privilege, encryption, secrets, scanning, audit, and exception path.",
                "resilience": "multi-AZ/region design, backup, restore, failover, and validation path.",
                "data": "ingestion, transformation, validation, warehouse/lake serving, and consumer ownership.",
                "mlops": "feature pipeline, model registry, deployment, monitoring, and retraining path.",
                "ops": "triage, owner routing, mitigation, validation, communication, and prevention.",
                "governance": "guardrails, approvals, evidence capture, review frequency, and signoff.",
            }.get(layout, "provider services that define the implementation boundary."),
            "proofCaption": {
                "network": "route check, DNS result, flow log, endpoint state, or reachability test.",
                "pipeline": "pipeline run, artifact version, test result, deployment history, or rollback note.",
                "security": "policy result, scan output, access log, key/secret event, or audit record.",
                "resilience": "restore output, health probe, replica state, failover test, or RTO/RPO note.",
                "data": "row counts, freshness check, schema result, lineage, failed-run recovery, or consumer query.",
                "mlops": "pipeline run, model version, metrics, drift report, endpoint health, or approval note.",
                "ops": "timeline, log/metric/trace, before-after signal, RCA, or runbook update.",
                "governance": "compliance dashboard, exception record, access review, or control sample.",
            }.get(layout, "logs, dashboards, policies, runbooks, or validation outputs that confirm the outcome."),
        }

    specs: dict[str, list[dict[str, Any]]] = {
        "Cloud Platform Engineer": [
            d("01. Banking Landing Zone Account Structure", f"Shows how {domain_name} workloads are separated by account, subscription, project, environment, and ownership.", "AWS / Azure / GCP", [n("Enterprise root", [domain_name, "organization / tenant", "policy baseline"], "problem"), n("Environment boundary", ["dev / test / prod", "shared services", "break-glass access"]), n("Network foundation", ["VPC/VNet", "CIDR plan", "subnet tiers"], "role"), n("Security baseline", ["IAM/RBAC", "KMS/Key Vault", "logging"]), n("Governance", ["tags", "budgets", "policy checks"], "role"), n("Audit proof", ["account map", "policy result", "access review"], "support")], ["account/subscription map", "environment ownership table", "policy compliance output", "access review evidence"], "layered"),
            d("02. Hub-Spoke VPC/VNet Network For Core Banking", f"Explains private connectivity between {app_pair}, shared services, and secure ingress.", "Azure / AWS", [n("Users and partners", ["branch users", "mobile users", "partner APIs"], "problem"), n("Edge", ["DNS", "CDN/WAF", "API gateway"]), n("Hub network", ["firewall", "NAT/egress", "private DNS"], "role"), n("Spoke network", ["app subnet", "data subnet", "private endpoints"], "role"), n("Shared services", ["directory", "logging", "secrets"]), n("Network proof", ["route table", "flow log", "connectivity test"], "support")], ["hub-spoke diagram", "route table validation", "firewall rule evidence", "private endpoint DNS test"], "network"),
            d("03. Public Ingress To Private Workload Path", f"Traces a customer request into {primary_system} without exposing backend services directly.", "Cloud Edge", [n("Customer request", ["browser/mobile/API", "TLS", "domain name"], "problem"), n("Protection", ["WAF rule", "rate limit", "certificate"]), n("Routing", ["API gateway", "load balancer", "target group"], "role"), n("Private runtime", ["AKS/EKS/GKE", "VM scale set", "serverless"]), n("Private dependency", ["database", "cache", "queue"]), n("Validation", ["200/4xx/5xx", "health probe", "trace"], "support")], ["load balancer health", "WAF rule", "target health", "trace/request log"], "request"),
            d("04. Private Endpoint Pattern For Data Services", f"Shows how {secondary_system} reaches storage, database, and key services over private paths.", "AWS / Azure / GCP", [n("Workload subnet", ["pod/VM/function", "managed identity", "service account"], "problem"), n("Private DNS", ["zone link", "resolver", "record"]), n("Private endpoint", ["Private Link", "VPC endpoint", "PSC"], "role"), n("Managed data service", ["SQL/RDS", "object storage", "NoSQL"]), n("Access control", ["IAM/RBAC", "network ACL", "encryption"]), n("Proof", ["DNS lookup", "connection test", "deny public access"], "support")], ["private DNS lookup", "endpoint connection state", "public access disabled", "successful private connection"], "network"),
            d("05. Kubernetes Platform Runtime Blueprint", f"Explains how platform standards support container workloads for {lob_label}.", "AKS / EKS / GKE", [n("Namespace model", ["team namespace", "quota", "RBAC"], "problem"), n("Ingress", ["controller", "TLS", "network policy"]), n("Workload", ["deployment", "HPA", "probes"], "role"), n("Platform add-ons", ["secrets driver", "observability agent", "policy agent"]), n("Node pools", ["system/user pools", "autoscaling", "patching"]), n("Runtime proof", ["rollout status", "pod health", "events"], "support")], ["namespace/RBAC view", "deployment rollout", "HPA metric", "pod/event evidence"], "layered"),
            d("06. Terraform Module Promotion Flow", f"Shows how reusable platform infrastructure changes are reviewed before affecting {domain_name} environments.", "IaC", [n("Change request", ["module version", "variable change", "risk"], "problem"), n("Plan", ["terraform plan", "drift check", "policy check"]), n("Review", ["pull request", "security review", "owner approval"], "role"), n("Apply", ["workspace", "state lock", "controlled rollout"]), n("Validate", ["resource diff", "connectivity", "monitoring"]), n("Evidence", ["plan output", "PR", "apply log"], "support")], ["terraform plan", "policy check", "PR approval", "apply and validation output"], "pipeline"),
            d("07. IAM And Secrets Access Boundary", f"Explains least-privilege access for platform engineers, applications, and support teams.", "Identity", [n("Actor", ["developer", "pipeline", "workload identity"], "problem"), n("Role binding", ["IAM role", "RBAC", "group"]), n("Secret store", ["Key Vault", "Secrets Manager", "Secret Manager"], "role"), n("Runtime access", ["managed identity", "service account", "token scope"]), n("Audit trail", ["access log", "denied request", "rotation"]), n("Control proof", ["policy", "audit event", "secret version"], "support")], ["role assignment", "secret access log", "denied access sample", "rotation record"], "security"),
            d("08. Multi-AZ High Availability Pattern", f"Shows how {primary_system} stays available during instance, node, or zone failure.", "Cloud Resilience", [n("User traffic", ["regional endpoint", "health check", "SLA"], "problem"), n("Load balancing", ["multi-AZ targets", "health probe", "failout"]), n("Compute scale", ["ASG/VMSS/node pool", "pod replicas", "autoscale"], "role"), n("Data tier", ["multi-AZ DB", "replica", "backup"]), n("Dependency", ["queue/cache", "retry", "timeout"]), n("HA proof", ["target health", "failover test", "SLO"], "support")], ["multi-AZ diagram", "health probe", "replica status", "failover validation"], "resilience"),
            d("09. Backup, Restore, And DR Evidence Path", f"Makes recoverability visible for regulated {domain_name} systems.", "DR", [n("Critical workload", [primary_system, "RTO/RPO", "tier"], "problem"), n("Backup policy", ["snapshot", "retention", "immutability"]), n("Replication", ["cross-zone", "cross-region", "vault"], "role"), n("Restore test", ["test database", "checksum", "application smoke"]), n("Failover plan", ["DNS/traffic switch", "runbook", "owner"]), n("DR proof", ["restore result", "RTO/RPO note", "signoff"], "support")], ["backup policy", "restore output", "DR runbook", "RTO/RPO validation"], "resilience"),
            d("10. Observability Baseline For Platform Services", f"Shows standard metrics, logs, and alerts required before onboarding {secondary_system}.", "Observability", [n("Platform component", ["load balancer", "cluster", "database"], "problem"), n("Metrics", ["availability", "latency", "saturation"]), n("Logs", ["access", "audit", "platform events"], "role"), n("Alerts", ["threshold", "owner", "severity"]), n("Dashboard", ["service view", "dependency view", "cost view"]), n("Support proof", ["alert route", "runbook", "incident link"], "support")], ["dashboard", "alert rule", "log query", "runbook link"], "ops"),
            d("11. Cloud Cost And Tagging Control Model", f"Connects platform design to cost visibility for {lob_label}.", "FinOps", [n("Resource request", ["environment", "cost center", "owner"], "problem"), n("Tag policy", ["application", "LOB", "data class"]), n("Budget control", ["budget alert", "quota", "rightsizing"], "role"), n("Lifecycle", ["storage tier", "cleanup", "schedule"]), n("Report", ["service cost", "unused resources", "trend"]), n("Evidence", ["tag report", "budget alert", "savings note"], "support")], ["tag compliance report", "budget alert", "rightsizing recommendation", "cleanup output"], "governance"),
            d("12. Hybrid Connectivity And Partner Integration", f"Explains secure connectivity for bank partners, payment networks, and internal systems.", "Network", [n("External partner", ["payment gateway", "KYC vendor", "core banking"], "problem"), n("Connectivity", ["VPN/Direct Connect", "ExpressRoute", "Interconnect"]), n("Routing", ["BGP", "route table", "firewall"], "role"), n("Private service", ["API", "database", "queue"]), n("Monitoring", ["tunnel status", "flow logs", "latency"]), n("Proof", ["reachability", "packet/flow", "SLA note"], "support")], ["tunnel status", "route table", "flow log", "connectivity test"], "network"),
            d("13. Policy-As-Code Guardrail Flow", f"Shows how platform standards prevent noncompliant resources before release.", "Governance", [n("Resource template", ["Terraform", "Bicep", "YAML"], "problem"), n("Policy rule", ["encryption", "public access", "approved region"]), n("CI check", ["OPA/Conftest", "Azure Policy", "Config"], "role"), n("Exception path", ["risk note", "approval", "expiry"]), n("Deployment gate", ["pass/fail", "environment lock", "audit"]), n("Evidence", ["policy result", "exception record", "compliance score"], "support")], ["policy check output", "failed guardrail example", "exception approval", "compliance dashboard"], "pipeline"),
            d("14. Certificate And DNS Change Flow", f"Explains controlled domain, certificate, and routing changes for public banking services.", "Edge", [n("Change request", ["domain", "certificate", "owner"], "problem"), n("DNS", ["record", "TTL", "zone delegation"]), n("Certificate", ["ACM/Key Vault", "renewal", "chain"], "role"), n("Ingress binding", ["listener", "gateway", "route"]), n("Validation", ["HTTPS check", "expiry monitor", "rollback"]), n("Evidence", ["change ticket", "curl result", "certificate status"], "support")], ["DNS record", "certificate status", "HTTPS response", "rollback note"], "edge"),
            d("15. Database Network And Encryption Pattern", f"Shows how platform decisions protect managed database access for {primary_system}.", "Data Platform", [n("Application runtime", ["private subnet", "identity", "connection string"], "problem"), n("Network access", ["security group/NSG", "private endpoint", "route"]), n("Encryption", ["KMS/CMK", "TLS", "at rest"], "role"), n("Database", ["RDS/Azure SQL/Cloud SQL", "replica", "backup"]), n("Audit", ["connection log", "admin access", "rotation"]), n("Proof", ["private access test", "encryption setting", "audit log"], "support")], ["database connectivity test", "encryption setting", "backup status", "audit log"], "security"),
            d("16. Platform Onboarding Golden Path", f"Shows how a new {domain_name} application team receives a standard cloud foundation.", "Developer Platform", [n("Application team", ["new service", "nonfunctional needs", "data class"], "problem"), n("Template", ["repo scaffold", "IaC module", "pipeline"]), n("Provision", ["namespace/account", "secrets", "network"], "role"), n("Baseline", ["monitoring", "alerts", "backup"]), n("Handoff", ["runbook", "owners", "support model"]), n("Readiness proof", ["checklist", "dashboard", "access test"], "support")], ["onboarding checklist", "module output", "monitoring baseline", "support handoff"], "pipeline"),
            d("17. Release Environment Separation", f"Explains how lower environments differ from production without losing validation quality.", "Environment", [n("Code change", ["feature", "config", "infra change"], "problem"), n("Dev/test", ["synthetic data", "reduced scale", "sandbox access"]), n("Staging", ["prod-like network", "approval", "smoke tests"], "role"), n("Production", ["controlled access", "change window", "rollback"]), n("Promotion rule", ["artifact version", "config diff", "approval"]), n("Evidence", ["deployment history", "config diff", "smoke result"], "support")], ["environment map", "config diff", "deployment history", "smoke test"], "pipeline"),
            d("18. Security Monitoring To Platform Response", f"Shows how security findings become platform remediation without confusing ownership.", "Security Ops", [n("Finding", ["CSPM alert", "vulnerability", "public exposure"], "problem"), n("Triage", ["severity", "asset owner", "blast radius"]), n("Platform fix", ["policy", "network rule", "patch"], "role"), n("Application handoff", ["code/config owner", "deadline", "risk"]), n("Validation", ["scan rerun", "control pass", "exception closed"]), n("Proof", ["ticket", "scan result", "control evidence"], "support")], ["security finding", "remediation PR", "scan rerun", "closure note"], "security"),
            d("19. Platform Incident Routing Model", f"Shows who owns network, identity, runtime, database, and application issues during incidents.", "Operations", [n("Alert/user symptom", ["timeout", "5xx", "access denied"], "problem"), n("Layer check", ["edge", "network", "identity"]), n("Platform lane", ["routing", "cluster", "policy"], "role"), n("Application lane", ["code", "dependency", "config"]), n("Data/security lane", ["database", "IAM", "compliance"]), n("Recovery proof", ["owner note", "metric normal", "timeline"], "support")], ["incident timeline", "owner routing note", "before/after metric", "runbook update"], "ops"),
            d("20. Regulated Audit Evidence Map", f"Maps platform controls to audit-ready evidence for {domain_name}.", "Compliance", [n("Control need", ["encryption", "access", "logging"], "problem"), n("Cloud control", ["policy", "IAM", "network"]), n("Operational control", ["backup", "monitoring", "change"], "role"), n("Evidence store", ["ticket", "report", "screenshot/output"]), n("Review", ["owner", "frequency", "exception"]), n("Audit proof", ["control map", "sample evidence", "approval"], "support")], ["control evidence map", "access review", "backup report", "change approval"], "governance"),
        ],
        "DevOps Engineer": [
            d("01. Domain Release Train For Application Changes", f"Shows how changes move safely into {primary_system}.", "CI/CD", [n("Backlog item", [domain_name, "story", "acceptance criteria"], "problem"), n("Source control", ["branch", "PR", "review"]), n("Build", ["compile", "unit test", "artifact"], "role"), n("Quality gate", ["SAST", "dependency scan", "approval"]), n("Deploy", ["environment", "version", "smoke"]), n("Release proof", ["pipeline run", "rollback point", "release note"], "support")], ["PR", "pipeline run", "artifact version", "smoke test"], "pipeline"),
            d("02. Container Build And Registry Flow", f"Explains image creation and promotion for {secondary_system}.", "Docker", [n("Code change", ["Dockerfile", "base image", "app config"], "problem"), n("Build", ["multi-stage", "cache", "tag"]), n("Scan", ["Trivy", "SCA", "policy"], "role"), n("Registry", ["ECR/ACR/GAR", "immutable tag", "retention"]), n("Deploy reference", ["Helm/Kustomize", "digest", "env config"]), n("Proof", ["image tag", "scan result", "deployment revision"], "support")], ["image tag", "scan result", "registry entry", "deployment revision"], "pipeline"),
            d("03. Environment Variable And Secret Failure Path", f"Shows a common production issue in {domain_name} releases.", "Runtime", [n("Deployment", ["new version", "config change", "secret ref"], "problem"), n("Runtime", ["pod/container", "env var", "volume mount"]), n("Failure signal", ["CrashLoop", "connection refused", "auth error"], "role"), n("Fix path", ["secret version", "variable name", "restart/rollout"]), n("Validation", ["logs clean", "health check", "request success"]), n("Evidence", ["event", "log", "fixed rollout"], "support")], ["failed pod event", "sanitized env/secret reference", "log before/after", "rollout status"], "ops"),
            d("04. Rollback And Hotfix Decision Flow", f"Shows how release recovery is handled without guessing.", "Release Ops", [n("Bad release", ["alert", "user impact", "failed check"], "problem"), n("Decision", ["rollback", "hotfix", "feature flag"]), n("Rollback path", ["previous artifact", "Helm revision", "deployment history"], "role"), n("Validation", ["smoke test", "error rate", "logs"]), n("Communication", ["incident update", "owner", "ETA"]), n("Proof", ["before/after", "timeline", "RCA note"], "support")], ["deployment history", "rollback command/result", "metric recovery", "incident update"], "ops"),
            d("05. CI/CD Security Gate", f"Connects code quality and security checks to deployment control for {app_pair}.", "DevSecOps", [n("Pull request", ["changed files", "review", "risk"], "problem"), n("Dependency check", ["OWASP/SCA", "license", "CVEs"]), n("Code quality", ["SonarQube", "coverage", "bugs"], "role"), n("Image scan", ["Trivy", "critical/high", "policy"]), n("Approval gate", ["exception", "fix required", "release block"]), n("Evidence", ["quality report", "scan report", "approval"], "support")], ["SonarQube report", "dependency scan", "image scan", "approval/exception record"], "security"),
            d("06. Kubernetes Deployment Troubleshooting", f"Shows how DevOps supports failed rollout for {primary_system}.", "Kubernetes", [n("Rollout", ["deployment", "image", "config"], "problem"), n("Signals", ["events", "logs", "readiness", "liveness"]), n("Isolation", ["image pull", "probe", "resource", "secret"], "role"), n("Action", ["fix manifest", "rollback", "scale"]), n("Validation", ["ready replicas", "service endpoint", "smoke"]), n("Proof", ["kubectl output", "events", "logs"], "support")], ["rollout status", "events/logs", "replica health", "smoke test"], "ops"),
        ],
        "Data Platform Engineer": [
            d("01. Source-To-Consumer Data Flow", f"Traces {primary_system} data from ingestion to trusted reporting.", "Data Platform", [n("Source", [primary_system, "files/APIs/events", "schema"], "problem"), n("Ingestion", ["batch/stream", "landing zone", "metadata"]), n("Transform", ["Spark/dbt/SQL", "business rules", "dedupe"], "role"), n("Validate", ["row count", "schema", "freshness"]), n("Serve", ["warehouse/lakehouse", "BI/API", "consumer"]), n("Proof", ["run log", "reconciliation", "dashboard"], "support")], ["pipeline run", "row-count reconciliation", "freshness check", "consumer query"], "data"),
            d("02. MDM Golden Record Pattern", f"Explains trusted master data for {domain_name} entities.", "MDM", [n("Systems", [primary_system, secondary_system, "external source"], "problem"), n("Match", ["keys", "fuzzy match", "survivorship"]), n("Golden record", ["customer/member/account", "standard attributes", "lineage"], "role"), n("Govern", ["steward review", "quality rule", "exception"]), n("Publish", ["warehouse", "API", "downstream"]), n("Evidence", ["match report", "DQ score", "lineage"], "support")], ["match report", "DQ dashboard", "steward exception", "lineage view"], "data"),
            d("03. Incremental ETL Recovery Flow", f"Shows how failed data loads are recovered without corrupting reporting.", "ETL", [n("Scheduled run", ["source extract", "watermark", "partition"], "problem"), n("Failure", ["bad record", "timeout", "schema drift"]), n("Quarantine", ["reject table", "error log", "sample"], "role"), n("Replay/backfill", ["watermark reset", "idempotent load", "merge"]), n("Reconcile", ["counts", "checksum", "freshness"]), n("Proof", ["failed run", "corrected run", "stakeholder note"], "support")], ["failed task", "bad record sample", "backfill output", "reconciled counts"], "ops"),
            d("04. Warehouse Performance Tuning Path", f"Explains query and storage optimization for {domain_name} analytics.", "Warehouse", [n("Slow report", ["BI dashboard", "ad hoc query", "SLA"], "problem"), n("Profile", ["query plan", "scan bytes", "join/cardinality"]), n("Optimize", ["partition", "cluster/index", "materialized view"], "role"), n("Validate", ["runtime", "cost", "same result"]), n("Publish", ["model update", "documentation", "owner"]), n("Evidence", ["before/after plan", "runtime chart", "query result"], "support")], ["query plan", "before/after runtime", "cost/scan reduction", "model documentation"], "data"),
        ],
        "MLOps / AI Platform Engineer": [
            d("01. Data-To-Model Lifecycle", f"Shows production ML movement from {primary_system} data to governed model deployment.", "MLOps", [n("Training data", [primary_system, "features", "labels"], "problem"), n("Pipeline", ["preprocess", "train", "evaluate"]), n("Registry", ["model version", "metrics", "approval"], "role"), n("Deploy", ["endpoint/batch", "canary", "rollback"]), n("Monitor", ["latency", "quality", "drift"]), n("Evidence", ["run ID", "model card", "endpoint metrics"], "support")], ["pipeline run", "model version", "approval record", "endpoint/drift metric"], "mlops"),
            d("02. Feature Store Governance Pattern", f"Explains reusable features for {domain_name} predictions.", "Feature Platform", [n("Raw signals", [primary_system, secondary_system, "events"], "problem"), n("Feature pipeline", ["transform", "window", "join"]), n("Feature store", ["offline", "online", "version"], "role"), n("Access", ["training", "serving", "permissions"]), n("Quality", ["freshness", "nulls", "skew"]), n("Proof", ["feature definition", "freshness chart", "access audit"], "support")], ["feature definition", "freshness check", "training/serving parity", "access audit"], "data"),
            d("03. Model Monitoring And Drift Response", f"Shows how AI behavior is monitored after deployment.", "AI Ops", [n("Prediction service", ["endpoint", "batch scoring", "consumer"], "problem"), n("Signals", ["latency", "errors", "prediction distribution"]), n("Drift check", ["data drift", "concept drift", "skew"], "role"), n("Decision", ["retrain", "rollback", "threshold change"]), n("Validation", ["A/B result", "business KPI", "human review"]), n("Evidence", ["monitor alert", "drift report", "model update"], "support")], ["drift report", "endpoint metric", "retraining run", "approval note"], "ops"),
        ],
    }

    role_diagrams = specs.get(role_name)
    if not role_diagrams:
        return []

    if len(role_diagrams) < 25:
        role_patterns = {
            "Cloud Platform Engineer": [
                ("Shared Services Platform Map", ["directory", "DNS", "logging", "secrets"]),
                ("Cloud Migration Wave Architecture", ["source network", "replication", "cutover", "validation"]),
                ("API Gateway And Service Mesh Path", ["gateway", "mTLS", "routing", "telemetry"]),
                ("Object Storage Lifecycle Control", ["bucket/container", "tiering", "retention", "restore"]),
                ("Platform Patch And Image Baseline", ["golden image", "patch ring", "scan", "rollout"]),
            ],
            "DevOps Engineer": [
                ("Blue-Green Deployment Flow", ["version A", "version B", "traffic shift", "rollback"]),
                ("Feature Flag Release Control", ["flag", "cohort", "monitor", "disable"]),
                ("Artifact Promotion Across Environments", ["build once", "promote", "approve", "deploy"]),
                ("Pipeline Agent And Runner Capacity", ["runner pool", "queue", "cache", "scaling"]),
                ("Database Migration Release Gate", ["migration", "backup", "compatibility", "rollback"]),
            ],
            "Data Platform Engineer": [
                ("Schema Evolution And Contract Check", ["producer", "schema", "compatibility", "consumer"]),
                ("Streaming Pipeline Lag Control", ["topic", "consumer lag", "checkpoint", "replay"]),
                ("Data Lake Zone Governance", ["raw", "curated", "published", "access"]),
                ("Data Lineage And Catalog Flow", ["source", "catalog", "lineage", "owner"]),
                ("Dashboard Metric Certification", ["definition", "model", "QA", "publish"]),
            ],
            "MLOps / AI Platform Engineer": [
                ("Experiment Tracking To Registry", ["experiment", "metrics", "artifact", "approval"]),
                ("Online Inference Reliability", ["request", "endpoint", "autoscale", "fallback"]),
                ("Batch Scoring Pipeline", ["dataset", "job", "output", "quality"]),
                ("Responsible AI Review Gate", ["risk", "bias check", "approval", "audit"]),
                ("Prompt And Agent Evaluation Flow", ["prompt", "tool call", "evaluation", "guardrail"]),
            ],
        }.get(role_name, [])
        index = len(role_diagrams) + 1
        while len(role_diagrams) < 25:
            pattern_title, pattern_steps = role_patterns[(len(role_diagrams) - index + 1) % len(role_patterns)]
            full_title = f"{len(role_diagrams) + 1:02d}. {domain_name} {pattern_title}"
            nodes = [
                n("Business trigger", [domain_name, primary_system, "change/risk"], "problem"),
                n(pattern_steps[0].title(), [pattern_steps[0], "input", "owner"]),
                n(pattern_steps[1].title(), [pattern_steps[1], "control", "decision"], "role"),
                n(pattern_steps[2].title(), [pattern_steps[2], "validation", "failure mode"], "role"),
                n(pattern_steps[3].title(), [pattern_steps[3], "handoff", main_tool]),
                n("Evidence", ["dashboard/output", "ticket/runbook", "interview proof"], "support"),
            ]
            role_diagrams.append(
                d(
                    full_title,
                    f"Connects {role_name} work to {domain_name} functionality around {primary_system}, using a distinct technical pattern rather than a generic business flow.",
                    main_tool,
                    nodes,
                    [f"{pattern_title} diagram", "before/after validation", "owner handoff", "operational proof"],
                    "technical",
                )
            )
    return role_diagrams[:25]


def _microsoft_healthcare_customer_story_references(program: TrainingProgram) -> list[dict[str, Any]]:
    if program.industry_domain != "Healthcare / Health Insurance":
        return []
    role_name = program.marketing_role.name
    systems = [str(item) for item in _as_list(program.application_landscape)[:3]]
    primary_system = systems[0] if systems else "healthcare system"
    role_focus = {
        "DevOps Engineer": "release, rollback, deployment evidence, environment promotion, and production handoff",
        "Cloud Platform Engineer": "landing zone, migration, network, compute, storage, security, and disaster recovery foundations",
        "Site Reliability / AIOps Engineer": "SLOs, observability, alerting, incident response, RCA, DR readiness, and operational evidence",
        "Data Platform Engineer": "data ingestion, lakehouse, warehouse, quality checks, reporting, lineage, and analytics reliability",
        "MLOps / AI Platform Engineer": "AI platform, model deployment, monitoring, drift checks, inference reliability, and responsible AI evidence",
    }.get(role_name, "role-specific delivery, governance, validation, and evidence")
    stories = [
        {
            "key": "access",
            "customer": "Access Community Health Network",
            "title": "Azure EMR Migration And Disaster Recovery",
            "sourceName": "Microsoft Customer Story",
            "sourceUrl": "https://www.microsoft.com/en/customers/story/26106-access-community-health-network-azure",
            "businessProblem": "A nonprofit healthcare provider needed to move from expensive end-of-life on-premises infrastructure to a more agile Azure foundation for EMR workloads.",
            "microsoftServices": ["Azure Migrate", "Azure Accelerate", "Azure Virtual Machines", "Microsoft Security", "Azure disaster recovery architecture"],
            "outcomes": ["300+ EMR servers migrated", "11-month migration", "15% performance improvement", "$300K+ license savings", "$150K expected annual DR savings"],
            "diagramSteps": ["On-prem EMR", "Azure Migrate", "Azure VMs", "Security visibility", "DR plan", "Performance/cost outcome"],
        },
        {
            "key": "intermountain",
            "customer": "Intermountain Health",
            "title": "Responsible AI Platform And AI Observability",
            "sourceName": "Microsoft Customer Story",
            "sourceUrl": "https://www.microsoft.com/en/customers/story/22701-intermountain-health-azure-open-ai-service",
            "businessProblem": "A large health system needed scalable, governed AI infrastructure for clinical and operational AI products.",
            "microsoftServices": ["Microsoft Azure", "Azure OpenAI Service", "Azure Databricks", "Azure API Management", "GitHub Actions for Azure", "Microsoft 365 Copilot"],
            "outcomes": ["AI infrastructure on Azure", "AI observability practices", "40 hours saved per quarter per AI product", "4,300 hours saved with Microsoft 365 and Copilot"],
            "diagramSteps": ["Clinical/AI use case", "Azure Databricks", "Azure OpenAI", "API Management", "Observability", "Responsible AI outcome"],
        },
        {
            "key": "fdb",
            "customer": "FDB Vela",
            "title": "E-Prescription Security, Compliance, And Scale",
            "sourceName": "Microsoft Customer Story",
            "sourceUrl": "https://www.microsoft.com/en/customers/story/23486-first-databank-azure",
            "businessProblem": "An e-prescribing platform needed stronger PHI protection, compliance posture, and scalable transaction capacity.",
            "microsoftServices": ["Microsoft Sentinel", "Microsoft Defender for Cloud", "Azure Resource Manager", "Azure Firewall", "Azure security controls"],
            "outcomes": ["HITRUST r2 certification support", "HIPAA-aligned security posture", "15,000 scripts per day", "future target of 10,000 scripts per hour"],
            "diagramSteps": ["eRx transaction", "Azure Firewall", "Defender for Cloud", "Sentinel", "Compliance evidence", "Scale outcome"],
        },
        {
            "key": "paige",
            "customer": "Paige.AI",
            "title": "Petabyte-Scale Digital Pathology And AI Infrastructure",
            "sourceName": "Microsoft Customer Story",
            "sourceUrl": "https://www.microsoft.com/en/customers/story/1731604994973070357-paigeai-azure-healthcare-en-united-states",
            "businessProblem": "A digital pathology AI company needed cost-effective cloud storage and compute for massive cancer-image datasets and AI workloads.",
            "microsoftServices": ["Azure Blob Storage", "Azure Virtual Machines", "Azure AI Infrastructure", "Azure Storage", "GPU and CPU compute"],
            "outcomes": ["Nearly 10 petabytes of image data", "phased migration to Azure", "AI training and inference support", "global pathology access"],
            "diagramSteps": ["Pathology images", "Azure Blob Storage", "GPU/CPU compute", "AI training", "Inference", "Pathologist access"],
        },
        {
            "key": "acentra",
            "customer": "Acentra Health",
            "title": "Azure OpenAI For Clinical Appeals Productivity",
            "sourceName": "Microsoft Customer Story",
            "sourceUrl": "https://www.microsoft.com/en/customers/story/19280-acentra-health-azure",
            "businessProblem": "A healthcare services organization needed to reduce manual nurse workload for appeals correspondence.",
            "microsoftServices": ["Azure OpenAI Service", "Azure application architecture", "secure healthcare workflow"],
            "outcomes": ["11,000 nursing hours saved", "nearly $800,000 saved", "MedScribe deployed in six months"],
            "diagramSteps": ["Appeals input", "Secure app", "Azure OpenAI", "Draft letter", "Human review", "Productivity outcome"],
        },
        {
            "key": "mercy",
            "customer": "Mercy",
            "title": "Azure Data Cloud For Patient Care Insights",
            "sourceName": "Microsoft Customer Story",
            "sourceUrl": "https://www.microsoft.com/en/customers/story/1663846645014128331-mercy-health-provider-azure-en-united-states",
            "businessProblem": "A large health system needed cloud-scale data access to improve patient experience, operational cost, and provider insights.",
            "microsoftServices": ["Microsoft Azure", "Epic-related Azure ecosystem", "cloud data and analytics foundation"],
            "outcomes": ["data access modernization", "scalable Azure tools", "patient-care insight foundation"],
            "diagramSteps": ["EHR/data sources", "Azure data foundation", "Analytics", "Provider access", "Operational insight", "Patient outcome"],
        },
    ]
    role_story_keys = {
        "DevOps Engineer": {"access", "fdb"},
        "Cloud Platform Engineer": {"access", "fdb", "paige"},
        "Site Reliability / AIOps Engineer": {"access", "intermountain", "fdb"},
        "Data Platform Engineer": {"paige", "mercy", "intermountain"},
        "MLOps / AI Platform Engineer": {"intermountain", "paige", "acentra"},
    }.get(role_name, {"access", "intermountain"})
    rows = []
    for story in stories:
        if story["key"] not in role_story_keys:
            continue
        rows.append(
            {
                **story,
                "roleUse": f"For {role_name}, connect this story to {role_focus} around {primary_system}.",
                "diagram": {
                    "title": f"Microsoft Reference: {story['title']}",
                    "provider": "Microsoft Azure",
                    "nodes": _diagram_nodes_from_steps(story["diagramSteps"], role_name),
                    "evidence": story["outcomes"][:5],
                },
            }
        )
    return rows


def _training_sre_document_diagram_workbook(
    domain_name: str,
    primary_system: str,
    secondary_system: str,
    lob_names: list[str],
    delivered: list[str],
) -> list[dict[str, Any]]:
    diagrams = [
        ("01. Healthcare SRE Operating Model", [domain_name, *lob_names[:3], "SRE/AIOps control room", "Patient/member impact"], "Maps healthcare business operations to the SRE/AIOps operating model."),
        ("01A. SRE/AIOps Production Control Tower", ["SLO status", "CI/CD releases", "Kubernetes health", "Logs and traces", "Incident queue", "Rollback/scale action"], "Shows the real production view: reliability, releases, runtime health, evidence, incidents, and recovery actions in one diagram."),
        ("01B. Agent-Assisted Incident Triage", ["Alert", "Agent context bundle", "Runbook retrieval", "Evidence query", "Human approval", "Recovery validation"], "Explains how AI-assisted SRE triage can speed investigation while keeping production remediation under human approval."),
        ("02. EMR Availability SLO Flow", ["Clinical users", primary_system, "Availability SLI", "SLO target", "Burn-rate alert", "Error budget review"], "Shows how an EMR-style workload is monitored for availability and reliability."),
        ("03. Patient Portal Latency Flow", ["Patient request", secondary_system, "API latency", "Trace sample", "Owner routing", "Performance fix"], "Turns user-facing slowness into trace-backed triage."),
        ("04. Azure Monitor Signal Pipeline", ["Application metric", "Log Analytics", "Alert rule", "Action group", "Incident ticket", "Runbook"], "Uses Microsoft monitoring concepts to show signal-to-incident movement."),
        ("05. Application Insights Trace Triage", ["Request", "Dependency call", "Exception", "Distributed trace", "Impacted endpoint", "Developer handoff"], "Shows how SRE connects request traces to an application owner."),
        ("06. Log Analytics Investigation", ["Alert", "KQL query", "Error pattern", "Time window", "Affected service", "RCA note"], "Makes log investigation visible as a repeatable workflow."),
        ("07. AKS Container Health", ["Ingress", "Pod", "Node", "Container Insights", "Restart signal", "Scale or rollback"], "Explains Kubernetes health triage for healthcare workloads."),
        ("08. OpenTelemetry To Azure Monitor", ["Service span", "Collector", "Trace export", "Metric/log correlation", "Dashboard", "Incident evidence"], "Shows telemetry standardization from service instrumentation to monitoring."),
        ("09. Network Watcher Triage", ["User symptom", "Connection troubleshoot", "NSG/route check", "Private endpoint", "Evidence", "Network owner"], "Covers Azure network-path troubleshooting for private healthcare systems."),
        ("10. Load Balancer Health Probe", ["Traffic", "Load balancer", "Health probe", "Backend pool", "Unhealthy instance", "Recovery validation"], "Shows infrastructure-level health checks and recovery proof."),
        ("11. Synthetic Availability Test", ["Synthetic probe", "Critical journey", "Failure alert", "Region check", "Escalation", "User-impact validation"], "Creates a visible SRE pattern for critical user journey monitoring."),
        ("12. Noisy Alert Correlation", ["Alert storm", "Group by service", "Recent change", "Dependency map", "Primary incident", "Suppression rule"], "Shows AIOps-style noise reduction without hiding real incidents."),
        ("13. Datadog APM Evidence Flow", ["User request", "APM trace", "Related logs", "Infrastructure metric", "Service map", "Mitigation"], "Connects Datadog trace, log, metric, and dependency evidence."),
        ("14. Datadog Kubernetes Cluster Agent", ["Cluster event", "Cluster Agent", "Node/pod metadata", "Workload view", "Monitor", "Runbook update"], "Uses the Datadog Kubernetes monitoring pattern already referenced in the material."),
        ("15. Observability Pipeline Governance", ["Telemetry source", "Filter/redact", "Route", "Retention tier", "Cost control", "Audit evidence"], "Shows telemetry routing, privacy, and cost-aware retention."),
        ("16. Service Map Dependency Triage", ["Impacted service", "Dependency graph", "Downstream API", "Database/cache", "Owner", "Recovery proof"], "Helps consultants explain dependency isolation during incidents."),
        ("17. Security Event To Reliability Incident", ["Suspicious traffic", "WAF/Sentinel alert", "Service impact", "SRE + security bridge", "Containment", "Post-incident control"], "Connects healthcare security monitoring with reliability operations."),
        ("18. Disaster Recovery Readiness", ["Critical system", "Backup/replication", "Failover plan", "DR test", "RTO/RPO evidence", "Leadership signoff"], "Maps the ACCESS-style modernization/DR outcome into SRE readiness."),
        ("19. AI Product Observability", ["AI service", "Latency/error SLI", "Model/data signal", "Drift or quality alert", "Rollback path", "Clinical workflow note"], "Uses the Intermountain-style AI operations theme for SRE monitoring."),
        ("20. E-Prescription Security Reliability", ["Prescription transaction", "API gateway/WAF", "Sentinel/Defender signal", "Anomaly", "Escalation", "Compliance evidence"], "Uses the FDB Vela-style security and transaction-protection story."),
        ("21. Incident Command Flow", ["Page", "Triage lead", "War room", "Mitigation", "Validation", "Comms update"], "Shows how SRE handles an incident calmly and visibly."),
        ("22. RCA And Prevention Loop", ["Incident timeline", "Root cause", "Contributing factor", "Action item", "Monitor/runbook update", "Review"], "Turns an outage into concrete prevention work."),
        ("23. Production Readiness Review", ["Release candidate", "SLO check", "Dashboard", "Rollback criteria", "Support handoff", "Go/no-go"], "Connects release readiness with SRE evidence."),
        ("24. Interview SRE Story Flow", ["Business impact", delivered[0], "SRE action", "Evidence", "Outcome", "Boundary"], "Turns one delivered use case into a structured SRE interview answer."),
        ("25. Final SRE Evidence Package", ["Architecture diagram", "Dashboard", "Alert rule", "Incident timeline", "Runbook", "Mock answer"], "Defines what must be visible before using the story in interviews."),
    ]
    return [
        {
            "title": title,
            "steps": steps[:6],
            "purpose": purpose,
            "provider": "Azure + Datadog",
            "nodes": _diagram_nodes_from_steps(steps[:6], "Site Reliability / AIOps Engineer"),
            "evidence": _diagram_evidence_explanations(title, steps[:6], purpose, "Site Reliability / AIOps Engineer", domain_name),
        }
        for title, steps, purpose in diagrams
    ]


def _diagram_evidence_explanations(title: str, steps: list[str], purpose: str, role_name: str, domain_name: str) -> list[str]:
    clean_steps = [str(step).strip() for step in steps if str(step).strip()]
    first_step = clean_steps[0] if clean_steps else "business trigger"
    last_step = clean_steps[-1] if clean_steps else "validated outcome"
    role_step = clean_steps[3] if len(clean_steps) > 3 else role_name
    return [
        f"Explain the diagram as a flow from {first_step} to {last_step}, not as isolated boxes.",
        f"Connect the business outcome to {domain_name}: {purpose}",
        f"State the {role_name} boundary around {role_step}; product, application, QA, security, data, and operations keep their own ownership.",
        f"Use validation proof from the final step: screenshot, dashboard, log/query output, pipeline result, runbook, incident note, or interview story tied to {title}.",
    ]


def _diagram_nodes_from_steps(steps: list[str], role_name: str) -> list[dict[str, Any]]:
    clean_steps = [str(step) for step in steps if str(step).strip()]
    while len(clean_steps) < 6:
        clean_steps.append("Validation evidence")
    return [
        {"label": clean_steps[0], "items": ["business trigger", "user or system impact", "priority"], "kind": "problem"},
        {"label": clean_steps[1], "items": ["source system", "workflow", "dependency"]},
        {"label": clean_steps[2], "items": ["metric", "log", "trace", "alert"]},
        {"label": clean_steps[3], "items": ["analysis", "decision", "owner routing"], "kind": "role"},
        {"label": clean_steps[4], "items": [role_name, "handoff", "mitigation"], "kind": "role"},
        {"label": clean_steps[5], "items": ["evidence", "runbook", "interview proof"], "kind": "support"},
    ]


def _training_diagram_export_options(program: TrainingProgram) -> list[dict[str, str]]:
    role_name = program.marketing_role.name
    return [
        {
            "label": "Core role diagrams",
            "description": f"Request flow, delivery workflow, incident flow, and evidence path for {role_name}.",
        },
        {
            "label": "Provider architecture diagrams",
            "description": "AWS, Azure, and GCP learning-path style diagrams adapted to this role.",
        },
        {
            "label": "Evidence package diagrams",
            "description": "Architecture, workflow, runbook, story bank, and resume proof paths for interview readiness.",
        },
    ]


def _training_provider_usecase_sources(program: TrainingProgram) -> list[dict[str, Any]]:
    role_name = program.marketing_role.name
    source_map: dict[str, list[dict[str, Any]]] = {
        "Cloud Platform Engineer": [
            {
                "source": "AWS SA / SysOps + Azure AZ-305",
                "pattern": "Private networking, identity, landing zone, cost guardrails, and resilient multi-AZ platforms.",
                "use_cases": [
                    "Private service access and endpoint design",
                    "Managed identity and least-privilege platform access",
                    "Hybrid migration network landing zone",
                    "Cost and storage lifecycle guardrails",
                ],
                "evidence": ["network diagram", "route/private endpoint validation", "IAM/RBAC policy", "cost or lifecycle report"],
            },
            {
                "source": "Official provider architecture docs",
                "pattern": "Cloud is not just services in isolation; the useful pattern is account/subscription/project structure, network path, access boundary, and operational proof.",
                "use_cases": ["Multi-AZ application scaling foundation", "Cloud governance and tagging controls"],
                "evidence": ["load balancer health", "autoscaling policy", "tag/policy compliance report", "runbook"],
            },
        ],
        "Data Platform Engineer": [
            {
                "source": "AWS Data Engineer + GCP Data Engineer",
                "pattern": "Source-to-consumer pipeline: catalog, transform, validate, query, orchestrate, monitor, and recover.",
                "use_cases": [
                    "Glue crawler and catalog onboarding",
                    "Lake governance with row-level access",
                    "Athena and BigQuery performance modernization",
                    "Streaming analytics dashboard pipeline",
                ],
                "evidence": ["catalog table", "partition/schema validation", "query before/after result", "freshness or latency dashboard"],
            },
            {
                "source": "Official AWS/GCP data docs",
                "pattern": "Prioritize Glue/Data Catalog, Athena/BigQuery, Pub/Sub/Dataflow, Composer/Airflow, warehouse access, and migration/reconciliation scenarios.",
                "use_cases": ["Warehouse sharing and API access pattern", "Incremental migration into the data lake"],
                "evidence": ["orchestration run", "row count reconciliation", "consumer access test", "failed-run recovery note"],
            },
        ],
        "DevOps Engineer": [
            {
                "source": "AWS SA / SysOps",
                "pattern": "Event-driven and release-driven operations: S3 events, queues, API gateway, secrets, CI/CD, rollback, and change evidence.",
                "use_cases": [
                    "S3 event to Lambda processing workflow",
                    "SQS decoupled deployment worker",
                    "API Gateway and serverless release path",
                    "Production change evidence package",
                ],
                "evidence": ["pipeline run", "event invocation log", "queue depth/DLQ evidence", "smoke test and rollback note"],
            },
            {
                "source": "Azure + provider security docs",
                "pattern": "DevOps credibility improves when release automation also shows secret handling, least privilege, approvals, and production handoff.",
                "use_cases": ["Secrets and identity rotation path", "Container and Helm deployment standardization"],
                "evidence": ["secret reference without exposed value", "image/chart version", "deployment history", "change ticket"],
            },
        ],
        "MLOps / AI Platform Engineer": [
            {
                "source": "AWS ML Specialty + GCP Vertex AI patterns",
                "pattern": "Model work is a production lifecycle: data, features, training, evaluation, registry, deployment, monitoring, drift, retraining, and rollback.",
                "use_cases": [
                    "SageMaker or Vertex AI training pipeline",
                    "Feature parity and drift guardrail",
                    "Model registry and staged promotion",
                    "Model monitoring and retraining trigger",
                ],
                "evidence": ["pipeline run", "model metric report", "registry version", "drift/quality dashboard"],
            },
            {
                "source": "Official MLOps platform docs",
                "pattern": "Separate batch inference, real-time endpoint operations, feature checks, and production model rollback.",
                "use_cases": ["Batch inference SLA and output validation", "Real-time inference endpoint operations"],
                "evidence": ["batch output count", "endpoint latency chart", "access policy", "rollback model/version note"],
            },
        ],
        "Site Reliability / AIOps Engineer": [
            {
                "source": "AWS SysOps + Azure Monitor/Network Watcher",
                "pattern": "My SRE use case starts with impact and evidence: alert, metric, log, trace, route, recent change, owner, recovery, and RCA.",
                "use_cases": [
                    "CloudWatch or Azure Monitor alert strategy",
                    "Network path troubleshooting workflow",
                    "Incident response and RCA modernization",
                    "Autoscaling and load-balancer health response",
                ],
                "evidence": ["alert rule", "before/after metric", "network path result", "incident timeline/RCA"],
            },
            {
                "source": "GCP SRE-style monitoring and tracing docs",
                "pattern": "Move beyond dashboards into SLO, dependency traces, error budget, and prevention work.",
                "use_cases": ["SLO and user-journey reliability dashboard", "OpenTelemetry and dependency trace rollout"],
                "evidence": ["SLI query", "SLO dashboard", "trace waterfall", "runbook update"],
            },
        ],
    }
    return source_map.get(role_name, [
        {
            "source": "Provided QA documents + official provider docs",
            "pattern": "My use case connects business trigger, role boundary, implementation output, validation, support handoff, and interview story.",
            "use_cases": [item.get("title", "") for item in _as_list((program.cloud_architecture or {}).get("deliveredUseCases"))[:4] if isinstance(item, dict)],
            "evidence": ["diagram", "workflow", "validation output", "runbook", "interview story"],
        }
    ])


def _training_resume_bullets(program: TrainingProgram) -> list[str]:
    role = program.marketing_role
    tools = _split_training_items(role.common_tools)
    tool_text = ", ".join(tools[:3]) or role.name
    return [
        f"Supported {role.name.lower()} activities across development, QA, and production environments using {tool_text}, improving repeatability and delivery visibility.",
        f"Automated and documented operational workflows for {role.name.lower()} responsibilities, reducing manual handoffs and improving support readiness.",
        f"Collaborated with engineering, QA, security, and operations teams to troubleshoot failures, validate changes, and communicate release or production status.",
        f"Created dashboards, runbooks, validation steps, or process notes that helped teams resolve issues faster and maintain consistent delivery practices.",
        f"Analyzed job requirements and mapped {role.name.lower()} skills to project stories, tools, measurable outcomes, and client-facing interview answers.",
    ]


def _training_final_evidence_package_rows(program: TrainingProgram) -> list[list[str]]:
    role_name = program.marketing_role.name
    return [
        ["1", "Architecture diagram", f"How the {role_name} work fits into systems, integrations, runtime, data, and support boundaries.", "Architecture interview, project explanation, resume project summary"],
        ["2", "Workflow diagram", "The practical flow for deployment, incident, data, automation, model, support, or release work.", "Scenario interviews and operational handoff"],
        ["3", "Use-case boundary sheet", "What the role owned, contributed to, reviewed, supported, and did not own.", "Credible interview boundaries, recruiter submission notes"],
        ["4", "Tool configuration notes", "Which tools were configured, what each produced, and how outputs supported delivery or operations.", "Technical screening, tool deep-dive, resume keyword proof"],
        ["5", "Screenshots or command outputs", "Safe proof of deployments, checks, dashboards, logs, tests, pipelines, tickets, or validation output.", "Evidence review and interview specifics"],
        ["6", "Runbook and incident simulation", "How symptoms were detected, triaged, routed, recovered, validated, and prevented.", "Troubleshooting rounds, SRE/DevOps/platform interviews"],
        ["7", "Interview story bank", "Short and long versions of architecture, delivery, incident, tool, teamwork, and business-impact stories.", "Client interviews and recruiter prep"],
        ["8", "Resume bullets and project summary", "Final wording that maps role, tools, ownership, evidence, outcome, and business context.", "Resume tailoring, submissions, LinkedIn/profile summary"],
    ]


def _training_final_evidence_package_details(program: TrainingProgram) -> list[dict[str, list[str] | str]]:
    role_name = program.marketing_role.name
    domain_name = program.industry_domain
    tools = _split_training_items(program.marketing_role.common_tools)[:6]
    tool_text = ", ".join(tools) or role_name
    return [
        {
            "artifact": "Architecture diagram",
            "include": [
                "Business users or upstream system.",
                "Entry point such as web app, API, scheduler, pipeline, alert, or ticket.",
                "Runtime or platform layer such as cloud, Kubernetes, serverless, database, data platform, or ML platform.",
                "Observability, security, deployment, and support touchpoints.",
                f"Clear {role_name} ownership boundary.",
            ],
            "acceptance": [
                "The project flow is understandable from the diagram without extra decoding.",
                "The diagram marks what the role owned versus what other teams owned.",
                "No client secrets, real credentials, internal hostnames, or sensitive production values are exposed.",
            ],
            "interview": "This artifact supports system, architecture, project scope, integration, failure-point, and production-support questions.",
            "resume": "It supports one project summary line and two architecture or platform ownership bullets.",
        },
        {
            "artifact": "Workflow diagram",
            "include": [
                "Step-by-step path from request or trigger to final validation.",
                "Decision points, failure points, handoffs, approvals, and rollback or recovery steps.",
                "Inputs, outputs, and evidence produced at each stage.",
            ],
            "acceptance": [
                "The workflow can be told as a 60-second story.",
                "The workflow includes validation evidence, not only implementation steps.",
                "The workflow matches the resume story and interview examples.",
            ],
            "interview": "This artifact supports scenario questions such as failed deployment, noisy alert, slow pipeline, broken data flow, access issue, or production incident.",
            "resume": "It supports bullets about automation, repeatability, release readiness, operational process, or support handoff.",
        },
        {
            "artifact": "Use-case boundary sheet",
            "include": [
                f"Business context for {domain_name}.",
                "Owned responsibilities.",
                "Supported or contributed responsibilities.",
                "Teams or systems outside role ownership.",
                "Claims to avoid in interviews.",
            ],
            "acceptance": [
                "The boundary sheet prevents overclaiming.",
                "It clearly separates role ownership from product owner, developer, security, DBA, data, cloud, QA, or vendor ownership.",
                "It names the evidence used to prove each claim.",
            ],
            "interview": "This artifact supports exact-responsibility questions and keeps the story specific.",
            "resume": "It keeps resume bullets specific and defensible.",
        },
        {
            "artifact": "Tool configuration notes",
            "include": [
                f"Tools used: {tool_text}.",
                "Purpose of each tool.",
                "Important configuration choices.",
                "Output each tool produced such as deployment, dashboard, alert, report, policy, pipeline run, log, test result, or artifact.",
            ],
            "acceptance": [
                "Each tool is explained by purpose and output.",
                "The notes avoid generic tool lists.",
                "One configuration decision and one troubleshooting example are visible.",
            ],
            "interview": "This artifact supports tool deep-dive questions and follow-up probes.",
            "resume": "It supports keywords that can be explained with examples.",
        },
        {
            "artifact": "Screenshots or command outputs",
            "include": [
                "Safe screenshots of dashboards, pipeline runs, test results, deployment status, alerts, tickets, or validation output.",
                "Terminal or command output with secrets removed.",
                "Short caption explaining what each output proves.",
            ],
            "acceptance": [
                "No secrets, tokens, customer data, proprietary URLs, or sensitive production identifiers.",
                "Each screenshot or output has a caption.",
                "Each output maps to a resume bullet or interview story.",
            ],
            "interview": "This artifact makes answers concrete: dashboard checked, command run, test passed, pipeline failed, log confirmed, alert cleared.",
            "resume": "It supports evidence-backed project bullets.",
        },
        {
            "artifact": "Runbook and incident simulation",
            "include": [
                "Symptom and user or business impact.",
                "Signals checked: logs, metrics, alerts, traces, tickets, pipeline status, data freshness, model metrics, or cloud service status.",
                "Triage steps, escalation owner, recovery action, validation step, and prevention note.",
            ],
            "acceptance": [
                "The incident can be explained in STAR format.",
                "The runbook includes what to check first, second, and third.",
                "The simulation does not blame another team without evidence.",
            ],
            "interview": "This artifact supports production support, SRE, DevOps, platform, data reliability, MLOps, and behavioral questions.",
            "resume": "It supports bullets about troubleshooting, incident response, RCA, runbook creation, or reliability improvement.",
        },
        {
            "artifact": "Interview story bank",
            "include": [
                "60-second story and 5-minute story for the project.",
                "Architecture explanation.",
                "Workflow explanation.",
                "Incident/troubleshooting story.",
                "Tool configuration story.",
                "Team handoff and communication story.",
                "Business impact story.",
            ],
            "acceptance": [
                "Stories use real evidence from the package.",
                "Stories include ownership boundaries.",
                "Stories are ready for interview scoring.",
            ],
            "interview": "This artifact is the primary project-story source before client calls.",
            "resume": "Story titles align resume bullets with interview answers.",
        },
        {
            "artifact": "Resume bullets and project summary",
            "include": [
                "Project title and business context.",
                f"{role_name} ownership summary.",
                "Tools and systems used.",
                "Evidence-backed outcomes.",
                "Three to five resume bullets and one recruiter-facing project summary.",
            ],
            "acceptance": [
                "Every bullet maps to at least one evidence artifact.",
                "Bullets avoid unverifiable claims.",
                "Project summary can be spoken naturally in a screening call.",
            ],
            "interview": "This artifact supports the opening answer for project-introduction questions.",
            "resume": "This is the final source for resume, submission notes, LinkedIn summary, and recruiter pitch.",
        },
    ]


def _training_readiness_rows() -> list[dict[str, str]]:
    return [
        {"area": "Vocabulary", "ready": "Explains 50 role terms without reading notes.", "evidence": "Trainer random check and glossary review."},
        {"area": "Concepts", "ready": "Explains workflow, tools, architecture, support, security, and troubleshooting.", "evidence": "Concept map or whiteboard explanation."},
        {"area": "Use Cases", "ready": "Tells 10 stories naturally in 60-second and 5-minute versions.", "evidence": "Mock interview recording or trainer score."},
        {"area": "Interview Questions", "ready": "Answers screening, technical, scenario, and project questions with specifics.", "evidence": "Mock interview score marked market-ready."},
        {"area": "Resume", "ready": "Resume bullets match target JDs without copying the JD.", "evidence": "JD-to-resume matching checklist."},
        {"area": "Submission", "ready": "Visa, location, rate, availability, LinkedIn, and role summary are ready.", "evidence": "Submission checklist completed before marketing."},
    ]


def _assessment_question(
    *,
    section: str,
    source_type: str,
    source_title: str,
    source_section: str,
    topic: str,
    question_type: str,
    prompt: str,
    options: list[tuple[str, str]],
    correct: list[str],
    explanation: str,
    difficulty: str = "medium",
) -> dict[str, Any]:
    return {
        "id": "",
        "section": section,
        "sourceType": source_type,
        "sourceTitle": source_title,
        "sourceSection": source_section,
        "topic": topic,
        "type": question_type,
        "typeLabel": "Choose all correct answers" if question_type == "multi_select" else "Single best answer",
        "prompt": prompt,
        "options": [{"key": key, "text": text} for key, text in options],
        "correctAnswers": correct,
        "explanation": explanation,
        "difficulty": difficulty,
    }


def _training_onboarding_basic_prep_questions() -> list[dict[str, Any]]:
    return [
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 1: Terminal, Linux, Networking, And Troubleshooting",
            source_section="Commands and failure drill",
            topic="Terminal/Linux/Networking",
            question_type="single_choice",
            prompt="A web service is reported down. Which command best confirms whether the local HTTP health endpoint is responding?",
            options=[("A", "git log --oneline"), ("B", "curl -i http://localhost:8080/health"), ("C", "terraform plan"), ("D", "docker images")],
            correct=["B"],
            explanation="The Basic Prep Day 1 lab uses curl and health endpoints to separate application response from other layers.",
            difficulty="easy",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 1: Terminal, Linux, Networking, And Troubleshooting",
            source_section="Command map",
            topic="Terminal/Linux/Networking",
            question_type="multi_select",
            prompt="Which checks belong in the first service-down triage path from Basic Prep? Choose all correct answers.",
            options=[("A", "Process state"), ("B", "Listening port"), ("C", "DNS resolution"), ("D", "Health endpoint response"), ("E", "Create a Git tag first")],
            correct=["A", "B", "C", "D"],
            explanation="The Day 1 triage flow checks process, port, DNS, HTTP/health, permissions, logs, and recent context before guessing.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 2: Git, Branches, Pull Requests, And Release Evidence",
            source_section="Release traceability",
            topic="Git/Release Evidence",
            question_type="single_choice",
            prompt="A bad configuration reached staging. Which Git command is best for inspecting the code/config difference before writing the rollback note?",
            options=[("A", "git diff"), ("B", "kubectl get pods"), ("C", "aws s3 ls"), ("D", "terraform output")],
            correct=["A"],
            explanation="The Git day emphasizes status, diff, log, PR summary, and rollback notes as release evidence.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 2: Git, Branches, Pull Requests, And Release Evidence",
            source_section="PR evidence",
            topic="Git/Release Evidence",
            question_type="multi_select",
            prompt="Which items belong in a production-quality pull request or release evidence note? Choose all correct answers.",
            options=[("A", "What changed"), ("B", "Why it changed"), ("C", "Validation evidence"), ("D", "Rollback note"), ("E", "Secret values copied from production")],
            correct=["A", "B", "C", "D"],
            explanation="Basic Prep asks for PR summaries with change, reason, validation, and rollback while removing sensitive values.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 3: Docker Images, Containers, Logs, And Debugging",
            source_section="Container troubleshooting",
            topic="Docker",
            question_type="single_choice",
            prompt="A container is running locally but the app is unreachable. Which check from Basic Prep directly verifies host-to-container port mapping?",
            options=[("A", "docker port <container>"), ("B", "git branch"), ("C", "aws iam get-user"), ("D", "terraform fmt")],
            correct=["A"],
            explanation="Docker troubleshooting covers image tag, port mapping, environment variables, inspect output, and logs.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 3: Docker Images, Containers, Logs, And Debugging",
            source_section="Image/container model",
            topic="Docker",
            question_type="multi_select",
            prompt="Which Docker concepts are explicitly part of the Day 3 image/container/runtime model? Choose all correct answers.",
            options=[("A", "Image"), ("B", "Container"), ("C", "Registry"), ("D", "Environment variable"), ("E", "Jira epic")],
            correct=["A", "B", "C", "D"],
            explanation="The Docker day covers image, container, registry, port mapping, environment variables, volumes, Compose, and logs.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 4: Kubernetes Core Objects And Failure Reading",
            source_section="CrashLoopBackOff triage",
            topic="Kubernetes",
            question_type="multi_select",
            prompt="A pod is in CrashLoopBackOff. Which commands help investigate the issue in the Basic Prep Kubernetes flow? Choose all correct answers.",
            options=[("A", "kubectl describe pod <pod> -n app"), ("B", "kubectl logs <pod> -n app"), ("C", "kubectl get events -n app --sort-by=.lastTimestamp"), ("D", "kubectl rollout status deploy/app -n app"), ("E", "git tag release-001")],
            correct=["A", "B", "C", "D"],
            explanation="The Kubernetes day uses describe, logs, events, rollout status/history, and service/deployment state to read failures.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 4: Kubernetes Core Objects And Failure Reading",
            source_section="Core objects",
            topic="Kubernetes",
            question_type="single_choice",
            prompt="Which Kubernetes object manages rollout, replicas, and pod replacement for an application?",
            options=[("A", "Deployment"), ("B", "Git branch"), ("C", "S3 bucket"), ("D", "Terraform state file")],
            correct=["A"],
            explanation="The Basic Prep Kubernetes reference defines deployment as the controller that manages rollout, replicas, and pod replacement.",
            difficulty="easy",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 5: Cloud Basics: Network, Identity, Compute, Storage",
            source_section="Cloud foundation sketch",
            topic="Cloud Basics",
            question_type="single_choice",
            prompt="Which command from the cloud basics day proves the active AWS identity before checking cloud resources?",
            options=[("A", "aws sts get-caller-identity"), ("B", "docker ps"), ("C", "git diff"), ("D", "kubectl top pods")],
            correct=["A"],
            explanation="Cloud foundation starts with account/project context and identity before reading storage, compute, and network resources.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 5: Cloud Basics: Network, Identity, Compute, Storage",
            source_section="Cloud vocabulary",
            topic="Cloud Basics",
            question_type="multi_select",
            prompt="Which building blocks belong in the Basic Prep cloud foundation sketch? Choose all correct answers.",
            options=[("A", "Account or subscription boundary"), ("B", "Region"), ("C", "IAM access"), ("D", "Storage and compute"), ("E", "Unreviewed production password screenshot")],
            correct=["A", "B", "C", "D"],
            explanation="The cloud day maps account/subscription/project, region, IAM, storage, compute, network, cost guardrails, backup, and recovery.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 6: CI/CD, Artifacts, Release, Rollback, And Checkpoint Test",
            source_section="Release evidence chain",
            topic="CI/CD",
            question_type="single_choice",
            prompt="A pipeline failed after tests passed but before deployment. What should the learner identify first?",
            options=[("A", "The failed pipeline stage and evidence from the run/log"), ("B", "The candidate's resume bullet"), ("C", "A new cloud provider"), ("D", "A database schema unrelated to the release")],
            correct=["A"],
            explanation="The CI/CD day frames releases as evidence flow: commit, build, test, scan, artifact/image, deployment, validation, rollback.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 6: CI/CD, Artifacts, Release, Rollback, And Checkpoint Test",
            source_section="Release safety",
            topic="CI/CD",
            question_type="multi_select",
            prompt="Which evidence items make a release safer according to the Basic Prep CI/CD material? Choose all correct answers.",
            options=[("A", "Artifact or image tag"), ("B", "Deployment status"), ("C", "Health check or smoke test"), ("D", "Rollback trigger/path"), ("E", "Skipping approval and monitoring")],
            correct=["A", "B", "C", "D"],
            explanation="Release confidence depends on artifact/image, deployment, validation, monitoring, approval, and rollback evidence.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 7: Observability, Logs, Metrics, Traces, And Alerts",
            source_section="Golden signals",
            topic="Observability",
            question_type="multi_select",
            prompt="Users report slowness with no obvious error. Which golden signals are named in Basic Prep? Choose all correct answers.",
            options=[("A", "Latency"), ("B", "Traffic"), ("C", "Errors"), ("D", "Saturation"), ("E", "Pull request title length")],
            correct=["A", "B", "C", "D"],
            explanation="The observability day uses latency, traffic, errors, and saturation, plus logs, traces, alerts, timelines, and runbooks.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 8: Terraform, Infrastructure As Code, And Change Safety",
            source_section="Plan review",
            topic="Terraform/IaC",
            question_type="single_choice",
            prompt="A Terraform plan wants to destroy a shared resource. What is the safest Basic Prep action before apply?",
            options=[("A", "Review create/update/destroy risk and approval needs"), ("B", "Apply immediately because the plan exists"), ("C", "Delete the state file"), ("D", "Ignore drift and move to interview practice")],
            correct=["A"],
            explanation="The Terraform day teaches reading a plan as a production change request before applying risk.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 9: Security, Secrets, IAM, And Audit Basics",
            source_section="Access denied evidence",
            topic="Security/IAM/Secrets",
            question_type="multi_select",
            prompt="A deployment cannot access a secret. Which evidence should be checked without exposing sensitive values? Choose all correct answers.",
            options=[("A", "Identity or service account"), ("B", "Role/policy or RBAC assignment"), ("C", "Secret reference and audit log"), ("D", "Secret value copied into a ticket"), ("E", "Certificate or rotation status when relevant")],
            correct=["A", "B", "C", "E"],
            explanation="Security basics focus on least privilege, identity, policy, secret manager/key vault, audit logs, rotation, and safe evidence handling.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 10: Agile, Jira, APIs, JSON/YAML, SQL, Cost, And Evidence",
            source_section="Sprint and evidence package",
            topic="Agile/API/SQL/Evidence",
            question_type="multi_select",
            prompt="A vague ticket says 'fix deployment issue' and an API/data/config signal is unclear. Which items belong in the Day 10 evidence package? Choose all correct answers.",
            options=[("A", "Acceptance criteria"), ("B", "API status/response evidence"), ("C", "JSON/YAML or config check"), ("D", "SQL row-count or null-check evidence"), ("E", "No owner route or validation")],
            correct=["A", "B", "C", "D"],
            explanation="The condensed Day 10 combines Jira story quality with API, config, SQL, cost/risk, validation, and handoff evidence.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 11: Ansible, Shell And Python Automation, And Runbooks",
            source_section="Automation comparison",
            topic="Ansible/Shell/Python",
            question_type="single_choice",
            prompt="Which statement best matches the Day 11 automation framing?",
            options=[("A", "Ansible supports repeatable remote configuration, Shell is good for quick system commands, and Python is stronger for APIs/JSON/YAML workflows."), ("B", "Only Python should be used for every operations task."), ("C", "Ansible inventory and SSH access are unrelated to automation."), ("D", "Shell scripts do not need exit codes or validation.")],
            correct=["A"],
            explanation="Day 11 distinguishes Ansible, Shell, and Python by input, output, failure signal, validation, owner handoff, and repeatability.",
        ),
        _assessment_question(
            section="Basic Prep Core",
            source_type="basic_prep",
            source_title="Day 12: Enterprise Lifecycle, Cutover, Final Evidence Package, And Final Exam",
            source_section="Lifecycle and cutover",
            topic="Enterprise Lifecycle/Cutover",
            question_type="multi_select",
            prompt="Which controls belong in the Day 12 cutover and final evidence package? Choose all correct answers.",
            options=[("A", "Sunrise and sunset checklist"), ("B", "Rollback and smoke test"), ("C", "Monitoring/runbook/support handoff"), ("D", "Access and cost cleanup"), ("E", "No validation after traffic moves")],
            correct=["A", "B", "C", "D"],
            explanation="The final day combines lifecycle, cutover, rollback, monitoring, support handoff, cleanup, and readiness evidence.",
        ),
    ]


def _program_text_options(values: list[str], fallback: list[str], limit: int = 4) -> list[str]:
    options = [_pdf_clean_text(value) for value in values if _pdf_clean_text(value)]
    options.extend(item for item in fallback if item not in options)
    return _dedupe_preserve_order(options)[:limit]


def _training_onboarding_role_domain_questions(program: TrainingProgram) -> list[dict[str, Any]]:
    role_name = program.marketing_role.name
    domain_name = program.industry_domain
    architecture = program.cloud_architecture or {}
    tools = _program_text_options(_as_list(program.tools_and_technologies), _split_training_items(program.marketing_role.common_tools)[:8] or [role_name], 6)
    applications = _program_text_options(_as_list(program.application_landscape), [domain_name, role_name, "Primary application"], 6)
    responsibilities = _program_text_options(_as_list(program.project_responsibilities), [program.marketing_role.description, "Validate and document the assigned workflow"], 6)
    deliverables = _program_text_options(_as_list(program.key_deliverables), ["Architecture diagram", "Runbook", "Evidence note", "Interview story"], 6)
    support_scenarios = _program_text_options(_as_list(program.production_support_scenarios), ["Failed release or production issue", "Access or configuration failure"], 4)
    glossary = [item for item in _as_list(architecture.get("productGlossary")) if isinstance(item, dict)]
    glossary_terms = _program_text_options([str(item.get("term", "")) for item in glossary], ROLE_TERMS.get(role_name, [])[:6], 6)
    glossary_meaning = _pdf_clean_text(glossary[0].get("productMeaning")) if glossary else program.marketing_role.description
    use_cases = [item for item in _as_list(architecture.get("deliveredUseCases")) if isinstance(item, dict)]
    use_case = use_cases[0] if use_cases else {}
    use_case_title = _pdf_clean_text(use_case.get("title")) or f"{domain_name} {role_name} delivery use case"
    use_case_scope = _program_text_options(_as_list(use_case.get("deliveredScope")), responsibilities[:4], 4)
    use_case_evidence = _program_text_options(_as_list(use_case.get("evidenceToExplain")), deliverables[:4], 4)
    provider_options = _program_text_options(_as_list(architecture.get("cloudProviderOptions")), ["AWS", "Azure", "GCP"], 4)
    interview_bank = architecture.get("maasInterviewBenchmark", {}) if isinstance(architecture.get("maasInterviewBenchmark"), dict) else {}
    benchmark_questions = [
        _pdf_clean_text(item.get("question"))
        for item in _as_list(interview_bank.get("questionBank"))
        if isinstance(item, dict) and _pdf_clean_text(item.get("question"))
    ]
    first_benchmark = benchmark_questions[0] if benchmark_questions else f"How does a {role_name} support {domain_name} systems?"

    return [
        _assessment_question(
            section="Selected Role And Domain",
            source_type="marketing_role_domain",
            source_title=f"{role_name} / {domain_name}",
            source_section="Program identity",
            topic="Role/domain fit",
            question_type="single_choice",
            prompt="Which marketing role is this onboarding assessment tied to?",
            options=[("A", role_name), *[(chr(66 + index), value) for index, value in enumerate([name for name in MARKETING_ROLE_NAMES if name != role_name][:3])]],
            correct=["A"],
            explanation=f"The assessment role section is generated from the selected training program: {role_name}.",
            difficulty="easy",
        ),
        _assessment_question(
            section="Selected Role And Domain",
            source_type="marketing_role_domain",
            source_title=f"{role_name} / {domain_name}",
            source_section="Program identity",
            topic="Domain fit",
            question_type="single_choice",
            prompt="Which industry domain is this onboarding assessment tied to?",
            options=[("A", domain_name), *[(chr(66 + index), value) for index, value in enumerate([name for name in INDUSTRY_DOMAINS if name != domain_name][:3])]],
            correct=["A"],
            explanation=f"The domain section must stay tied to the selected program domain: {domain_name}.",
            difficulty="easy",
        ),
        _assessment_question(
            section="Selected Role And Domain",
            source_type="marketing_role_domain",
            source_title=f"{role_name} / {domain_name}",
            source_section="Role vocabulary",
            topic="Role vocabulary",
            question_type="multi_select",
            prompt=f"Which terms are part of the {role_name} role vocabulary or selected program glossary? Choose all correct answers.",
            options=[("A", glossary_terms[0]), ("B", glossary_terms[1] if len(glossary_terms) > 1 else tools[0]), ("C", glossary_terms[2] if len(glossary_terms) > 2 else tools[-1]), ("D", "Unrelated resume formatting"), ("E", "Generic salary negotiation")],
            correct=["A", "B", "C"],
            explanation="Role vocabulary is drawn from the selected marketing role glossary and program product glossary.",
        ),
        _assessment_question(
            section="Selected Role And Domain",
            source_type="marketing_role_domain",
            source_title=f"{role_name} / {domain_name}",
            source_section="Product glossary",
            topic="Domain vocabulary",
            question_type="single_choice",
            prompt=f"In this program glossary, what should the consultant connect '{glossary_terms[0]}' to?",
            options=[("A", glossary_meaning), ("B", f"A topic outside {domain_name} role/domain training"), ("C", "A copied secret value"), ("D", "An unrelated personal profile item")],
            correct=["A"],
            explanation="The glossary question is generated from the selected role/domain product glossary.",
        ),
        _assessment_question(
            section="Selected Role And Domain",
            source_type="marketing_role_domain",
            source_title=f"{role_name} / {domain_name}",
            source_section="Application landscape",
            topic="Domain systems",
            question_type="multi_select",
            prompt=f"Which systems are listed in the {domain_name} application landscape for this program? Choose all correct answers.",
            options=[("A", applications[0]), ("B", applications[1] if len(applications) > 1 else applications[0]), ("C", applications[2] if len(applications) > 2 else applications[-1]), ("D", "Unassigned personal email inbox"), ("E", "Unrelated movie database")],
            correct=["A", "B", "C"],
            explanation="Application-landscape questions are generated only from the selected training program's application list.",
        ),
        _assessment_question(
            section="Selected Role And Domain",
            source_type="marketing_role_domain",
            source_title=f"{role_name} / {domain_name}",
            source_section="Tools and technologies",
            topic="Role tools",
            question_type="multi_select",
            prompt=f"Which tools or technologies are part of this {role_name} / {domain_name} training program? Choose all correct answers.",
            options=[("A", tools[0]), ("B", tools[1] if len(tools) > 1 else tools[0]), ("C", tools[2] if len(tools) > 2 else tools[-1]), ("D", "Unapproved credential sharing"), ("E", "Personal tax filing")],
            correct=["A", "B", "C"],
            explanation="The tool question is generated from the selected program's tools and the role's common tools.",
        ),
        _assessment_question(
            section="Selected Role And Domain",
            source_type="marketing_role_domain",
            source_title=f"{role_name} / {domain_name}",
            source_section="Cloud architecture",
            topic="Cloud/provider options",
            question_type="multi_select",
            prompt="Which cloud provider options are listed in this role/domain architecture material? Choose all correct answers.",
            options=[("A", provider_options[0]), ("B", provider_options[1] if len(provider_options) > 1 else provider_options[0]), ("C", provider_options[2] if len(provider_options) > 2 else provider_options[-1]), ("D", "Untracked local laptop only"), ("E", "No provider option")],
            correct=["A", "B", "C"],
            explanation="Provider options come from the selected program's cloud architecture material.",
        ),
        _assessment_question(
            section="Selected Role And Domain",
            source_type="marketing_role_domain",
            source_title=f"{role_name} / {domain_name}",
            source_section="Delivered use case",
            topic="Use case workflow",
            question_type="single_choice",
            prompt=f"The role/domain use case '{use_case_title}' should primarily be explained through which kind of material?",
            options=[("A", "Business problem, role boundary, delivered scope, evidence, and interview narrative"), ("B", "Only unrelated tool definitions"), ("C", "Only personal background details"), ("D", "Only a copied job description")],
            correct=["A"],
            explanation="Delivered use cases in the training material include business problem, role boundary, delivered scope, evidence, and interview answer.",
        ),
        _assessment_question(
            section="Selected Role And Domain",
            source_type="marketing_role_domain",
            source_title=f"{role_name} / {domain_name}",
            source_section="Delivered scope",
            topic="Role responsibilities",
            question_type="multi_select",
            prompt=f"Which items are role/domain scope or responsibility signals for this program? Choose all correct answers.",
            options=[("A", use_case_scope[0]), ("B", use_case_scope[1] if len(use_case_scope) > 1 else responsibilities[0]), ("C", responsibilities[0]), ("D", "Claiming ownership of unrelated teams"), ("E", "Skipping validation evidence")],
            correct=["A", "B", "C"],
            explanation="Scope and responsibility questions are generated from delivered use cases and project responsibilities.",
        ),
        _assessment_question(
            section="Selected Role And Domain",
            source_type="marketing_role_domain",
            source_title=f"{role_name} / {domain_name}",
            source_section="Evidence to explain",
            topic="Evidence artifacts",
            question_type="multi_select",
            prompt="Which artifacts are valid evidence anchors in this selected training program? Choose all correct answers.",
            options=[("A", use_case_evidence[0]), ("B", use_case_evidence[1] if len(use_case_evidence) > 1 else deliverables[0]), ("C", deliverables[0]), ("D", "Sensitive values pasted into screenshots"), ("E", "Unsupported claims without artifact")],
            correct=["A", "B", "C"],
            explanation="Evidence anchors are drawn from the selected program's use-case evidence and key deliverables.",
        ),
        _assessment_question(
            section="Selected Role And Domain",
            source_type="marketing_role_domain",
            source_title=f"{role_name} / {domain_name}",
            source_section="Production support scenarios",
            topic="Troubleshooting",
            question_type="single_choice",
            prompt=f"When answering a {role_name} troubleshooting question for {domain_name}, which response style best matches the selected program material?",
            options=[("A", "Name the symptom, checked layer, evidence, validation, and owner boundary"), ("B", "List tool names without system behavior"), ("C", "Blame another team before reading evidence"), ("D", "Skip the domain workflow")],
            correct=["A"],
            explanation=f"Production support scenarios in this program include examples such as: {support_scenarios[0]}.",
        ),
        _assessment_question(
            section="Selected Role And Domain",
            source_type="marketing_role_domain",
            source_title=f"{role_name} / {domain_name}",
            source_section="Interview benchmark",
            topic="Project/interview scenario",
            question_type="single_choice",
            prompt=f"Which source should the onboarding test use for the role/domain interview scenario '{first_benchmark}'?",
            options=[("A", "Selected marketing role and selected domain training material"), ("B", "A generic public certification bank"), ("C", "Random questions outside the course"), ("D", "A different consultant's assigned role")],
            correct=["A"],
            explanation="The onboarding assessment is intentionally restricted to Basic Prep plus the selected role/domain program.",
        ),
    ]


def _training_onboarding_assessment(program: TrainingProgram) -> dict[str, Any]:
    basic_questions = _training_onboarding_basic_prep_questions()
    role_questions = _training_onboarding_role_domain_questions(program)
    questions = basic_questions + role_questions
    for index, question in enumerate(questions, start=1):
        question["id"] = f"Q{index:02d}"
    return {
        "title": "Onboarding Readiness Assessment",
        "subtitle": f"Basic Prep + {program.marketing_role.name} / {program.industry_domain}",
        "totalQuestions": len(questions),
        "timeLimitMinutes": 45,
        "passingScore": 75,
        "sourceRule": "Questions are generated only from Basic Prep and the selected marketing role/domain training material.",
        "allowedTypes": ["single_choice", "multi_select"],
        "scoringRules": [
            "Single-answer questions: 1 point for the exact correct answer, 0 for wrong.",
            "Choose-all-that-apply questions: exact match required, 0 for missing or extra choices.",
            "No short answers, no outside certification-bank questions, and no partial credit.",
        ],
        "sections": [
            {"name": "Basic Prep Core", "questionCount": len(basic_questions), "source": "12-day Basic Prep course"},
            {"name": "Selected Role And Domain", "questionCount": len(role_questions), "source": f"{program.marketing_role.name} / {program.industry_domain} material"},
        ],
        "resultBands": [
            {"range": "85-100%", "label": "Ready", "action": "Start selected role/domain training."},
            {"range": "75-84%", "label": "Ready with review", "action": "Start role/domain training and assign weak-topic review."},
            {"range": "60-74%", "label": "Conditional", "action": "Repair weak Basic Prep topics before full role/domain pace."},
            {"range": "Below 60%", "label": "Not ready", "action": "Complete Basic Prep before role/domain training."},
        ],
        "questions": questions,
    }


def _training_program_pdf_blocks(program: TrainingProgram, *, include_diagrams: bool = True) -> list[dict[str, str]]:
    architecture = program.cloud_architecture or {}
    blocks: list[dict[str, str]] = []
    role_name = program.marketing_role.name
    domain_name = program.industry_domain
    program_title = program.title or f"{domain_name} - {role_name} Training Program"

    def add(text: Any = "", style: str = "body", *, page_break: bool = False, box: str = "") -> None:
        if page_break:
            blocks.append({"text": "", "style": "page_break"})
        value = _pdf_clean_text(text)
        if value or style == "space":
            block = {"text": value, "style": style}
            if box:
                block["box"] = box
            blocks.append(block)

    def add_space() -> None:
        blocks.append({"text": "", "style": "space"})

    def add_list(items: Any, *, prefix: str = "-", style: str = "body") -> None:
        for item in _as_list(items):
            add(f"{prefix} {_pdf_clean_text(item)}", style)

    def add_callout(title: str, body: Any, kind: str = "key") -> None:
        add(title, "callout_title", box=kind)
        if isinstance(body, (list, tuple, set)):
            add_list(body, prefix="-", style="callout")
        else:
            add(body, "callout", box=kind)
        add_space()

    def add_table(headers: list[str], rows: list[list[Any]]) -> None:
        add(" | ".join(headers), "table_header")
        for row in rows:
            add(" | ".join(_pdf_clean_text(cell) for cell in row), "table_row")
        add_space()

    def add_visual_flow(title: str, steps: list[Any]) -> None:
        clean_steps = [_pdf_clean_text(step) for step in steps if _pdf_clean_text(step)]
        if clean_steps:
            blocks.append({"text": f"{_pdf_clean_text(title)}||{'|'.join(clean_steps[:6])}", "style": "visual_flow"})
            add_space()

    def add_provider_architecture_diagram(diagram: dict[str, Any]) -> None:
        payload = {
            "title": diagram.get("title", ""),
            "provider": diagram.get("provider", "Cloud"),
            "nodes": [
                {
                    "label": node.get("label", ""),
                    "items": _as_list(node.get("items"))[:4],
                    "kind": node.get("kind", ""),
                }
                for node in _as_list(diagram.get("nodes"))[:6]
                if isinstance(node, dict)
            ],
            "evidence": _as_list(diagram.get("evidence"))[:5],
        }
        blocks.append({"text": json.dumps(payload), "style": "provider_arch"})
        add_space()

    def add_datadog_reference_diagram(item: dict[str, Any]) -> None:
        title = str(item.get("title") or "Datadog Reference Diagram")
        lower_title = title.lower()
        if "observability pipelines" in lower_title:
            nodes = [
                {"label": "Telemetry sources", "items": ["pods", "nodes", "services", "events"], "kind": "problem"},
                {"label": "Collect", "items": ["agent", "logs", "metrics", "traces"]},
                {"label": "Filter/redact", "items": ["sensitive fields", "noise", "routing rules"], "kind": "role"},
                {"label": "Route/store", "items": ["critical logs", "retention tier", "cost control"]},
                {"label": "Incident proof", "items": ["query", "dashboard", "runbook"], "kind": "support"},
            ]
        elif "cluster agent" in lower_title:
            nodes = [
                {"label": "Kubernetes cluster", "items": ["pods", "nodes", "services", "metadata"], "kind": "problem"},
                {"label": "Node agents", "items": ["local checks", "container metrics", "logs"]},
                {"label": "Cluster Agent", "items": ["service discovery", "metadata", "coordination"], "kind": "role"},
                {"label": "Datadog", "items": ["workload view", "monitors", "service map"]},
                {"label": "SRE action", "items": ["triage", "scale/rollback", "runbook"], "kind": "support"},
            ]
        else:
            nodes = [
                {"label": "Healthcare workload", "items": ["service", "pod", "node", "deployment"], "kind": "problem"},
                {"label": "Datadog Agent", "items": ["metrics", "logs", "traces"]},
                {"label": "APM + infra", "items": ["trace waterfall", "errors", "saturation"], "kind": "role"},
                {"label": "Service map", "items": ["dependencies", "owner", "recent change"]},
                {"label": "Incident evidence", "items": ["alert", "dashboard", "RCA"], "kind": "support"},
            ]
        add_provider_architecture_diagram(
            {
                "title": f"{title} - Visual Reference Flow",
                "provider": "Datadog",
                "nodes": nodes,
                "evidence": _as_list(item.get("evidenceToCollect"))[:5],
            }
        )

    def add_chapter(number: int, title: str, overview: str, learn: list[str], terms: list[str], interview: str) -> None:
        add(f"Chapter {number}", "chapter_number", page_break=True)
        add(title, "chapter_title")
        add(overview, "chapter_intro")
        add_callout("What the reader will learn", learn, "learn")
        add_callout("Key terms covered", terms, "key")
        add_callout("Why this chapter matters in interviews", interview, "interview")

    def add_chapter_close(takeaways: list[str], mistakes: list[str], questions: list[str]) -> None:
        add("Chapter summary", "section")
        add_callout("Functional takeaways", takeaways, "key")
        add_callout("Common weak spots", mistakes, "warning")
        add_callout("Concept checks", questions, "practice")

    def add_role_answer_workspace() -> None:
        add("Scenario notes", "subsection")
        add("- Functional flow: ________________________________________________________", "body")
        add("- System behavior: ________________________________________________________", "body")
        add("- Role-owned change: ______________________________________________________", "body")
        add("- Validation signal: ______________________________________________________", "body")
        add("- Failure behavior: _______________________________________________________", "body")
        add("- Evidence artifact: ______________________________________________________", "body")
        add("- Interview narrative: ____________________________________________________", "body")
        add("", "space")

    def add_role_domain_rehearsal_appendix() -> None:
        scenario_views = [
            ("Business Function View", "Business workflow, users, line-of-business impact, and why the capability matters."),
            ("System Flow View", "Request, release, data, or incident flow with ownership boundaries and downstream dependencies."),
            ("Provider Reference View", "Official provider reference pattern and why it matches the implemented use case."),
            ("Implementation Behavior View", "Configured, automated, monitored, migrated, deployed, or validated behavior."),
            ("Jira Delivery View", "Jira story title, acceptance criteria, implementation note, and done evidence."),
            ("Diagram Explanation View", "Diagram path from trigger to outcome, with the role-owned portion visible."),
            ("Failure Behavior View", "Failure symptom, changed signal, affected system, and owner of the next action."),
            ("Troubleshooting Signal View", "First diagnostic checks and the evidence each check produces."),
            ("Observability View", "Metric, log, trace, dashboard, alert, or report connected to the use case."),
            ("Security And Access View", "IAM, secrets, audit, encryption, approval, or policy concern in the flow."),
            ("Data And State View", "Data object, state transition, schema, queue, warehouse, or model artifact involved."),
            ("Automation And Recovery View", "Automated behavior, approval point, rollback path, and recovery evidence."),
            ("Cloud Reliability View", "Availability, backup, DR, scaling, cost, or performance behavior."),
            ("Product System View", "Product systems touched and the user-facing or business-facing impact."),
            ("Resume Evidence View", "Truthful project bullet supported by the scenario evidence."),
            ("Short Narrative View", "Problem, role-owned action, evidence, and result in a compact story."),
            ("Deep-Dive Narrative View", "Architecture, role boundary, failure handling, and outcome."),
            ("Follow-Up Question View", "Likely follow-up areas and the evidence connected to each one."),
            ("Ownership Boundary View", "Owned, contributed, reviewed, supported, and non-owned areas."),
            ("Incident Timeline View", "Incident timeline, suspected layer, action taken, validation, and prevention note."),
            ("Review Artifact View", "Artifacts visible for validating the use-case story."),
            ("JD Signal View", "Common active JD wording and the use-case behavior that maps to it."),
            ("Domain Transfer View", "How the same pattern sounds in another domain without client-specific claims."),
            ("Final Scenario View", "Clear project story with evidence, boundaries, and functional outcome."),
        ]
        use_cases = [item for item in _as_list(architecture.get("deliveredUseCases")) if isinstance(item, dict)]
        if not use_cases:
            return
        add("600-Page Role And Domain Scenario Workbook", "section", page_break=True)
        add(
            f"These pages expand the {role_name} / {domain_name} program into a functionality-first scenario workbook. Each page explains business flow, system behavior, role-owned change, validation, failure behavior, evidence, and interview narrative.",
            "chapter_intro",
        )
        add_callout(
            "Scope boundary",
            "The role/domain workbook stays tied to the selected marketing role and industry domain. It explains realistic project behavior without claiming client-specific details outside the training narrative.",
            "warning",
        )
        for use_case_index, item in enumerate(use_cases, start=1):
            title = item.get("title", f"Use Case {use_case_index}")
            evidence = _as_list(item.get("evidenceToExplain"))
            stories = [story for story in _as_list(item.get("jiraStories")) if isinstance(story, dict)]
            for sheet_index, (sheet_title, prompt) in enumerate(scenario_views[:4], start=1):
                add(f"Use Case Scenario {use_case_index}.{sheet_index}: {sheet_title}", "section", page_break=True)
                add_callout("Use case", title, "key")
                add_callout("Functional lens", prompt, "practice")
                add_callout("Business problem", item.get("businessProblem"), "warning")
                add_callout("Role boundary", item.get("roleBoundary"), "interview")
                add_callout("Implemented behavior", item.get("deliveredScope"), "learn")
                if include_diagrams:
                    add_callout(
                        "Diagram path",
                        [
                            "Business trigger -> Architecture/workflow -> Role-owned change -> Validation signal -> Outcome",
                            "The diagram path keeps the functional behavior visible without adding unrelated theory.",
                        ],
                        "diagram",
                    )
                if evidence:
                    add_callout("Evidence anchor", evidence[(sheet_index - 1) % len(evidence)], "key")
                if stories:
                    story = stories[(sheet_index - 1) % len(stories)]
                    add_callout("Jira anchor", [story.get("key", ""), story.get("title", ""), story.get("summary", "")], "practice")
                add_callout("Interview narrative", item.get("interviewAnswer") or item.get("interviewStory") or item.get("whatToRemember"), "interview")
                add_role_answer_workspace()
                add_callout(
                    "Scenario artifact summary",
                    [
                        "Functional flow:",
                        "System behavior:",
                        "Role-owned change:",
                        "Validation evidence:",
                        "Interview narrative:",
                    ],
                    "learn",
                )

    chapters = [
        "How This Program Is Structured",
        "Project Context",
        "Application Landscape",
        "Enterprise Architecture",
        "Role Ownership and Responsibility Boundaries",
        "Tools and Technologies",
        "Product System Deep Dives",
        "Delivered Use Cases",
        "Jira Stories and Sprint Evidence",
        "Production Support and Troubleshooting",
        "Interview Preparation",
        "Resume Summary and Marketing Positioning",
        "Final Evidence Package",
        "Glossary and Quick Revision Notes",
    ]

    add("Mintel Consultant Training Book", "cover")
    add(program_title, "title")
    add(program.short_description, "cover_summary")
    add(f"Role: {role_name}", "cover_meta")
    add(f"Industry Domain: {domain_name}", "cover_meta")
    add(f"Version: {datetime.now().strftime('%b %d, %Y')}", "cover_meta")
    add("Prepared by Mintel", "cover_meta")
    add_callout(
        "Book purpose",
        "A print-ready role/domain reference guide for understanding product flow, architecture behavior, production support stories, and interview context.",
        "interview",
    )
    add_callout(
        "Diagram setting",
        "Diagrams are included in this material." if include_diagrams else "This export is text-only. Diagram chapters were intentionally skipped.",
        "diagram" if include_diagrams else "key",
    )

    add("Front Matter", "section", page_break=True)
    add_callout(
        "Usage note",
        "This guide explains the product flow, ownership boundaries, evidence, and delivery stories behind the selected role and domain.",
        "key",
    )
    add_callout(
        "How the book is organized",
        [
            "Project context explains the business environment and team model.",
            "Architecture and workflows explain how systems behave from trigger to outcome.",
            "Product system pages explain what each application does and where failures occur.",
            "Use case studies connect business need, role-owned change, evidence, and outcome.",
            "Glossary and revision notes define the product and role vocabulary.",
        ],
        "learn",
    )
    add_callout(
        "Learning outcomes",
        [
            f"Explain {domain_name} product systems from a business and engineering point of view.",
            "Master 10-12 use cases with reference architecture, diagrams, evidence, troubleshooting, and interview stories.",
            f"Describe {role_name} ownership without claiming unrelated application, business, or security ownership.",
            "Walk through request flow, release flow, incident flow, and evidence produced.",
            "Answer architecture, scenario, troubleshooting, and behavioral questions with specific examples.",
            "Connect resume bullets to delivered use cases and production support outcomes.",
        ],
        "interview",
    )
    add("Concept Coverage Map", "section", page_break=True)
    add(
        "Every concept is explained either in Basics Prep or in the role/domain company context, then connected to system behavior, project scenarios, validation checks, and evidence artifacts.",
        "chapter_intro",
    )
    add_callout(
        "Business/domain reading lens",
        [
            "Start with the business capability, actor, workflow, business rule, exception path, KPI, and report before moving into architecture.",
            "Connect each use case to the line of business it supports and the system of record or system of engagement involved.",
            "Separate normal business flow from exception flow, approval flow, audit flow, and support flow.",
            "Tie technical evidence back to business value: SLA, KPI, report freshness, reconciliation, auditability, customer experience, or operational recovery.",
        ],
        "key",
    )
    for item in _training_concept_coverage_map(program):
        add(item["area"], "subsection")
        add_callout("Where it appears", item["where"], "key")
        add_callout("Functional meaning", item["practice"], "practice")
        add_callout("Evidence visible", item["proof"], "interview")
        add_callout("Concepts", item["concepts"][:40], "learn")
    add("Table Of Contents", "section")
    for index, chapter in enumerate(chapters, start=1):
        add(f"Chapter {index}: {chapter}", "toc")

    add_chapter(
        1,
        "How This Program Is Structured",
        "This chapter frames the program as a business and systems story. The material starts with domain context, then moves into architecture, product workflows, role-owned implementation behavior, evidence, and interview narrative.",
        ["Domain context", "System behavior", "Workflow diagrams", "Evidence signals", "Ownership boundary"],
        ["Story", "Diagram", "Workflow", "Evidence", "Boundary", "Outcome"],
        "Interviewers listen for business context, functional flow, ownership boundary, failure behavior, and evidence-backed outcomes.",
    )
    add_callout(
        "Functional reading model",
        "Every topic is explained through four questions: what is happening, why it matters, what can fail, and which evidence shows the outcome.",
        "key",
    )
    add_visual_flow(
        "Functional Understanding Loop",
        ["Business context", "System picture", "Role-owned behavior", "Failure check", "Evidence", "Interview narrative"],
    )
    if include_diagrams:
        add("25 Diagram Workbook", "section")
        add(
            "Every full document includes this diagram workbook so the role is explained through business flows, line-of-business maps, customer journeys, system paths, evidence paths, and interview story structure.",
            "body",
        )
        for diagram in _training_document_diagram_workbook(program):
            add(diagram["title"], "subsection")
            add_callout("Purpose", diagram["purpose"], "key")
            if diagram.get("nodes"):
                add_provider_architecture_diagram(diagram)
            add_visual_flow(diagram["title"], diagram["steps"])
            add_callout("Evidence to connect", diagram["evidence"], "diagram")
        microsoft_references = _microsoft_healthcare_customer_story_references(program)
        if microsoft_references:
            add("Role-Relevant Microsoft Healthcare Azure Reference Diagrams", "section")
            add(
                "These diagrams use a small set of official Microsoft healthcare Azure customer stories as reference architecture examples. The goal is clear consultant explanation, not collecting every possible story.",
                "body",
            )
            for reference in microsoft_references:
                add(f"{reference['customer']}: {reference['title']}", "subsection")
                add_callout("Official Microsoft source", reference["sourceUrl"], "diagram")
                add_provider_architecture_diagram(reference["diagram"])
                add_callout("Business problem", reference["businessProblem"], "warning")
                add_callout("Microsoft services", reference["microsoftServices"], "key")
                add_callout("Published outcomes to reference", reference["outcomes"], "interview")
                add_callout("Role-specific use", reference["roleUse"], "learn")
        add("Core architecture and training diagrams", "section")
        for diagram in _training_architecture_diagram_cards(program):
            add(diagram["title"], "subsection")
            add_callout("Purpose", diagram["purpose"], "key")
            add_visual_flow(diagram["title"], [node["label"] for node in diagram["nodes"]])
            add_table(
                ["Layer", "Functional meaning"],
                [[layer["label"], layer["text"]] for layer in diagram["layers"]],
            )
            add_callout("Evidence to collect", diagram["evidence"], "diagram")
            add_callout("Interview use", diagram["interview"], "interview")
        add("Provider architecture diagrams for this role", "section")
        for diagram in _training_provider_diagram_cards(program):
            add(diagram["title"], "subsection")
            add_callout("Learning path source model", diagram["source"], "key")
            if diagram.get("sourceUrl"):
                add_callout("Official provider documentation", diagram["sourceUrl"], "diagram")
            add_provider_architecture_diagram(diagram)
            add_table(
                ["Architecture area", "What to notice"],
                [[layer["label"], layer["text"]] for layer in diagram["layers"]],
            )
            add_callout("Evidence to collect", diagram["evidence"], "diagram")
            add_callout("Interview use", diagram["interview"], "interview")
        for item in _as_list(architecture.get("datadogInlineDiagrams")):
            if isinstance(item, dict):
                add(item.get("title"), "subsection")
                add_callout("Official Datadog source", item.get("sourceUrl"), "diagram")
                add_datadog_reference_diagram(item)
                add_callout("Where it fits", item.get("whereItFits"), "key")
                add_callout("Beginner explanation", item.get("beginnerExplanation"), "learn")
                add_callout("What to say", item.get("whatToSay"), "interview")
                add_callout("Evidence to collect", item.get("evidenceToCollect"), "diagram")
    add("Provider-document use case source pack", "section")
    add(
        "This section connects the program to the provided certification QA documents and provider learning-path patterns. Staff can use it to decide which use cases deserve diagrams, labs, screenshots, and interview practice.",
        "body",
    )
    for item in _training_provider_usecase_sources(program):
        add(item["source"], "subsection")
        add_callout("Pattern pulled forward", item["pattern"], "key")
        add_callout("Use cases to prioritize", item["use_cases"], "learn")
        add_callout("Evidence visible in the scenario", item["evidence"], "diagram" if include_diagrams else "key")
    for card in _training_beginner_cards(program):
        add(f"{card['step']}. {card['title']}", "section")
        add_callout("What to understand", card["bullets"], "learn")
        add_callout("Functional example", card["try_this"], "practice")
    add("The story to keep in mind", "section")
    add_table(
        ["Step", "What it means"],
        [[item["label"], item["text"]] for item in _training_beginner_story_steps(program)],
    )
    add("Quick check", "section")
    for item in _training_beginner_quiz(program):
        add_callout(item["question"], item["answer"], "practice")
    add_chapter_close(
        [
            "The system story matters before tool memorization.",
            "A rough diagram is useful because it shows flow, ownership, and failure points.",
            "Every resume claim eventually connects to evidence.",
        ],
        [
            "Reading glossary terms without drawing the workflow.",
            "Answers that do not mention ownership boundaries.",
            "Marketing before proof artifacts are understandable.",
        ],
        [
            "Can you explain the project without tool names?",
            "Can you draw the workflow from memory?",
            "Which artifact proves your strongest resume bullet?",
        ],
    )

    add_chapter(
        2,
        "Project Context",
        "This chapter establishes the company scale, team structure, application portfolio, and consultant positioning. It keeps the training story grounded in a real enterprise operating model instead of disconnected tool knowledge.",
        ["Enterprise scale", "Team model", "Business systems", "Consultant positioning", "Interview opening"],
        ["Application portfolio", "Operating model", "Ownership boundary", "Delivery evidence"],
        "Interviewers look for context first. My answer explains where the role operated, which teams were involved, and what outcomes the work supported.",
    )
    context_brief = architecture.get("consultantProjectContextBrief") if isinstance(architecture.get("consultantProjectContextBrief"), dict) else {}
    if context_brief:
        add(context_brief.get("headline"), "section")
        add_callout("Context Summary", context_brief.get("summary"), "key")
        add_callout("Enterprise Scale", context_brief.get("scale"), "learn")
        add_callout("Business Product Flow", context_brief.get("businessFlow"), "diagram")
        add_callout("Role Boundary", context_brief.get("roleBoundary"), "interview")
        add_callout("Delivery Model", context_brief.get("deliveryModel"), "learn")
        add_callout("Architecture View", context_brief.get("architectureView"), "diagram")
        add_callout("Evidence Model", context_brief.get("evidenceModel"), "key")
        add_callout("Interview Frame", context_brief.get("interviewFrame"), "interview")
        add("Full Project Narrative", "section")
    add(architecture.get("consultantProjectContext") or program.enterprise_context)
    operating_model = architecture.get("enterpriseOperatingModel") or {}
    if operating_model:
        add("Enterprise Operating Model", "section")
        add(operating_model.get("consultantPlacement"))
        add_table(["Enterprise Scale"], [[item] for item in _as_list(operating_model.get("scale"))])
        add_callout("Technology teams", operating_model.get("technologyTeams"), "key")
    add("Product Point Of View", "section")
    add(architecture.get("roleProductExplanation"))
    add_chapter_close(
        [
            "Start every project answer with business context, scale, systems, and team model.",
            f"Position {role_name} work as delivery, reliability, platform, data, or AI enablement based on the role.",
            "Mention evidence such as dashboards, runbooks, PRs, deployment records, incident notes, and validation outputs.",
        ],
        [
            "Starting with only tool names.",
            "Claiming ownership of product requirements or application feature logic.",
            "Skipping the enterprise scale and support model.",
        ],
        [
            "What kind of enterprise did the work support?",
            "Which teams were involved?",
            "What evidence proves the work was delivered?",
        ],
    )

    add_chapter(
        3,
        "Application Landscape",
        "This chapter maps the major applications in the domain. The goal is to understand business purpose, operational dependencies, and where the role interacts with each system.",
        ["Major applications", "Line-of-business grouping", "Operational criticality", "Support visibility"],
        ["Product system", "Channel", "System of record", "Integration", "Operational signal"],
        "Interviewers often test whether the consultant understands the application, not only the tools around it.",
    )
    add_list(program.application_landscape)
    product_rows = []
    for index, item in enumerate(_as_list(program.application_landscape), start=1):
        product_rows.append([str(index), item, "Business workflow, integration, support, monitoring"])
    add_table(["S No", "Application", "Why It Matters"], product_rows[:25])
    add_chapter_close(
        [
            "Each product system has users, data, integrations, risks, and operational signals.",
            "The role touches product systems through delivery pipelines, platform services, monitoring, data flows, AI workflows, or support processes.",
            "Use product names in answers so the story sounds specific.",
        ],
        ["Treating all applications as identical.", "Explaining only infrastructure without product impact."],
        ["Which application was most business-critical?", "Which systems were integrated?", "What signals showed health or failure?"],
    )

    add_chapter(
        4,
        "Enterprise Architecture",
        "This chapter explains the architecture as a set of flows: user request flow, release flow, incident flow, data or automation flow, and ownership boundary. It replaces raw paragraphs with diagram-style reading paths.",
        ["High-level architecture", "Request flow", "Release flow", "Incident flow", "Ownership boundary"],
        ["DNS", "WAF", "API Gateway", "Kubernetes", "Queue", "Database", "Observability", "Runbook"],
        "Architecture answers become stronger when the system can be traced from user action to backend processing, monitoring, and support handoff.",
    )
    add(architecture.get("architectureSummary") or (architecture.get("architectureSummary") if architecture else ""))
    add_callout(
        "Architect reading lens",
        [
            "Start with the business capability and system boundary before naming cloud services.",
            "Explain the chosen pattern by rationale, tradeoff, constraint, NFR, control, and operational evidence.",
            "Separate product ownership, application ownership, role ownership, security control, data ownership, and operations ownership.",
            "For every flow, identify the failure mode, detection signal, recovery path, and evidence artifact.",
        ],
        "key",
    )
    if include_diagrams:
        add("Role architecture diagrams", "section")
        for diagram in _training_architecture_diagram_cards(program):
            add(diagram["title"], "subsection")
            add_callout("Purpose", diagram["purpose"], "key")
            add_visual_flow(diagram["title"], [node["label"] for node in diagram["nodes"]])
            add_table(
                ["Layer", "What to explain"],
                [[layer["label"], layer["text"]] for layer in diagram["layers"]],
            )
            add_callout("Evidence to collect", diagram["evidence"], "diagram")
            add_callout("Interview use", diagram["interview"], "interview")
        add("Provider architecture diagrams", "section")
        for diagram in _training_provider_diagram_cards(program):
            add(diagram["title"], "subsection")
            add_callout("Learning path source model", diagram["source"], "key")
            if diagram.get("sourceUrl"):
                add_callout("Official provider documentation", diagram["sourceUrl"], "diagram")
            add_provider_architecture_diagram(diagram)
            add_table(
                ["Architecture area", "What to explain"],
                [[layer["label"], layer["text"]] for layer in diagram["layers"]],
            )
            add_callout("Evidence to collect", diagram["evidence"], "diagram")
            add_callout("Interview use", diagram["interview"], "interview")
        for item in _as_list(architecture.get("datadogInlineDiagrams")):
            if isinstance(item, dict):
                add(item.get("title"), "subsection")
                add_callout("Official Datadog source", item.get("sourceUrl"), "diagram")
                add_datadog_reference_diagram(item)
                add_callout("Where it fits", item.get("whereItFits"), "key")
                add_callout("Beginner explanation", item.get("beginnerExplanation"), "learn")
                add_callout("What to say", item.get("whatToSay"), "interview")
                add_callout("Evidence to collect", item.get("evidenceToCollect"), "diagram")
    mindmap = architecture.get("architectureMindmap") or {}
    if mindmap:
        add("Architecture Mindmap", "section")
        add(mindmap.get("root"))
        for branch in _as_list(mindmap.get("branches")):
            if isinstance(branch, dict):
                add_callout(branch.get("title", "Architecture branch"), branch.get("items"), "diagram")
    if include_diagrams:
        add_visual_flow(
            "High-Level Architecture Flow",
            [
                "Users / channels",
                "DNS / CDN / WAF",
                "API gateway / load balancer",
                "Kubernetes or managed compute",
                "Databases / queues / storage",
                "Observability / support handoff",
            ],
        )
    for layer in _as_list(architecture.get("architectureLayers")):
        if isinstance(layer, dict):
            add(layer.get("layer"), "section")
            add_callout("Purpose", layer.get("purpose", ""), "key")
            add_callout(f"{role_name} view", layer.get("roleView", ""), "interview")
            add_callout("Components", layer.get("components"), "diagram")
            add_callout(
                "Architect challenge",
                [
                    "What is the business capability protected by this layer?",
                    "Which NFR is most exposed here: availability, security, performance, cost, auditability, data quality, or operability?",
                    "What signal proves this layer is healthy or degraded?",
                    "Where does ownership hand off to another team?",
                ],
                "warning",
            )
    for flow in _as_list(architecture.get("architectureFlows")):
        if isinstance(flow, dict):
            add(flow.get("name"), "section")
            if include_diagrams:
                add_visual_flow(
                    flow.get("name") or "Architecture Flow",
                    [
                        "Request enters",
                        "Security and routing",
                        "Runtime processing",
                        "Data or dependency call",
                        "Telemetry captured",
                        "Support evidence",
                    ],
                )
                add_callout("Flow diagram", flow.get("diagram"), "diagram")
            add(flow.get("explanation"))
            add_callout("Interview explanation", flow.get("whatToSay"), "interview")
    for item in _as_list(architecture.get("workflowDiagrams"))[:4]:
        if isinstance(item, dict):
            add(item.get("name"), "section")
            add_callout("Purpose", item.get("purpose"), "key")
            if include_diagrams:
                add_visual_flow(item.get("name") or "Workflow", item.get("steps"))
                add_callout("Workflow", item.get("steps"), "diagram")
            add_callout("Enterprise interview response angle", item.get("interviewCue"), "interview")
    add_chapter_close(
        [
            "Explain architecture using flow, not tool listing.",
            "Separate product workflow, platform workflow, release workflow, and incident workflow.",
            "Name the handoff points where ownership changes.",
        ],
        ["Listing tools without explaining request movement.", "Skipping failure points and monitoring signals."],
        ["How does a request flow through the system?", "Where can failures occur?", "What did the role own in the architecture?"],
    )

    add_chapter(
        5,
        "Role Ownership and Responsibility Boundaries",
        f"This chapter defines what {role_name} owned, contributed to, reviewed, and did not own. It is designed to prevent interview answers from sounding inflated or unclear.",
        ["Ownership matrix", "Responsibility boundary", "Evidence types", "Team handoffs"],
        ["RACI", "Runbook", "Validation", "Escalation", "Production support"],
        "I sound credible when I explain boundaries clearly instead of claiming ownership of every system.",
    )
    add_list(program.project_responsibilities)
    for item in _as_list(architecture.get("roleArchitectureOwnership")):
        if isinstance(item, dict):
            add(item.get("area"), "section")
            add_callout("Ownership", item.get("ownership", ""), "key")
            add_callout("Boundary", item.get("boundary", ""), "warning")
    add_table(
        ["Evidence", "Enterprise Interview Response"],
        [
            ["Jira stories", "Sprint-level work tied to use cases, defects, automation, support, or release tasks."],
            ["Pull requests", "Code, config, workflow, chart, module, data pipeline, or platform changes reviewed by the team."],
            ["Dashboards", "Operational visibility for health, latency, failures, trends, and support decisions."],
            ["Runbooks", "Repeatable steps for triage, validation, rollback, recovery, and handoff."],
            ["Incident notes", "Timeline, symptoms, diagnosis, action taken, owner, outcome, and prevention."],
        ],
    )
    add_chapter_close(
        ["Ownership must sound specific, limited, and evidence-backed.", "Boundaries make the story credible."],
        ["Saying the role owned business priority.", "Saying the role fixed every application issue."],
        ["What did the role own?", "What stayed with developers or product owners?", "What evidence was produced?"],
    )

    add_chapter(
        6,
        "Tools and Technologies",
        "This chapter connects tools to outputs. The goal is not to memorize names, but to explain why each tool existed, what output it produced, and how that output supported delivery or operations.",
        ["Tool purpose", "Tool output", "Operational usage", "Interview phrasing"],
        ["CI/CD", "IaC", "Observability", "Secrets", "Container runtime", "Automation"],
        "Tool answers impress interviewers when they connect the tool to an output such as deployment evidence, metrics, logs, policies, dashboards, or incident tickets.",
    )
    tool_rows = [[str(index), tool, "Purpose, output, owner, and evidence"] for index, tool in enumerate(_as_list(program.tools_and_technologies), start=1)]
    add_table(["S No", "Tool", "Enterprise Interview Response"], tool_rows)
    add_callout("Key deliverables", program.key_deliverables, "key")
    add_chapter_close(
        ["Explain tools by output: workflow run, deployment, metric, alert, trace, report, or evidence."],
        ["Listing tools without saying what each produced."],
        ["Which tools produced deployment evidence?", "Which tools supported troubleshooting?", "Which tools supported governance?"],
    )

    add_chapter(
        7,
        "Product System Deep Dives",
        "This chapter turns each major product system into a role-specific explanation. Each page explains users, capabilities, integrations, risks, signals, failure points, support ownership, and what to remember.",
        ["Business purpose", "Connected systems", "Data objects", "Failure points", "Monitoring signals"],
        ["Channel", "Ledger", "Queue", "Reconciliation", "Fraud signal", "SLA"],
        "Most interviews move from role questions into system questions. These deep dives help the consultant sound like someone who supported real products.",
    )
    for card in product_system_cards(program.application_landscape):
        system = product_system_detail(card["slug"], role_name, domain_name)
        if not system:
            continue
        add(system["name"], "section", page_break=True)
        add(system.get("summary"))
        add_table(
            ["Area", "Details"],
            [
                ["Business purpose", system.get("business_purpose") or system.get("summary")],
                ["Users", ", ".join(_as_list(system.get("users"))[:8])],
                ["Core capabilities", ", ".join(_as_list(system.get("capabilities"))[:8])],
                ["Connected systems", ", ".join(_as_list(system.get("systems"))[:8])],
                ["Important data", ", ".join(_as_list(system.get("data"))[:8])],
                ["Risks", ", ".join(_as_list(system.get("risks"))[:8])],
                ["Operational signals", ", ".join(_as_list(system.get("operational_signals"))[:8])],
            ],
        )
        add_callout("Architecture and support flow", system.get("architecture_flow") or system.get("support_ownership") or system.get("summary"), "diagram")
        add_callout("Failure points to watch", system.get("failure_points") or system.get("risks"), "warning")
        add_callout("Interview explanation", system.get("interview_answer"), "interview")
        for section in _as_list(system.get("sections")):
            if isinstance(section, dict):
                add(section.get("title"), "section")
                for paragraph in _as_list(section.get("paragraphs")):
                    add(paragraph)
        add_callout("What to remember", system.get("what_to_remember") or system.get("capabilities"), "key")

    add_chapter(
        8,
        "Delivered Use Cases",
        "Use cases are formatted as case studies. Each one explains the business problem, enterprise context, role ownership, implementation approach, workflow, evidence, production outcome, interview answer, and revision points.",
        ["Business problem", "Implementation approach", "Workflow", "Evidence", "Outcome"],
        ["Jira story", "Acceptance criteria", "Validation", "Rollback", "Runbook", "Dashboard"],
        "Use cases are the strongest interview material because they connect business need, technical action, and production outcome.",
    )
    add("Provider-document source guidance", "section")
    for item in _training_provider_usecase_sources(program):
        add(item["source"], "subsection")
        add_callout("Pattern pulled forward", item["pattern"], "key")
        add_callout("Priority use cases", item["use_cases"], "learn")
        add_callout("Evidence to collect", item["evidence"], "diagram" if include_diagrams else "key")
    for index, item in enumerate(_as_list(architecture.get("deliveredUseCases"))[:12], start=1):
        if not isinstance(item, dict):
            continue
        add(f"Use Case {index}: {item.get('title', '')}", "section", page_break=True)
        for section in _as_list(item.get("textbookSections")):
            if isinstance(section, dict):
                add(section.get("title"), "section")
                add_list(section.get("bullets"))
        add_callout("A. Business Problem", item.get("businessProblem"), "warning")
        ba_lens = item.get("businessAnalystLens") if isinstance(item.get("businessAnalystLens"), dict) else {}
        if ba_lens:
            add_callout("B. BA And Domain View", ba_lens.get("businessCapability"), "key")
            add_callout("B1. Business Actors", ba_lens.get("businessActors"), "learn")
            add_callout("B2. Business Workflow", ba_lens.get("businessWorkflow"), "diagram")
            add_callout("B3. Rules And Exceptions", ba_lens.get("businessRules"), "warning")
            add_callout("B4. KPIs And Reports", ba_lens.get("kpisAndReports"), "interview")
            add_callout("B5. Business/System Touchpoints", ba_lens.get("systemTouchpoints"), "key")
            add_callout("B6. BA Acceptance Criteria", ba_lens.get("baAcceptanceCriteria"), "learn")
            add_callout("B7. BA Review Questions", ba_lens.get("businessQuestions"), "interview")
        pm_lens = item.get("projectManagerLens") if isinstance(item.get("projectManagerLens"), dict) else {}
        if pm_lens:
            add_callout("C. Project Manager Delivery View", pm_lens.get("projectObjective"), "key")
            add_callout("C1. Scope", pm_lens.get("scope"), "learn")
            add_callout("C2. Stakeholders", pm_lens.get("stakeholders"), "key")
            add_callout("C3. Dependencies", pm_lens.get("dependencies"), "warning")
            add_callout("C4. Milestones", pm_lens.get("milestones"), "diagram")
            risk_rows = [
                [risk.get("risk", ""), risk.get("impact", ""), risk.get("mitigation", "")]
                for risk in _as_list(pm_lens.get("deliveryRisks"))
                if isinstance(risk, dict)
            ]
            if risk_rows:
                add_table(["Delivery Risk", "Impact", "Mitigation"], risk_rows)
            add_callout("C5. Status Reporting", pm_lens.get("statusReporting"), "interview")
            add_callout("C6. PM Acceptance Criteria", pm_lens.get("pmAcceptanceCriteria"), "learn")
            add_callout("C7. Delivery Evidence", pm_lens.get("deliveryEvidence"), "key")
        qa_lens = item.get("qaTestLens") if isinstance(item.get("qaTestLens"), dict) else {}
        if qa_lens:
            add_callout("D. QA / Test Lead View", qa_lens.get("testStrategy"), "key")
            add_callout("D1. Test Coverage And Gates", _as_list(qa_lens.get("coverage"))[:2] + _as_list(qa_lens.get("releaseGates"))[:3], "learn")
        ops_lens = item.get("productionSupportLens") if isinstance(item.get("productionSupportLens"), dict) else {}
        if ops_lens:
            add_callout("E. Production Support / Operations View", ops_lens.get("supportModel"), "warning")
            add_callout("E1. Incident Flow And Handoff", _as_list(ops_lens.get("incidentFlow"))[:2] + _as_list(ops_lens.get("opsHandoff"))[:3], "interview")
        security_lens = item.get("securityComplianceLens") if isinstance(item.get("securityComplianceLens"), dict) else {}
        if security_lens:
            add_callout("F. Security / Compliance View", security_lens.get("securityControls"), "key")
            add_callout("F1. Compliance Evidence", security_lens.get("complianceEvidence"), "learn")
        data_lens = item.get("dataReportingLens") if isinstance(item.get("dataReportingLens"), dict) else {}
        if data_lens:
            add_callout("G. Data / Reporting View", data_lens.get("dataFlow"), "diagram")
            add_callout("G1. Data Quality And Reporting Signals", _as_list(data_lens.get("dataQualityChecks"))[:2] + _as_list(data_lens.get("reportingSignals"))[:3], "key")
        product_lens = item.get("productOwnerLens") if isinstance(item.get("productOwnerLens"), dict) else {}
        if product_lens:
            add_callout("H. Product Owner View", product_lens.get("productValue"), "interview")
            add_callout("H1. Product Fit And Acceptance", _as_list(product_lens.get("roadmapFit"))[:2] + _as_list(product_lens.get("productAcceptanceCriteria"))[:2], "learn")
        add_callout("I. Environment Context", item.get("environmentContext") or architecture.get("consultantProjectContext") or program.enterprise_context, "key")
        add_callout("J. Role Ownership", item.get("roleBoundary"), "interview")
        lens = item.get("architectLens") if isinstance(item.get("architectLens"), dict) else {}
        if lens:
            add_callout("K. Architect And SME View", lens.get("seniorExplanation"), "key")
            add_callout("K1. Decision Rationale", lens.get("decisionRationale"), "learn")
            add_callout("K2. Tradeoffs", lens.get("architecturalTradeoffs"), "warning")
            add_callout("K3. Constraints", lens.get("constraints"), "key")
            add_callout("K4. NFRs And Controls", lens.get("nfrsAndControls"), "diagram")
            risk_rows = [
                [risk.get("risk", ""), risk.get("impact", ""), risk.get("mitigation", "")]
                for risk in _as_list(lens.get("riskRegister"))
                if isinstance(risk, dict)
            ]
            if risk_rows:
                add_table(["Risk", "Impact", "Mitigation"], risk_rows)
            add_callout("K5. Architect Review Questions", lens.get("reviewQuestions"), "interview")
        add_callout("L. Implementation Approach", item.get("deliveredScope"), "learn")
        if include_diagrams:
            add_visual_flow(
                f"Use Case {index} Delivery Flow",
                [
                    "Business problem",
                    "Jira stories",
                    f"{role_name} implementation",
                    "Review and validation",
                    "Production support evidence",
                    "Interview story",
                ],
            )
            add_callout("M. Architecture / Workflow", item.get("workflow") or item.get("architectureFlow") or item.get("systemsTouched"), "diagram")
        add_callout("N. Evidence Produced", item.get("evidenceToExplain"), "key")
        add_callout("O. Production Outcome", item.get("productionOutcome") or item.get("outcome") or "The process became easier to validate, support, explain, and hand off during release or incident activity.", "interview")
        add_callout("P. Interview Answer", item.get("interviewAnswer") or item.get("interviewStory") or item.get("businessProblem"), "interview")
        add_callout("Q. What To Remember", item.get("whatToRemember") or item.get("deliveredScope"), "practice")

    add_chapter(
        9,
        "Jira Stories and Sprint Evidence",
        "This chapter extracts sprint-style evidence from delivered use cases. The focus is on implementation chunks, acceptance criteria, validation, and proof that the work was delivered through normal engineering process.",
        ["Jira naming", "Story purpose", "Implementation evidence", "Validation output", "Sprint rhythm"],
        ["JIRA", "Acceptance criteria", "Definition of done", "Sprint demo", "Release note"],
        "Interviewers trust stories more when they include how the work moved through Jira, review, testing, release, and support.",
    )
    story_rows: list[list[Any]] = []
    for use_case_index, item in enumerate(_as_list(architecture.get("deliveredUseCases")), start=1):
        if not isinstance(item, dict):
            continue
        for story_index, story in enumerate(_as_list(item.get("jiraStories")), start=1):
            if isinstance(story, dict):
                key = story.get("key") or f"JIRA-{use_case_index}-{story_index}"
                story_rows.append([key, item.get("title", ""), story.get("title", ""), story.get("summary", "")])
    add_table(["Jira", "Use Case", "Story", "Evidence"], story_rows[:120])
    add_chapter_close(
        ["Every 5-6 Jira stories connect to a meaningful use case.", "Evidence shows implementation, validation, and support readiness."],
        ["Treating Jira stories as task names only.", "Skipping acceptance criteria or validation evidence."],
        ["Which Jira stories supported the use case?", "How was the story validated?", "What changed after the sprint?"],
    )

    add_chapter(
        10,
        "Production Support and Troubleshooting",
        "This chapter explains incident response, triage, escalation, evidence collection, and support boundaries. It connects production symptoms to the likely layer where the issue lives.",
        ["Incident flow", "Failure modes", "Triage", "Escalation", "RCA", "Runbook"],
        ["Alert", "Metric", "Log", "Trace", "Rollback", "MTTR", "RCA"],
        "Scenario and troubleshooting rounds test whether the issue can be diagnosed calmly and routed to the right owner.",
    )
    add_callout("Production support scenarios", program.production_support_scenarios, "warning")
    for item in _as_list(architecture.get("workflowDiagrams")):
        if isinstance(item, dict):
            add(item.get("name"), "section")
            add_callout("Purpose", item.get("purpose"), "key")
            if include_diagrams:
                add_callout("Steps", item.get("steps"), "diagram")
            add_callout("Enterprise interview response angle", item.get("interviewCue"), "interview")
    add_chapter_close(
        ["Triage starts by finding the failing layer.", "My answer explains symptoms, evidence, action, owner, and prevention."],
        ["Jumping to a fix without diagnosis.", "Blaming downstream teams without evidence."],
        ["What metrics changed?", "Which logs confirmed the issue?", "Who owned the fix?", "How was recovery validated?"],
    )

    add_chapter(
        11,
        "Interview Preparation",
        "This chapter converts the role/domain material into crisp interview answers. Questions are grouped by general, architecture, system design, scenario, troubleshooting, and behavioral categories.",
        ["Question categories", "Enterprise interview responses", "Evidence terms", "Mistakes to avoid"],
        ["STAR", "System design", "Troubleshooting", "Ownership", "Evidence"],
        "The goal is to answer directly, then support the answer with product context, tools, implementation steps, validation, and outcome.",
    )
    add_callout("Primary interview story", program.interview_story, "interview")
    market_pack = architecture.get("marketJobDescriptionPack") or {}
    if market_pack:
        add("MAAS Active JD Alignment", "section")
        add_callout("Source", [market_pack.get("source", ""), f"Status: {market_pack.get('status', '')}", f"Loaded: {market_pack.get('loadedCount', 0)} / {market_pack.get('requiredCount', 7)}"], "key")
        if market_pack.get("note"):
            add_callout("Important", market_pack.get("note"), "warning")
        for item in _as_list(market_pack.get("items")):
            if isinstance(item, dict):
                label = " - ".join(part for part in [item.get("title", ""), item.get("company", ""), item.get("location", "")] if part)
                add(label, "section")
                add_callout("Domain match", [item.get("domainMatchStatus", ""), f"Score: {item.get('domainValidationScore', 0)}%", f"Evidence: {', '.join(item.get('domainValidationEvidence', []))}"], "key")
                add_callout("JD URL fetch", [f"Status: {item.get('jdFetchStatus', '')}", "Full JD text was used for scoring." if item.get("jdFetchedFromUrl") else "Full JD text was not available yet; run the MAAS JD enrichment script."], "key")
                add_callout("Training coverage", [f"Score: {item.get('trainingCoverageScore', 0)}%", "Action: update training program" if item.get("programUpdateRequired") else "Action: ignore because coverage is already 80% or higher"], "practice")
                if item.get("programUpdateRequired"):
                    add_callout("Program gaps to add", item.get("programGapActions"), "warning")
                add_callout("Real JD summary", item.get("summary", ""), "key")
                add_callout("Use cases to practice", item.get("useCasesToPractice"), "practice")
                add_callout("Diagram focus", item.get("diagramFocus"), "interview")
    benchmark = architecture.get("maasInterviewBenchmark") or {}
    if benchmark:
        add("MAAS Benchmark", "section")
        for label, key in [
            ("Round flow", "roundFlow"),
            ("Market signals", "marketSignals"),
            ("Evaluation focus", "evaluationFocus"),
            ("Rejection signals", "rejectionSignals"),
            ("Core questions", "coreQuestions"),
            ("Follow-up probes", "followUpProbes"),
            ("Pressure checks", "pressureChecks"),
        ]:
            add_callout(label, benchmark.get(key), "key")
        for item in _as_list(benchmark.get("questionBank")):
            if isinstance(item, dict):
                add(item.get("question"), "section")
                add_callout("Interview response", item.get("answerResponse") or item.get("answerBullets"), "interview")
                add_callout("Why this answer works", item.get("whyThisWorks") or item.get("evidenceToMention"), "key")
                add_callout("Key terms to mention", item.get("evidenceToMention"), "practice")
                add_callout("Mistakes to avoid", item.get("mistakesToAvoid") or ["Avoid vague tool lists.", "Avoid claiming ownership outside the role boundary.", "Avoid skipping validation evidence."], "warning")
    add_chapter_close(
        ["Lead with the answer, then add context and evidence.", "Use product system names and delivered use cases."],
        ["Saying 'we used tools' without explaining output.", "Giving long answers without a clear result."],
        ["What was the business problem?", "What did the role own?", "How was the outcome validated?"],
    )

    add_chapter(
        12,
        "Resume Summary and Marketing Positioning",
        "This chapter turns the training program into resume-ready positioning. It connects project scope, role ownership, tools, delivered use cases, and measurable outcomes.",
        ["Resume summary", "Project bullets", "ATS terms", "Marketing story"],
        ["ATS", "Project summary", "Impact statement", "Role alignment"],
        "Marketing works better when the resume and interview story describe the same project, same systems, and same evidence.",
    )
    add(program.resume_project_summary)
    add_callout("Resume bullets", _training_resume_bullets(program), "interview")
    sprint = architecture.get("sprintDeliveryModel") or {}
    add_callout("Sprint delivery model", sprint.get("summary"), "key")
    for track in _as_list(sprint.get("projectTracks")):
        if isinstance(track, dict):
            add(track.get("track"), "section")
            if include_diagrams:
                add_callout("Systems", track.get("systems"), "diagram")
            add_callout("Sprint output", track.get("output", ""), "key")
    for area, items in (program.three_year_delivery_timeline or {}).items():
        add(str(area), "section")
        add_list(items)
    add_chapter_close(
        ["Resume, interview story, and use cases should reinforce the same project narrative."],
        ["Writing generic tool bullets.", "Adding projects that cannot be explained in interviews."],
        ["Which bullet maps to which use case?", "Which tools and outputs prove the bullet?"],
    )

    add_chapter(
        13,
        "Final Evidence Package",
        "This chapter defines the evidence package behind the project story. It turns role/domain work into artifacts that support architecture, workflow, troubleshooting, resume, and interview claims.",
        ["Evidence checklist", "Artifact acceptance criteria", "Interview usage", "Resume usage", "Submission support"],
        ["Architecture diagram", "Workflow diagram", "Boundary sheet", "Runbook", "Story bank", "Resume bullets"],
        "Credibility improves when every resume bullet and interview story is backed by a diagram, workflow, runbook, screenshot, output, or documented decision.",
    )
    add_callout(
        "Package rule",
        "Vague tool knowledge is not enough. The final evidence package shows what was built, operated, validated, troubleshot, documented, and explained.",
        "warning",
    )
    add_table(
        ["S No", "Artifact", "What It Must Prove", "Used For"],
        _training_final_evidence_package_rows(program),
    )
    for item in _training_final_evidence_package_details(program):
        add(item["artifact"], "section")
        add_callout("What to include", item["include"], "key")
        add_callout("Acceptance criteria", item["acceptance"], "learn")
        add_callout("Interview use", item["interview"], "interview")
        add_callout("Resume or submission use", item["resume"], "practice")
    if include_diagrams:
        add_visual_flow(
            "Evidence Package To Interview Flow",
            [
                "Artifact created",
                "Staff reviews boundary",
                "Resume bullet updated",
                "Mock question tested",
                "Submission note tailored",
                "Interview story delivered",
            ],
        )
    add_chapter_close(
        [
            "Every claim connects to at least one artifact.",
            "The package removes overclaiming by defining ownership boundaries.",
            "Screenshots, command outputs, runbooks, and story banks make interview answers concrete.",
        ],
        [
            "Uploading diagrams with no explanation.",
            "Including screenshots with secrets or client-sensitive data.",
            "Writing resume bullets that cannot be supported by evidence.",
        ],
        [
            "Which artifact proves the architecture story?",
            "Which artifact proves troubleshooting ability?",
            "Which artifact should staff use for company-specific submission notes?",
        ],
    )

    add_chapter(
        14,
        "Glossary and Quick Revision Notes",
        "This chapter provides quick revision terms from the product domain and the role. Each term should be understood from product meaning, interview use, and ownership boundary.",
        ["Domain vocabulary", "Role vocabulary", "Product meaning", "Interview phrasing"],
        ["Glossary", "Boundary", "Evidence", "Product workflow"],
        "Glossary fluency helps the consultant sound natural when discussing unfamiliar product systems, support scenarios, and architecture.",
    )
    for item in _as_list(architecture.get("productGlossary")):
        if isinstance(item, dict):
            source_label = "ROLE TERM" if item.get("sourceType") == "role" else "DOMAIN TERM"
            term_style = "glossary_term" if item.get("sourceType") == "role" else "section"
            add(f"{source_label}: {item.get('term', '')} ({item.get('category', 'Core Term')})", term_style)
            add_callout("Product meaning", item.get("productMeaning", ""), "key")
            add_callout("Interview points", item.get("consultantTalkTrackBullets") or [item.get("consultantTalkTrack")], "interview")
            add_callout("Boundary", item.get("boundaryBullets") or [item.get("boundary")], "warning")
    add_chapter_close(
        ["Know terms by product meaning, not dictionary definition.", "Use boundary language to stay credible."],
        ["Memorizing terms without connecting them to systems.", "Using the same explanation for every term."],
        ["What does this term mean in the product?", "What evidence proves experience with it?", "What does the role not own?"],
    )

    add_role_domain_rehearsal_appendix()

    return blocks


def _simple_text_pdf(title: str, blocks: list[dict[str, str]], *, program: Optional[TrainingProgram] = None) -> bytes:
    page_width = 612
    page_height = 792
    left = 58
    top = 716
    bottom = 58
    max_width = page_width - (left * 2)
    styles = {
        "cover": {"size": 25, "leading": 35},
        "title": {"size": 19, "leading": 25},
        "chapter_number": {"size": 12, "leading": 18},
        "chapter_title": {"size": 22, "leading": 29},
        "chapter_intro": {"size": 11, "leading": 16},
        "section": {"size": 14, "leading": 19},
        "glossary_term": {"size": 12, "leading": 18},
        "subsection": {"size": 12, "leading": 16},
        "toc": {"size": 11, "leading": 17},
        "cover_meta": {"size": 11, "leading": 16},
        "cover_summary": {"size": 12, "leading": 18},
        "callout_title": {"size": 11, "leading": 19},
        "callout": {"size": 10, "leading": 16},
        "table_header": {"size": 9, "leading": 16},
        "table_row": {"size": 9, "leading": 16},
        "code": {"size": 9, "leading": 14},
        "visual_flow": {"size": 9, "leading": 92},
        "meta": {"size": 9, "leading": 13},
        "body": {"size": 10, "leading": 14},
    }
    pages: list[list[tuple[int, int, int, str, str, str]]] = []
    current: list[tuple[int, int, int, str, str, str]] = []
    y = top

    def new_page() -> None:
        nonlocal current, y
        if current:
            pages.append(current)
        current = []
        y = top

    def block_gap(style_name: str) -> int:
        return {
            "cover": 12,
            "title": 12,
            "chapter_number": 4,
            "chapter_title": 14,
            "chapter_intro": 10,
            "section": 12,
            "glossary_term": 10,
            "subsection": 8,
            "toc": 4,
            "cover_meta": 5,
            "cover_summary": 10,
            "callout_title": 2,
            "callout": 8,
            "table_header": 4,
            "table_row": 7,
            "code": 5,
            "body": 7,
        }.get(style_name, 6)

    for block in blocks:
        style_name = block.get("style", "body")
        if style_name == "page_break":
            new_page()
            continue
        if style_name == "space":
            y -= 10
            if y < bottom:
                new_page()
            continue
        if style_name == "visual_flow":
            if y < bottom + 118:
                new_page()
            current.append((left, y, 9, block.get("text", ""), style_name, block.get("box", "")))
            y -= 122
            continue
        if style_name == "provider_arch":
            if y < bottom + 318:
                new_page()
            current.append((left, y, 9, block.get("text", ""), style_name, block.get("box", "")))
            y -= 328
            continue
        style = styles.get(style_name, styles["body"])
        indent = 0 if style_name in {"cover", "title", "chapter_number", "chapter_title", "section", "glossary_term", "subsection", "meta"} else 16
        if style_name in {"callout", "callout_title", "table_header", "table_row", "code"}:
            indent = 18
        if style_name == "chapter_number" and current:
            new_page()
        wrapped = _pdf_wrap_text(block.get("text", ""), style["size"], max_width - indent)
        if not wrapped:
            continue
        if style_name in {"chapter_number", "chapter_title"}:
            y -= 8
        for line in wrapped:
            if y < bottom + style["leading"]:
                new_page()
            current.append((left + indent, y, style["size"], line, style_name, block.get("box", "")))
            y -= style["leading"]
        y -= block_gap(style_name)
        if y < bottom:
            new_page()
    if current:
        pages.append(current)
    if not pages:
        pages = [[(left, top, 12, _pdf_clean_text(title), "body", "")]]
    header = ""
    if program:
        header = f"{program.marketing_role.name} | {program.industry_domain} | Full Reference"
    return _build_pdf_bytes(pages, page_width, page_height, header=header)


def _build_pdf_bytes(pages: list[list[tuple[int, int, int, str, str, str]]], page_width: int, page_height: int, *, header: str = "") -> bytes:
    objects: list[bytes] = []
    page_refs: list[int] = []
    font_obj = 3
    bold_font_obj = 4
    next_obj = 5
    content_page_pairs: list[tuple[int, int, bytes]] = []
    for page_number, page in enumerate(pages, start=1):
        content_obj = next_obj
        page_obj = next_obj + 1
        next_obj += 2
        page_refs.append(page_obj)
        commands: list[str] = []
        if page_number == 1:
            commands.append("q 0.94 0.98 0.97 rg 0 0 612 792 re f Q")
            commands.append("q 0.00 0.45 0.40 rg 0 0 18 792 re f Q")
            commands.append("q 0.00 0.45 0.40 RG 58 646 m 554 646 l S Q")
        elif header:
            commands.append("q 0.97 0.98 0.99 rg 0 748 612 44 re f Q")
            commands.append(_pdf_text_command(58, 764, 8, header, "0.28 0.34 0.44"))
            commands.append("q 0.82 0.86 0.90 RG 58 746 m 554 746 l S Q")
        for x, y, size, text, style_name, box in page:
            if style_name == "chapter_number":
                commands.append("q 0.00 0.45 0.40 rg 58 666 92 24 re f Q")
            elif style_name == "chapter_title":
                commands.append("q 0.94 0.98 0.97 rg 44 610 524 98 re f Q")
                commands.append("q 0.00 0.45 0.40 rg 44 610 5 98 re f Q")
            elif style_name == "section":
                line_y = max(46, y - 4)
                commands.append(f"q 0.00 0.45 0.40 RG 58 {line_y} m 554 {line_y} l S Q")
            elif style_name == "glossary_term":
                commands.append("q 0.93 0.98 0.97 rg 58 {0} 496 22 re f Q".format(y - 6))
                commands.append("q 0.00 0.45 0.40 rg 58 {0} 5 22 re f Q".format(y - 6))
            elif style_name == "callout_title":
                commands.append(_pdf_box_command(x - 10, y - 5, 496, 23, box or "key", stroke=False))
            elif style_name == "callout":
                commands.append(_pdf_box_command(x - 10, y - 5, 496, 21, box or "key", stroke=False))
            elif style_name == "table_header":
                commands.append("q 0.91 0.95 0.96 rg 58 {0} 496 21 re f Q".format(y - 6))
            elif style_name == "table_row":
                commands.append(f"q 0.96 0.98 0.99 rg 58 {y - 6} 496 20 re f Q")
            elif style_name == "code":
                commands.append(f"q 0.07 0.10 0.16 rg 58 {y - 5} 496 18 re f Q")
            elif style_name == "visual_flow":
                commands.extend(_pdf_visual_flow_commands(x, y, text))
                continue
            elif style_name == "provider_arch":
                commands.extend(_pdf_provider_architecture_commands(x, y, text))
                continue
            color = {
                "cover": "0.02 0.20 0.20",
                "title": "0.07 0.10 0.16",
                "chapter_number": "1 1 1",
                "chapter_title": "0.02 0.20 0.20",
                "section": "0.07 0.10 0.16",
                "glossary_term": "0.02 0.20 0.20",
                "subsection": "0.10 0.14 0.20",
                "toc": "0.28 0.34 0.44",
                "cover_meta": "0.28 0.34 0.44",
                "cover_summary": "0.20 0.28 0.38",
                "callout_title": "0.02 0.20 0.20",
                "callout": "0.20 0.28 0.38",
                "table_header": "0.02 0.20 0.20",
                "table_row": "0.20 0.28 0.38",
                "code": "0.90 0.94 0.98",
            }.get(style_name, "0.20 0.28 0.38")
            font_name = "F2" if style_name in {"cover", "title", "chapter_number", "chapter_title", "section", "glossary_term", "subsection", "callout_title", "table_header"} else "F1"
            commands.append(_pdf_text_command(x, y, size, text, color, font_name=font_name))
        commands.append("q 0.82 0.86 0.90 RG 58 44 m 554 44 l S Q")
        commands.append(_pdf_text_command(58, 30, 8, f"Mintel Consultant Training | Page {page_number} of {len(pages)}", "0.36 0.42 0.52"))
        stream = "\n".join(commands).encode("latin-1", "replace")
        content_page_pairs.append((content_obj, page_obj, stream))

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{page_obj} 0 R" for page_obj in page_refs)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_refs)} >>".encode("latin-1"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    for content_obj, page_obj, stream in content_page_pairs:
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream")
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
                f"/Resources << /Font << /F1 {font_obj} 0 R /F2 {bold_font_obj} 0 R >> >> /Contents {content_obj} 0 R >>"
            ).encode("latin-1")
        )

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("latin-1"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_at = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n"
        ).encode("latin-1")
    )
    return bytes(output)


def _pdf_text_command(x: int, y: int, size: int, text: str, color: str = "0 0 0", *, font_name: str = "F1") -> str:
    return f"BT {color} rg /{font_name} {size} Tf 1 0 0 1 {x} {y} Tm ({_pdf_escape(text)}) Tj ET"


def _pdf_box_command(x: int, y: int, width: int, height: int, kind: str, *, stroke: bool = True) -> str:
    fill = {
        "warning": "1.00 0.96 0.90",
        "interview": "0.93 0.98 0.97",
        "learn": "0.95 0.97 1.00",
        "diagram": "0.96 0.98 0.99",
        "practice": "0.98 0.96 1.00",
        "key": "0.96 0.98 0.96",
    }.get(kind, "0.96 0.98 0.96")
    stroke_color = {
        "warning": "0.86 0.48 0.15",
        "interview": "0.00 0.45 0.40",
        "learn": "0.32 0.45 0.72",
        "diagram": "0.32 0.42 0.50",
        "practice": "0.45 0.34 0.70",
        "key": "0.00 0.45 0.40",
    }.get(kind, "0.00 0.45 0.40")
    if not stroke:
        return f"q {fill} rg {x} {y} {width} {height} re f Q"
    return f"q {fill} rg {x} {y} {width} {height} re f {stroke_color} RG {x} {y} {width} {height} re S Q"


def _pdf_visual_flow_commands(x: int, y: int, text: str) -> list[str]:
    parts = [_pdf_clean_text(part) for part in text.split("|") if _pdf_clean_text(part)]
    if not parts:
        return []
    title = parts[0]
    steps = parts[1:] or ["Input", "Processing", "Validation", "Evidence"]
    commands = [
        f"q 0.96 0.98 0.99 rg {x} {y - 96} 496 104 re f Q",
        f"q 0.00 0.45 0.40 RG {x} {y - 96} 496 104 re S Q",
        _pdf_text_command(x + 14, y - 20, 11, title, "0.02 0.20 0.20", font_name="F2"),
    ]
    box_width = 72
    gap = 10
    start_x = x + 14
    box_y = y - 78
    for index, step in enumerate(steps[:6]):
        box_x = start_x + (index * (box_width + gap))
        commands.append(f"q 0.93 0.98 0.97 rg {box_x} {box_y} {box_width} 38 re f Q")
        commands.append(f"q 0.00 0.45 0.40 RG {box_x} {box_y} {box_width} 38 re S Q")
        for line_index, line in enumerate(_pdf_wrap_text(step, 7, box_width - 10)[:3]):
            commands.append(_pdf_text_command(box_x + 5, box_y + 25 - (line_index * 9), 7, line, "0.16 0.24 0.34"))
        if index < min(len(steps), 6) - 1:
            arrow_x = box_x + box_width + 2
            arrow_y = box_y + 19
            commands.append(f"q 0.00 0.45 0.40 RG {arrow_x} {arrow_y} m {arrow_x + 6} {arrow_y} l S Q")
            commands.append(f"q 0.00 0.45 0.40 rg {arrow_x + 6} {arrow_y} m {arrow_x + 1} {arrow_y + 3} l {arrow_x + 1} {arrow_y - 3} l f Q")
    return commands


def _pdf_provider_architecture_commands(x: int, y: int, text: str) -> list[str]:
    try:
        diagram = json.loads(text or "{}")
    except json.JSONDecodeError:
        diagram = {}
    title = _pdf_clean_text(diagram.get("title") or "Provider Architecture Diagram")
    provider = _pdf_clean_text(diagram.get("provider") or "Cloud")
    nodes = [node for node in _as_list(diagram.get("nodes")) if isinstance(node, dict)]
    evidence = [_pdf_clean_text(item) for item in _as_list(diagram.get("evidence")) if _pdf_clean_text(item)]
    external = nodes[0] if nodes else {"label": "Entry", "items": ["User", "request"]}
    operations = nodes[-1] if len(nodes) > 1 else {"label": "Operations", "items": ["logs", "metrics", "evidence"]}
    services = nodes[1:-1] if len(nodes) > 2 else nodes[:4]
    canvas_x = x
    canvas_y = y - 300
    canvas_w = 496
    canvas_h = 306
    commands = [
        f"q 0.98 0.99 1.00 rg {canvas_x} {canvas_y} {canvas_w} {canvas_h} re f Q",
        f"q 0.32 0.42 0.50 RG {canvas_x} {canvas_y} {canvas_w} {canvas_h} re S Q",
        _pdf_text_command(canvas_x + 14, y - 22, 12, title[:82], "0.02 0.20 0.20", font_name="F2"),
        _pdf_text_command(canvas_x + 14, y - 38, 8, "Architecture-style provider diagram for role/domain reference material", "0.36 0.42 0.52"),
    ]

    def draw_wrapped_text(px: int, py: int, size: int, text_value: Any, max_width: int, color: str, *, font_name: str = "F1", max_lines: int = 2, line_gap: int = 9) -> None:
        for line_index, line in enumerate(_pdf_wrap_text(_pdf_clean_text(text_value), size, max_width)[:max_lines]):
            commands.append(_pdf_text_command(px, py - (line_index * line_gap), size, line, color, font_name=font_name))

    def draw_panel(px: int, py: int, pw: int, ph: int, label: str, items: list[Any], *, fill: str, stroke: str, badge: str) -> None:
        commands.append(f"q {fill} rg {px} {py} {pw} {ph} re f {stroke} RG {px} {py} {pw} {ph} re S Q")
        commands.append(f"q {stroke} rg {px + 10} {py + ph - 25} 20 18 re f Q")
        commands.append(_pdf_text_command(px + 16, py + ph - 20, 8, badge, "1 1 1", font_name="F2"))
        if pw < 92:
            label_x = px + 12
            label_y = py + ph - 34
            label_width = pw - 20
            item_start_y = py + ph - 58
        else:
            label_x = px + 36
            label_y = py + ph - 17
            label_width = pw - 42
            item_start_y = py + ph - 44
        draw_wrapped_text(label_x, label_y, 8, label, max(28, label_width), "0.07 0.10 0.16", font_name="F2", max_lines=2, line_gap=8)
        for index, item in enumerate(_as_list(items)[:3]):
            line_y = item_start_y - (index * 14)
            if line_y < py + 8:
                break
            draw_wrapped_text(px + 12, line_y, 6, f"- {_pdf_clean_text(item)}", max(28, pw - 18), "0.20 0.28 0.38", max_lines=1, line_gap=7)

    def arrow(x1: int, y1: int, x2: int, y2: int) -> None:
        commands.append(f"q 0.00 0.45 0.40 RG {x1} {y1} m {x2} {y2} l S Q")
        commands.append(f"q 0.00 0.45 0.40 rg {x2} {y2} m {x2 - 6} {y2 + 4} l {x2 - 6} {y2 - 4} l f Q")

    entry_x = canvas_x + 14
    entry_y = canvas_y + 156
    entry_w = 106
    entry_h = 86
    cloud_x = canvas_x + 154
    cloud_y = canvas_y + 72
    cloud_w = 218
    cloud_h = 178
    ops_x = canvas_x + 404
    ops_y = canvas_y + 156
    ops_w = 78
    ops_h = 86

    draw_panel(entry_x, entry_y, entry_w, entry_h, external.get("label", "Entry"), external.get("items", []), fill="1.00 0.97 0.91", stroke="0.74 0.45 0.12", badge="1")
    commands.append(f"q 0.95 1.00 0.98 rg {cloud_x} {cloud_y} {cloud_w} {cloud_h} re f 0.00 0.45 0.40 RG {cloud_x} {cloud_y} {cloud_w} {cloud_h} re S Q")
    commands.append(f"q 1 1 1 rg {cloud_x + 12} {cloud_y + cloud_h - 12} 78 18 re f 0.00 0.45 0.40 RG {cloud_x + 12} {cloud_y + cloud_h - 12} 78 18 re S Q")
    commands.append(_pdf_text_command(cloud_x + 20, cloud_y + cloud_h - 7, 8, provider.upper()[:14], "0.00 0.45 0.40", font_name="F2"))
    draw_panel(ops_x, ops_y, ops_w, ops_h, operations.get("label", "Operations"), operations.get("items", []), fill="0.95 0.97 1.00", stroke="0.32 0.45 0.72", badge=str(max(len(nodes), 3)))
    arrow(entry_x + entry_w + 8, entry_y + 43, cloud_x - 8, entry_y + 43)
    arrow(cloud_x + cloud_w + 8, entry_y + 43, ops_x - 8, entry_y + 43)

    service_positions = [
        (cloud_x + 14, cloud_y + 112),
        (cloud_x + 114, cloud_y + 112),
        (cloud_x + 14, cloud_y + 34),
        (cloud_x + 114, cloud_y + 34),
    ]
    for index, node in enumerate(services[:4]):
        sx, sy = service_positions[index]
        kind = node.get("kind") or ""
        fill = "0.93 0.98 0.97" if kind == "role" else "1 1 1"
        stroke = "0.00 0.45 0.40" if kind == "role" else "0.68 0.78 0.82"
        draw_panel(sx, sy, 88, 58, node.get("label", "Service"), node.get("items", []), fill=fill, stroke=stroke, badge=str(index + 2))
        if index in {0, 1}:
            arrow(sx + 44, sy - 4, sx + 44, sy - 22)

    caption_y = canvas_y + 44
    captions = [
        ("Entry", "business trigger, user, data, or event"),
        ("Cloud boundary", "provider services and role ownership"),
        ("Proof", "logs, dashboards, policies, runbooks"),
    ]
    for index, (heading, body) in enumerate(captions):
        cx = canvas_x + 14 + (index * 160)
        commands.append(f"q 0.96 0.98 0.99 rg {cx} {caption_y - 4} 148 30 re f 0.82 0.86 0.90 RG {cx} {caption_y - 4} 148 30 re S Q")
        draw_wrapped_text(cx + 7, caption_y + 13, 7, heading, 134, "0.02 0.20 0.20", font_name="F2", max_lines=1)
        draw_wrapped_text(cx + 7, caption_y + 3, 6, body, 134, "0.36 0.42 0.52", max_lines=1)

    if evidence:
        evidence_y = canvas_y + 14
        commands.append(_pdf_text_command(canvas_x + 14, evidence_y + 11, 7, "Evidence to collect:", "0.02 0.20 0.20", font_name="F2"))
        draw_wrapped_text(canvas_x + 94, evidence_y + 11, 6, ", ".join(evidence), 380, "0.20 0.28 0.38", max_lines=1)
    return commands


def _pdf_wrap_text(value: str, font_size: int, max_width: int) -> list[str]:
    value = _pdf_clean_text(value)
    if not value:
        return []
    max_chars = max(6, int(max_width / (font_size * 0.52)))
    words = value.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        while len(current) > max_chars:
            lines.append(current[:max_chars])
            current = current[max_chars:]
    if current:
        lines.append(current)
    return lines


def _pdf_clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(item) for item in value)
    text = str(value)
    replacements = {
        "\u2022": "-",
        "\u2023": "-",
        "\u2192": "->",
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\xa0": " ",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return re.sub(r"\s+", " ", text).strip()


def _pdf_escape(value: str) -> str:
    return _pdf_clean_text(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_filename(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{slug or 'training-program'}.pdf"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _split_training_items(value: str) -> list[str]:
    return [item.strip() for item in (value or "").replace(" and ", ", ").split(",") if item.strip()]


def _sort_options() -> list[dict[str, str]]:
    return [
        {"value": "fit", "label": "Best fit"},
        {"value": "sponsor", "label": "Likely sponsor"},
        {"value": "approvals", "label": "Most approvals"},
        {"value": "approval_rate", "label": "Approval rate"},
        {"value": "latest", "label": "Latest activity"},
        {"value": "new", "label": "New employment"},
        {"value": "transfer", "label": "Transfer"},
        {"value": "continue", "label": "Continuation"},
        {"value": "name", "label": "Employer name"},
    ]


def _pursuit_tabs() -> list[dict[str, str]]:
    return [
        {"key": "profile", "label": "Profile"},
        {"key": "intelligence", "label": "Decision"},
        {"key": "workflow", "label": "Ownership"},
        {"key": "job-postings", "label": "Job Evidence"},
        {"key": "tech-stack", "label": "Tech Stack"},
        {"key": "use-cases", "label": "Use Cases"},
        {"key": "requirements", "label": "Requirements"},
        {"key": "submissions", "label": "Submission"},
        {"key": "contacts", "label": "Contacts"},
        {"key": "vendors", "label": "Vendors"},
        {"key": "managers", "label": "C2C"},
        {"key": "prompt", "label": "OpenAI"},
        {"key": "notes", "label": "Notes"},
    ]


def _pursuit_status_options() -> list[dict[str, str]]:
    return [
        {"value": PursuitStatus.ANALYSIS.value, "label": "Analysis"},
        {"value": PursuitStatus.PROMOTED.value, "label": "Promoted"},
        {"value": PursuitStatus.ASSIGNED.value, "label": "Assigned"},
        {"value": PursuitStatus.IN_PROGRESS.value, "label": "In progress"},
        {"value": PursuitStatus.PAUSED.value, "label": "Paused"},
        {"value": PursuitStatus.CLOSED.value, "label": "Closed"},
    ]


def _company_uscis_context(db: Session, company: Company) -> dict[str, Any]:
    yearly = db.execute(
        select(
            UscisEmployerYearlyStat.fiscal_year,
            func.sum(UscisEmployerYearlyStat.total_approvals).label("approvals"),
            func.sum(UscisEmployerYearlyStat.total_denials).label("denials"),
            func.sum(UscisEmployerYearlyStat.new_employment_approval).label("new_employment"),
            func.sum(UscisEmployerYearlyStat.change_employer_approval).label("change_employer"),
            func.sum(UscisEmployerYearlyStat.continuation_approval).label("continuation"),
            func.sum(UscisEmployerYearlyStat.amended_approval).label("amended"),
        )
        .where(UscisEmployerYearlyStat.company_id == company.id)
        .group_by(UscisEmployerYearlyStat.fiscal_year)
        .order_by(UscisEmployerYearlyStat.fiscal_year.desc())
    ).mappings().all()
    locations = db.execute(
        select(
            UscisEmployerYearlyStat.petitioner_state,
            UscisEmployerYearlyStat.petitioner_city,
            func.sum(UscisEmployerYearlyStat.total_approvals).label("approvals"),
            func.sum(UscisEmployerYearlyStat.total_denials).label("denials"),
        )
        .where(UscisEmployerYearlyStat.company_id == company.id)
        .group_by(UscisEmployerYearlyStat.petitioner_state, UscisEmployerYearlyStat.petitioner_city)
        .order_by(func.sum(UscisEmployerYearlyStat.total_approvals).desc())
        .limit(25)
    ).mappings().all()
    totals = {
        "approvals": sum(int(row["approvals"] or 0) for row in yearly),
        "denials": sum(int(row["denials"] or 0) for row in yearly),
        "new_employment": sum(int(row["new_employment"] or 0) for row in yearly),
        "change_employer": sum(int(row["change_employer"] or 0) for row in yearly),
        "continuation": sum(int(row["continuation"] or 0) for row in yearly),
        "amended": sum(int(row["amended"] or 0) for row in yearly),
    }
    total_decisions = totals["approvals"] + totals["denials"]
    totals["approval_rate"] = round(totals["approvals"] / total_decisions * 100, 1) if total_decisions else 0
    return {
        "yearly": yearly,
        "locations": locations,
        "totals": totals,
        "region_signal": region_signal_for_company(db, company.id),
    }
