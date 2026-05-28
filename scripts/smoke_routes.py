from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name == "scripts" else Path("/app")))

from app.db.session import SessionLocal
from app.main import app
from app.models.company import Company, CompanyPursuit, Region
from app.models.consultant import ConsultantProfile
from app.models.job import JobOpportunity
from app.models.pursuit_intelligence import MarketingRole
from app.models.training import TrainingProgram
from app.models.user import User
from app.web.auth import require_admin, require_manager, require_user


def main() -> None:
    admin = SimpleNamespace(id=1, email="audit@local", name="Audit Admin", role="admin", active=True, timezone="America/Chicago", username="audit", password_hash="")
    app.dependency_overrides[require_user] = lambda: admin
    app.dependency_overrides[require_manager] = lambda: admin
    app.dependency_overrides[require_admin] = lambda: admin
    client = TestClient(app, base_url="http://localhost")
    urls = [
        "/dashboard",
        "/landings",
        "/landings/admin",
        "/landings/consultant",
        "/account",
        "/uscis/analysis",
        "/uscis/import",
        "/reports",
        "/reports/companies-by-region",
        "/companies/tools",
        "/pursuits",
        "/consultants",
        "/jobs",
        "/marketing-roles",
        "/marketing-roles/glossary",
        "/training-programs",
        "/staff",
        "/staff/access",
        "/staff/new",
        "/staff-assignments",
        "/regions",
        "/docs",
    ]
    with SessionLocal() as db:
        ids = {
            "company": db.scalar(select(Company.id).order_by(Company.id)),
            "pursuit": db.scalar(select(CompanyPursuit.id).order_by(CompanyPursuit.id)),
            "consultant": db.scalar(select(ConsultantProfile.id).order_by(ConsultantProfile.id)),
            "job": db.scalar(select(JobOpportunity.id).order_by(JobOpportunity.id)),
            "role": db.scalar(select(MarketingRole.id).order_by(MarketingRole.id)),
            "program": db.scalar(select(TrainingProgram.id).order_by(TrainingProgram.id)),
            "staff": db.scalar(select(User.id).order_by(User.id)),
            "region": db.scalar(select(Region.id).order_by(Region.id)),
            "region_code": db.scalar(select(Region.code).order_by(Region.id)),
        }
    if ids["company"]:
        urls += [f"/companies/{ids['company']}/uscis", f"/companies/{ids['company']}/aliases"]
    if ids["pursuit"]:
        urls += [f"/pursuits/{ids['pursuit']}", f"/pursuits/{ids['pursuit']}?tab=prompt", f"/pursuits/{ids['pursuit']}?tab=notes"]
    if ids["consultant"]:
        urls += [f"/consultants/{ids['consultant']}", f"/consultants/{ids['consultant']}/edit"]
    if ids["job"]:
        urls += [f"/jobs/{ids['job']}", f"/jobs/{ids['job']}/edit"]
    if ids["role"]:
        urls += [f"/marketing-roles/{ids['role']}", f"/marketing-roles/{ids['role']}/edit", f"/landings/roles/{ids['role']}"]
    if ids["region"]:
        urls += [f"/landings/regions/{ids['region']}"]
    if ids["program"]:
        urls += [f"/training-programs/{ids['program']}", f"/training-programs/{ids['program']}/edit"]
    if ids["staff"]:
        urls += [f"/staff/{ids['staff']}", f"/staff/{ids['staff']}/edit", f"/staff-assignments/{ids['staff']}", f"/staff-assignments/{ids['staff']}/edit"]
    if ids["region_code"]:
        urls += [f"/reports/companies-by-region/{ids['region_code']}"]
    failures = []
    for url in urls:
        try:
            response = client.get(url, follow_redirects=False)
            print(f"{response.status_code} {url}", flush=True)
            if response.status_code >= 400:
                failures.append((url, response.status_code, response.text[:200]))
        except Exception as exc:
            print(f"EXC {url} {exc!r}", flush=True)
            failures.append((url, "EXC", repr(exc)))
    print(f"failures={len(failures)}", flush=True)
    for failure in failures:
        print(failure, flush=True)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
